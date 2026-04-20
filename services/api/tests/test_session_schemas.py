from pydantic import ValidationError

from services.api.app.schemas.session import CreateSessionRequest, GetSessionResponse
from services.api.tests.contract_helpers import get_contract_operation_block, get_resource_block


def test_create_session_request_has_typed_channel_boundary() -> None:
    request = CreateSessionRequest(channel="web", metadata={"source": "smoke"})
    assert request.channel == "web"
    assert CreateSessionRequest.model_json_schema()["properties"]["channel"]["enum"] == ["web", "miniapp", "api"]


def test_create_session_request_rejects_unknown_fields() -> None:
    try:
        CreateSessionRequest(channel="web", unexpected="value")
    except ValidationError as exc:
        assert "unexpected" in str(exc)
    else:
        raise AssertionError("CreateSessionRequest accepted an unknown field")


def test_get_session_response_includes_typed_turns() -> None:
    response = GetSessionResponse(
        session_id="sess_123",
        channel="web",
        status="active",
        created_at="2026-04-20T12:00:00Z",
        updated_at="2026-04-20T12:05:00Z",
        turns=[
            {
                "turn_id": "turn_123",
                "session_id": "sess_123",
                "user_input_type": "text",
                "user_input_text": "What changed?",
                "task_id": "task_123",
                "status": "completed",
                "created_at": "2026-04-20T12:01:00Z",
            }
        ],
    )
    assert response.turns[0].status == "completed"


def test_session_contract_and_resource_spec_reference_schema_models() -> None:
    create_block = get_contract_operation_block("createSession")
    detail_block = get_contract_operation_block("getSession")
    resource_block = get_resource_block("session")

    assert "schema_ref: services.api.app.schemas.session.CreateSessionRequest" in create_block
    assert "schema_ref: services.api.app.schemas.session.CreateSessionResponse" in create_block
    assert "schema_ref: services.api.app.schemas.session.GetSessionResponse" in detail_block
    assert "services.api.app.schemas.session.CreateSessionRequest" in resource_block
    assert "services.api.app.schemas.session.GetSessionResponse" in resource_block
