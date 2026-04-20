from fastapi import APIRouter

from services.api.app.schemas.common import SessionId
from services.api.app.schemas.session import CreateSessionRequest, CreateSessionResponse, GetSessionResponse
from services.api.app.services.session_service import get_session_service


router = APIRouter(tags=["sessions"])


@router.post("/sessions", operation_id="createSession", response_model=CreateSessionResponse, status_code=201)
def create_session(payload: CreateSessionRequest) -> CreateSessionResponse:
    return get_session_service().create_session(payload)


@router.get("/sessions/{session_id}", operation_id="getSession", response_model=GetSessionResponse)
def get_session(session_id: SessionId) -> GetSessionResponse:
    return get_session_service().get_session(session_id)
