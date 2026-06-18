"""RuntimePort interface contract tests."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

from app.contracts.sdui.models import UIComponent
from app.ports.response_envelope import ResponseEnvelope
from app.ports.runtime import RuntimePort

RUNTIME_SOURCE = Path("app/ports/runtime.py")


def _session_id_field() -> str:
    return next(field for field in ResponseEnvelope.model_fields if field == "session_id")


class _MockRuntime:
    async def handle_user_message(
        self,
        channel: str,
        ai_user_id: str,
        session_id: str,
        message: str,
        client_capabilities: dict[str, Any],
    ) -> ResponseEnvelope:
        response_payload: dict[str, Any] = {
            "response_id": "resp-001",
            "task_id": "task-001",
            "status": "completed",
            "message": "ok",
            "fallback_text": "ok",
            "ui": UIComponent(component_type="none"),
            "trace_id": "trace-001",
        }
        response_payload[_session_id_field()] = session_id
        return ResponseEnvelope.model_validate(response_payload)


def test_runtime_port_protocol_defines_only_handle_user_message() -> None:
    assert "handle_user_message" in RuntimePort.__protocol_attrs__
    assert len(RuntimePort.__protocol_attrs__) == 1


def test_handle_user_message_signature_matches_spec_8_6_8() -> None:
    signature = inspect.signature(RuntimePort.handle_user_message)

    assert list(signature.parameters) == [
        "self",
        "channel",
        "ai_user_id",
        "session_id",
        "message",
        "client_capabilities",
    ]
    assert inspect.iscoroutinefunction(RuntimePort.handle_user_message)


def test_return_annotation_uses_response_envelope_reexport() -> None:
    hints = get_type_hints(RuntimePort.handle_user_message)

    assert hints["return"] is ResponseEnvelope


def test_channel_literal_values_match_spec_8_6_8() -> None:
    hints = get_type_hints(RuntimePort.handle_user_message)
    channel_hint = hints["channel"]

    assert get_args(channel_hint) == ("web", "cli", "api", "mock")


def test_client_capabilities_annotation_is_open_dict_str_any() -> None:
    hints = get_type_hints(RuntimePort.handle_user_message)
    client_capabilities_hint = hints["client_capabilities"]

    assert get_origin(client_capabilities_hint) is dict
    assert get_args(client_capabilities_hint) == (str, Any)


def test_concrete_runtime_mock_accepts_each_channel_and_returns_real_response_envelope() -> None:
    async def exercise_runtime() -> None:
        runtime = _MockRuntime()

        for channel in ("web", "cli", "api", "mock"):
            result = await runtime.handle_user_message(
                channel,
                "user-sentinel-1",
                "sess-sentinel-1",
                "msg-sentinel-1",
                {"feat": True, "level": 3},
            )

            assert isinstance(result, ResponseEnvelope)

    asyncio.run(exercise_runtime())


def test_handle_user_message_accepts_arbitrary_str_inputs() -> None:
    async def exercise_runtime() -> None:
        runtime = _MockRuntime()

        result = await runtime.handle_user_message(
            "api",
            "usr-42-arbitrary",
            "sess-99-arbitrary",
            "arbitrary message content 123",
            {"arbitrary_cap": "yes"},
        )

        assert isinstance(result, ResponseEnvelope)

    asyncio.run(exercise_runtime())


def test_runtime_source_does_not_contain_concrete_implementation_dependencies() -> None:
    runtime_source = RUNTIME_SOURCE.read_text(encoding="utf-8")

    forbidden_strings = (
        "OpenAI",
        "CapabilityGateway",
        "TaskStore",
        "TracePort(",
        "PolicyGuard",
        "IdentityMapping",
        "requests",
        "httpx",
    )

    for forbidden_string in forbidden_strings:
        assert forbidden_string not in runtime_source
