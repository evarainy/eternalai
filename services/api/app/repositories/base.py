from datetime import datetime
from typing import Protocol

from services.api.app.schemas.session import ConversationTurn, GetSessionResponse
from services.api.app.schemas.task import GetTaskResponse


class SessionTaskRepository(Protocol):
    def create_session(self, session: GetSessionResponse) -> None: ...

    def get_session(self, session_id: str) -> GetSessionResponse | None: ...

    def submit_message(
        self,
        *,
        session_id: str,
        turn: ConversationTurn,
        task: GetTaskResponse,
        session_updated_at: datetime,
    ) -> bool: ...

    def get_task(self, task_id: str) -> GetTaskResponse | None: ...

    def close(self) -> None: ...
