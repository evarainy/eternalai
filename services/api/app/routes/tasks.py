from fastapi import APIRouter

from services.api.app.schemas.common import TaskId
from services.api.app.schemas.task import GetTaskResponse
from services.api.app.services.session_service import get_session_service


router = APIRouter(tags=["tasks"])


@router.get("/tasks/{task_id}", operation_id="getTask", response_model=GetTaskResponse)
def get_task(task_id: TaskId) -> GetTaskResponse:
    return get_session_service().get_task(task_id)
