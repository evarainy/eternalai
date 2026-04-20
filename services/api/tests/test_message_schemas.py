from pydantic import ValidationError

from services.api.app.schemas.message import SubmitMessageRequest, SubmitMessageResponse
from services.api.tests.contract_helpers import get_contract_operation_block, get_resource_block


def test_submit_message_request_accepts_text_payloads() -> None:
    request = SubmitMessageRequest(input_type="text", text="hello")
    assert request.input_type == "text"
    assert request.audio_ref is None


def test_submit_message_request_requires_matching_payload_shape() -> None:
    try:
        SubmitMessageRequest(input_type="audio", text="hello")
    except ValidationError as exc:
        assert "audio_ref is required" in str(exc)
    else:
        raise AssertionError("SubmitMessageRequest accepted an audio payload without audio_ref")


def test_submit_message_response_defaults_to_queued() -> None:
    response = SubmitMessageResponse(task_id="task_123", accepted_turn_id="turn_123")
    assert response.status == "queued"


def test_message_contract_and_resource_spec_reference_schema_models() -> None:
    message_block = get_contract_operation_block("submitMessage")
    turn_block = get_resource_block("conversation_turn")

    assert "schema_ref: services.api.app.schemas.message.SubmitMessageRequest" in message_block
    assert "schema_ref: services.api.app.schemas.message.SubmitMessageResponse" in message_block
    assert "schema_ref: services.api.app.schemas.common.BadRequestErrorResponse" in message_block
    assert "services.api.app.schemas.session.ConversationTurn" in turn_block
