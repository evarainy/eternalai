from fastapi import APIRouter, Depends

from services.api.app.schemas.common import TaskId
from services.api.app.schemas.task import GetTaskResponse
from services.api.app.services.session_service import SessionService, get_session_service


router = APIRouter(tags=["tasks"])


@router.get("/tasks/{task_id}", operation_id="getTask", response_model=GetTaskResponse)
def get_task(
    task_id: TaskId,
    service: SessionService = Depends(get_session_service),
) -> GetTaskResponse:
    return service.get_task(task_id)
