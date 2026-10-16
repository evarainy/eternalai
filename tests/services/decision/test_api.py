from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from services.decision.api import create_app
from services.decision.protocol import EngineInput, EngineResult
from tests.services.decision.test_protocol import wire
from tests.services.decision.test_service import FakeEngine, StubbornEngine, service


def test_http_factory_defaults_to_deny_and_never_starts_inference() -> None:
    async def run() -> None:
        engine = FakeEngine()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(service(engine))),
            base_url="http://synthetic.invalid",
        ) as client:
            response = await client.post(
                "/select", content=wire(), headers={"Content-Type": "application/json"}
            )
        assert response.status_code == 401
        assert response.json() == {"error": "unauthorized"}
        assert engine.inputs == []

    asyncio.run(run())


def test_http_success_exact_wire_and_explicit_synthetic_identity() -> None:
    async def run() -> None:
        engine = FakeEngine()
        app = create_app(service(engine), authorize=lambda request: request.url.path == "/select")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid"
        ) as client:
            response = await client.post(
                "/select", content=wire(), headers={"Content-Type": "application/json"}
            )
            docs = await client.get("/docs")
        assert response.status_code == 200
        assert response.headers["X-Decision-Execution-Kind"] == "synthetic"
        assert response.json()["deployment"] == "synthetic-decision-test-v1"
        assert response.json()["schema_version"] == "browser_choice.v1"
        assert docs.status_code == 404

    asyncio.run(run())


@pytest.mark.parametrize(
    "raw,content_type",
    [(b"x", "application/json"), (b"x" * 65_537, "application/json"), (b"{}", "text/plain")],
    ids=("malformed", "oversized", "wrong-content-type"),
)
def test_http_bad_input_has_fixed_error_and_no_echo(raw: bytes, content_type: str) -> None:
    async def run() -> None:
        engine = FakeEngine()
        app = create_app(service(engine), authorize=lambda _: True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid"
        ) as client:
            response = await client.post(
                "/select", content=raw, headers={"Content-Type": content_type}
            )
        assert response.status_code == 422
        assert response.json() == {"error": "input_unsupported"}
        assert engine.inputs == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "case,status,code",
    [("timeout", 504, "timeout"), ("cancelled", 499, "cancelled"), ("broken", 503, "unavailable")],
)
def test_http_failure_codes_preserved_without_backend_text(
    case: str, status: int, code: str
) -> None:
    async def run() -> None:
        class Failing(FakeEngine):
            async def infer(self, inputs: EngineInput, cancellation: asyncio.Event) -> EngineResult:
                if case == "timeout":
                    await asyncio.Event().wait()
                if case == "cancelled":
                    raise asyncio.CancelledError
                raise RuntimeError("generated_backend_detail")

        server = service(Failing(), timeout=0.02)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(server, authorize=lambda _: True)),
            base_url="http://synthetic.invalid",
        ) as client:
            response = await client.post(
                "/select", content=wire(), headers={"Content-Type": "application/json"}
            )
        assert response.status_code == status
        assert response.json() == {"error": code}
        assert "generated_backend_detail" not in response.text
        assert await server.close()

    asyncio.run(run())


def test_http_overload_is_429_and_first_request_keeps_its_own_result() -> None:
    async def run() -> None:
        engine = StubbornEngine()
        server = service(engine)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(server, authorize=lambda _: True)),
            base_url="http://synthetic.invalid",
        ) as client:
            first = asyncio.create_task(
                client.post("/select", content=wire(), headers={"Content-Type": "application/json"})
            )
            await asyncio.wait_for(engine.started.wait(), 0.5)
            second = await client.post(
                "/select", content=wire(), headers={"Content-Type": "application/json"}
            )
            assert second.status_code == 429
            assert second.json() == {"error": "overloaded"}
            engine.release.set()
            assert (await first).json()["selected_id"] == "target_a"
        assert await server.close()

    asyncio.run(run())


def test_request_cannot_select_real_backend_via_free_text_tag() -> None:
    async def run() -> None:
        engine = FakeEngine()
        data = json.loads(wire())
        data["execution_kind"] = "model"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_app(service(engine), authorize=lambda _: True)
            ),
            base_url="http://synthetic.invalid",
        ) as client:
            response = await client.post("/select", json=data)
        assert response.status_code == 422
        assert engine.inputs == []

    asyncio.run(run())


def test_pinned_local_deployment_mismatch_has_distinct_412_before_inference() -> None:
    async def run() -> None:
        engine = FakeEngine()
        data = json.loads(wire())
        data["deployment"] = "wrong-pinned-deployment"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_app(service(engine), authorize=lambda _: True)
            ),
            base_url="http://synthetic.invalid",
        ) as client:
            response = await client.post("/select", json=data)
        assert response.status_code == 412
        assert response.json() == {"error": "model_mismatch"}
        assert engine.inputs == []

    asyncio.run(run())


def test_asgi_disconnect_cancels_response_but_retains_stubborn_engine_slot() -> None:
    async def run() -> None:
        engine = StubbornEngine()
        server = service(engine)
        app = create_app(server, authorize=lambda _: True)
        disconnected = asyncio.Event()
        delivered = False
        messages: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": wire(), "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/select",
            "raw_path": b"/select",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1),
            "server": ("synthetic.invalid", 80),
        }
        operation = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(engine.started.wait(), 0.5)
        disconnected.set()
        await asyncio.wait_for(operation, 0.5)
        assert messages[0]["status"] == 499
        assert json.loads(messages[1]["body"]) == {"error": "cancelled"}
        assert server.active_count == 1
        engine.release.set()
        assert await server.close()

    asyncio.run(run())
