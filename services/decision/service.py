"""Bounded local Decision coordination; one injected backend, no model loader fallback."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import Field

from app.browser_skill.models import Contract, Probability
from services.decision.manifest import PinnedCheckpoint
from services.decision.offline import VerifiedArtifacts, inspect_offline
from services.decision.protocol import (
    ChoiceRequest,
    ChoiceResponse,
    EngineInput,
    EngineResult,
    ProjectionPolicy,
    encode_response,
    schema_digest,
)


class ServingLimits(Contract):
    maximum_active: Annotated[int, Field(ge=1, le=256)] = 1
    maximum_request_bytes: Annotated[int, Field(ge=1, le=1_048_576)] = 65_536
    maximum_response_bytes: Annotated[int, Field(ge=1, le=1_048_576)] = 65_536
    timeout_seconds: Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)] = 10.0
    minimum_confidence: Probability = 0.9


class InferenceEngine(Protocol):
    @property
    def execution_kind(self) -> Literal["synthetic", "model"]: ...

    @property
    def loaded_manifest_digest(self) -> str: ...

    async def infer(self, inputs: EngineInput, cancellation: asyncio.Event) -> EngineResult:
        """Cooperate with cancellation; actual completion owns capacity release.

        Blocking inference must stay outside the event loop. Preserve per-call
        candidate identity when the selected serving engine batches requests.
        """
        ...


class ServingFailure(Exception):
    def __init__(
        self,
        code: Literal[
            "unauthorized",
            "input_unsupported",
            "model_mismatch",
            "overloaded",
            "timeout",
            "cancelled",
            "invalid_response",
            "unavailable",
            "WAITING_ENV",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)


class DecisionService:
    """Per-process capacity; late/stubborn inference retains its slot until done.

    Synthetic construction is explicit trusted composition and a separate fixed
    deployment identity. HTTP input cannot switch it into a real model or vice
    versa. A verified checkpoint constructor additionally requires physical
    artifacts, dependency/calibration evidence and an already loaded engine.
    """

    def __init__(
        self,
        *,
        engine: InferenceEngine,
        deployment: str,
        policy: ProjectionPolicy,
        authorize: Callable[[ChoiceRequest], bool],
        limits: ServingLimits,
        _receipt: VerifiedArtifacts | None = None,
        _manifest: PinnedCheckpoint | None = None,
    ) -> None:
        if not callable(authorize):
            raise ValueError("decision_authorizer_required")
        if _receipt is None:
            if (
                engine.execution_kind != "synthetic"
                or deployment != "synthetic-decision-test-v1"
                or _manifest is not None
            ):
                raise ValueError("decision_unverified_engine")
        else:
            if not isinstance(_receipt, VerifiedArtifacts) or _manifest is None:
                raise ValueError("decision_unverified_engine")
            checked = PinnedCheckpoint.model_validate_json(_manifest.model_dump_json())
            if checked.repository != "cklxx/laya-browser":
                raise ServingFailure("WAITING_ENV")
            if (
                not _receipt.matches(checked)
                or deployment != checked.deployment
                or engine.execution_kind != "model"
                or engine.loaded_manifest_digest != checked.digest
                or checked.input_schema_digest != schema_digest(ChoiceRequest)
                or checked.output_schema_digest != schema_digest(ChoiceResponse)
                or checked.projection_policy_digest != policy.digest
                or limits.minimum_confidence < checked.calibration.minimum_confidence
            ):
                raise ServingFailure("model_mismatch")
        self._engine = engine
        self.deployment = deployment
        self.execution_kind = engine.execution_kind
        self._engine_digest = engine.loaded_manifest_digest
        self._policy = ProjectionPolicy.model_validate_json(policy.model_dump_json())
        self._authorize = authorize
        self.limits = ServingLimits.model_validate_json(limits.model_dump_json())
        self._active: set[asyncio.Task[EngineResult]] = set()
        self._signals: dict[asyncio.Task[EngineResult], asyncio.Event] = {}
        self._closed = False

    @classmethod
    def synthetic(
        cls,
        *,
        engine: InferenceEngine,
        policy: ProjectionPolicy,
        authorize: Callable[[ChoiceRequest], bool],
        limits: ServingLimits,
    ) -> DecisionService:
        return cls(
            engine=engine,
            deployment="synthetic-decision-test-v1",
            policy=policy,
            authorize=authorize,
            limits=limits,
        )

    @classmethod
    def checkpoint(
        cls,
        *,
        engine: InferenceEngine,
        manifest: PinnedCheckpoint,
        artifact_root: Path,
        policy: ProjectionPolicy,
        authorize: Callable[[ChoiceRequest], bool],
        limits: ServingLimits,
    ) -> DecisionService:
        checked = PinnedCheckpoint.model_validate_json(manifest.model_dump_json())
        inspection = inspect_offline(checked, artifact_root)
        if inspection.status == "WAITING_ENV":
            raise ServingFailure("WAITING_ENV")
        if (
            inspection.status != "READY"
            or inspection.receipt is None
            or not inspection.receipt.matches(checked)
        ):
            raise ServingFailure("unavailable")
        return cls(
            engine=engine,
            deployment=checked.deployment,
            policy=policy,
            authorize=authorize,
            limits=limits,
            _receipt=inspection.receipt,
            _manifest=checked,
        )

    @property
    def active_count(self) -> int:
        return len(self._active)

    def _finished(self, task: asyncio.Task[EngineResult]) -> None:
        self._active.discard(task)
        self._signals.pop(task, None)
        # Consume late exceptions without emitting backend text or logging input.
        if not task.cancelled():
            task.exception()

    def _authorized(self, request: ChoiceRequest) -> None:
        try:
            permitted = self._authorize(request)
        except Exception:
            raise ServingFailure("unauthorized") from None
        if permitted is not True:
            raise ServingFailure("unauthorized")

    def _engine_pinned(self) -> None:
        try:
            actual = self._engine.execution_kind, self._engine.loaded_manifest_digest
        except Exception:
            raise ServingFailure("unavailable") from None
        if actual != (self.execution_kind, self._engine_digest):
            raise ServingFailure("model_mismatch")

    async def select(
        self, raw: bytes, *, cancellation: asyncio.Event, deadline_monotonic: float
    ) -> bytes:
        if self._closed:
            raise ServingFailure("unavailable")
        if cancellation.is_set():
            raise ServingFailure("cancelled")
        deadline = min(deadline_monotonic, time.monotonic() + self.limits.timeout_seconds)
        if not math.isfinite(deadline_monotonic) or deadline <= time.monotonic():
            raise ServingFailure("timeout")
        try:
            request = ChoiceRequest.parse(raw, self.limits.maximum_request_bytes)
        except (ValueError, TypeError, RecursionError):
            raise ServingFailure("input_unsupported") from None
        if request.deployment != self.deployment:
            raise ServingFailure("model_mismatch")
        self._engine_pinned()
        self._authorized(request)
        try:
            inputs = self._policy.project(request)
        except (ValueError, TypeError):
            raise ServingFailure("input_unsupported") from None
        if cancellation.is_set():
            raise ServingFailure("cancelled")
        if time.monotonic() >= deadline:
            raise ServingFailure("timeout")
        if len(self._active) >= self.limits.maximum_active:
            raise ServingFailure("overloaded")
        signal = asyncio.Event()
        operation = asyncio.create_task(self._engine.infer(inputs, signal))
        self._active.add(operation)
        self._signals[operation] = signal
        operation.add_done_callback(self._finished)
        cancelled = asyncio.create_task(cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {operation, cancelled},
                timeout=max(0.0, deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation.is_set():
                raise ServingFailure("cancelled")
            if operation not in done or time.monotonic() >= deadline:
                raise ServingFailure("timeout")
            try:
                result = operation.result()
            except asyncio.CancelledError:
                raise ServingFailure("cancelled") from None
            except Exception:
                raise ServingFailure("unavailable") from None
            self._engine_pinned()
            self._authorized(request)
            try:
                return encode_response(
                    request,
                    result,
                    minimum_confidence=self.limits.minimum_confidence,
                    maximum_bytes=self.limits.maximum_response_bytes,
                )
            except (ValueError, TypeError, AttributeError):
                raise ServingFailure("invalid_response") from None
        finally:
            cancelled.cancel()
            if not operation.done():
                signal.set()
                operation.cancel()
            # Do not await a backend that swallows cancellation. Its done callback
            # owns quota release, so timeout cannot admit unbounded zombie work.
            try:
                await cancelled
            except asyncio.CancelledError:
                pass

    async def close(self, timeout_seconds: float = 1.0) -> bool:
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0 or timeout_seconds > 120:
            raise ValueError("decision_close_timeout_invalid")
        self._closed = True
        tasks = tuple(self._active)
        for task in tasks:
            self._signals[task].set()
            task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
            return not pending
        return True
