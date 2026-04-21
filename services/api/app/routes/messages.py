from fastapi import APIRouter

from services.api.app.schemas.common import SessionId
from services.api.app.schemas.message import SubmitMessageRequest, SubmitMessageResponse
from services.api.app.services.session_service import get_session_service


router = APIRouter(tags=["messages"])


@router.post(
    "/sessions/{session_id}/messages",
    operation_id="submitMessage",
    response_model=SubmitMessageResponse,
    status_code=202,
)
def submit_message(session_id: SessionId, payload: SubmitMessageRequest) -> SubmitMessageResponse:
    return get_session_service().submit_message(session_id, payload)
