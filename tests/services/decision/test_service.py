from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest

from services.decision.protocol import (
    ChoiceRequest,
    EngineInput,
    EngineResult,
    ProbabilityEntry,
    Usage,
)
from services.decision.service import DecisionService, ServingFailure, ServingLimits
from tests.services.decision.test_manifest import artifact_bundle, projection_policy, seal_manifest
from tests.services.decision.test_protocol import wire


class FakeEngine:
    """Synthetic test double, never a checkpoint, measured model or Arbiter engine."""

    execution_kind: Literal["synthetic", "model"] = "synthetic"
    loaded_manifest_digest = "synthetic-fixture"

    def __init__(self) -> None:
        self.inputs: list[EngineInput] = []
        self.started = asyncio.Event()

    async def infer(self, inputs: EngineInput, cancellation: asyncio.Event) -> EngineResult:
        self.inputs.append(inputs)
        self.started.set()
        return EngineResult(
            outcome="selected",
            selected_id=inputs.options[0].id,
            distribution=tuple(
                ProbabilityEntry(id=option.id, p=0.95 if index == 0 else 0.05)
                for index, option in enumerate(inputs.options)
            ),
            certainty=0.95,
            usage=Usage(input_tokens=8, output_tokens=0),
        )


class StubbornEngine(FakeEngine):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self.saw_cancel = asyncio.Event()

    async def infer(self, inputs: EngineInput, cancellation: asyncio.Event) -> EngineResult:
        self.started.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.saw_cancel.set()
        return await super().infer(inputs, cancellation)


def service(
    engine: FakeEngine,
    *,
    timeout: float = 1.0,
    authorize: Callable[[ChoiceRequest], bool] | None = None,
) -> DecisionService:
    return DecisionService.synthetic(
        engine=engine,
        policy=projection_policy(),
        authorize=authorize if authorize is not None else lambda _: True,
        limits=ServingLimits(timeout_seconds=timeout),
    )


async def call(
    server: DecisionService,
    raw: bytes | None = None,
    cancelled: asyncio.Event | None = None,
    deadline: float | None = None,
) -> bytes:
    return await server.select(
        raw if raw is not None else wire(),
        cancellation=cancelled if cancelled is not None else asyncio.Event(),
        deadline_monotonic=deadline if deadline is not None else time.monotonic() + 10,
    )


def test_success_and_close_without_heavy_imports() -> None:
    async def run() -> None:
        engine = FakeEngine()
        server = service(engine)
        result = json.loads(await call(server))
        assert result["selected_id"] == "target_a"
        assert len(engine.inputs) == 1
        assert set(engine.inputs[0].model_dump()) == {"task_kind", "criteria", "options"}
        assert await server.close()
        with pytest.raises(ServingFailure, match="unavailable"):
            await call(server)

    asyncio.run(run())


@pytest.mark.parametrize(
    "reason", ["denied", "unapproved", "deployment", "expired", "cancelled", "oversize"]
)
def test_rejection_before_inference(reason: str) -> None:
    async def run() -> None:
        engine, cancellation = FakeEngine(), asyncio.Event()
        server = service(engine, authorize=lambda _: reason != "denied")
        data = json.loads(wire())
        expected = {
            "denied": "unauthorized",
            "unapproved": "input_unsupported",
            "deployment": "model_mismatch",
            "expired": "timeout",
            "cancelled": "cancelled",
            "oversize": "input_unsupported",
        }[reason]
        if reason == "unapproved":
            data["options"][0]["label"] = "unapproved synthetic label"
        if reason == "deployment":
            data["deployment"] = "not-this-engine"
        if reason == "cancelled":
            cancellation.set()
        raw = b"x" * 65_537 if reason == "oversize" else json.dumps(data).encode()
        deadline = time.monotonic() - 1 if reason == "expired" else None
        with pytest.raises(ServingFailure, match=f"^{expected}$"):
            await call(server, raw, cancellation, deadline)
        assert engine.inputs == []
        assert server.active_count == 0

    asyncio.run(run())


@pytest.mark.parametrize("cause", ["timeout", "cancelled"])
def test_stubborn_cancelled_engine_keeps_capacity_until_actual_completion(cause: str) -> None:
    async def run() -> None:
        engine, cancellation = StubbornEngine(), asyncio.Event()
        server = service(engine, timeout=0.02 if cause == "timeout" else 1.0)
        operation = asyncio.create_task(call(server, cancelled=cancellation))
        await asyncio.wait_for(engine.started.wait(), 0.5)
        if cause == "cancelled":
            cancellation.set()
        with pytest.raises(ServingFailure, match=f"^{cause}$"):
            await asyncio.wait_for(operation, 0.5)
        await asyncio.wait_for(engine.saw_cancel.wait(), 0.5)
        assert server.active_count == 1
        with pytest.raises(ServingFailure, match="overloaded"):
            await call(server)
        assert await server.close(timeout_seconds=0.01) is False
        assert server.active_count == 1
        engine.release.set()
        assert await server.close(timeout_seconds=0.5) is True
        assert server.active_count == 0

    asyncio.run(run())


def test_cancellation_wins_when_result_is_ready_at_same_time() -> None:
    async def run() -> None:
        cancellation = asyncio.Event()

        class CancelAtReturn(FakeEngine):
            async def infer(self, inputs: EngineInput, signal: asyncio.Event) -> EngineResult:
                result = await super().infer(inputs, signal)
                cancellation.set()
                return result

        server = service(CancelAtReturn())
        with pytest.raises(ServingFailure, match="cancelled"):
            await call(server, cancelled=cancellation)
        assert await server.close()

    asyncio.run(run())


def test_deadline_upper_bound_overrides_far_future_client_deadline() -> None:
    async def run() -> None:
        engine = StubbornEngine()
        server = service(engine, timeout=0.02)
        with pytest.raises(ServingFailure, match="timeout"):
            await asyncio.wait_for(call(server, deadline=time.monotonic() + 3600), 0.5)
        assert server.active_count == 1
        engine.release.set()
        assert await server.close()

    asyncio.run(run())


def test_current_authorization_and_loaded_engine_identity_rechecked_after_inference() -> None:
    async def run() -> None:
        allowed = True

        class RevokeAtReturn(FakeEngine):
            async def infer(self, inputs: EngineInput, cancellation: asyncio.Event) -> EngineResult:
                nonlocal allowed
                result = await super().infer(inputs, cancellation)
                allowed = False
                return result

        server = service(RevokeAtReturn(), authorize=lambda _: allowed)
        with pytest.raises(ServingFailure, match="unauthorized"):
            await call(server)

        class SwapAtReturn(FakeEngine):
            async def infer(self, inputs: EngineInput, cancellation: asyncio.Event) -> EngineResult:
                result = await super().infer(inputs, cancellation)
                self.loaded_manifest_digest = "different"
                return result

        server = service(SwapAtReturn())
        with pytest.raises(ServingFailure, match="model_mismatch"):
            await call(server)

    asyncio.run(run())


def test_engine_exception_never_appears_in_error() -> None:
    async def run() -> None:
        class Broken(FakeEngine):
            async def infer(self, inputs: EngineInput, cancellation: asyncio.Event) -> EngineResult:
                raise RuntimeError("synthetic_backend_detail")

        with pytest.raises(ServingFailure, match="^unavailable$") as error:
            await call(service(Broken()))
        assert "synthetic_backend_detail" not in str(error.value)

    asyncio.run(run())


def test_independent_concurrent_calls_do_not_mix_candidate_ids() -> None:
    async def run() -> None:
        engine = FakeEngine()
        server = DecisionService.synthetic(
            engine=engine,
            policy=projection_policy(),
            authorize=lambda _: True,
            limits=ServingLimits(maximum_active=2),
        )
        other = json.loads(wire())
        other["request_id"] = "second_request"
        other["options"][0]["id"] = "second_a"
        other["options"][1]["id"] = "second_b"
        first, second = await asyncio.gather(call(server), call(server, json.dumps(other).encode()))
        assert json.loads(first)["selected_id"] == "target_a"
        assert json.loads(second)["selected_id"] == "second_a"
        assert json.loads(first)["request_id"] != json.loads(second)["request_id"]
        assert await server.close()

    asyncio.run(run())


def test_checkpoint_startup_requires_actual_artifacts_and_loaded_digest(tmp_path: Path) -> None:
    manifest = artifact_bundle(tmp_path)
    engine = FakeEngine()
    engine.execution_kind = "model"  # Constructor test double only; no inference is run.
    engine.loaded_manifest_digest = manifest.digest
    kwargs = dict(
        engine=engine,
        manifest=manifest,
        artifact_root=tmp_path,
        policy=projection_policy(),
        authorize=lambda _: True,
        limits=ServingLimits(),
    )
    server = DecisionService.checkpoint(**kwargs)
    assert server.deployment == manifest.deployment
    assert engine.inputs == []
    engine.loaded_manifest_digest = "wrong"
    with pytest.raises(ServingFailure, match="model_mismatch"):
        DecisionService.checkpoint(**kwargs)
    engine.loaded_manifest_digest = manifest.digest
    (tmp_path / manifest.artifacts[0].path).write_text(
        "modified synthetic artifact", encoding="utf-8"
    )
    with pytest.raises(ServingFailure, match="unavailable"):
        DecisionService.checkpoint(**kwargs)


def test_missing_environment_and_challenger_do_not_become_verified_engine(tmp_path: Path) -> None:
    manifest = artifact_bundle(tmp_path)
    engine = FakeEngine()
    with pytest.raises(ServingFailure, match="WAITING_ENV"):
        DecisionService.checkpoint(
            engine=engine,
            manifest=manifest,
            artifact_root=tmp_path / "missing",
            policy=projection_policy(),
            authorize=lambda _: True,
            limits=ServingLimits(),
        )
    payload = manifest.model_dump(mode="json")
    payload["repository"] = "ichenney/laya-browser-v32b"
    challenger = seal_manifest(payload)
    engine.execution_kind = "model"
    engine.loaded_manifest_digest = challenger.digest
    with pytest.raises(ServingFailure, match="WAITING_ENV"):
        DecisionService.checkpoint(
            engine=engine,
            manifest=challenger,
            artifact_root=tmp_path,
            policy=projection_policy(),
            authorize=lambda _: True,
            limits=ServingLimits(),
        )


def test_direct_constructor_cannot_bypass_verification_or_default_authorization() -> None:
    engine = FakeEngine()
    engine.execution_kind = "model"
    with pytest.raises(ValueError, match="unverified_engine"):
        DecisionService(
            engine=engine,
            deployment="model",
            policy=projection_policy(),
            authorize=lambda _: True,
            limits=ServingLimits(),
        )
    with pytest.raises(ValueError, match="authorizer_required"):
        DecisionService.synthetic(
            engine=FakeEngine(), policy=projection_policy(), authorize=None, limits=ServingLimits()
        )  # type: ignore[arg-type]
