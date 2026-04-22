from dataclasses import dataclass, field
from datetime import datetime

from services.api.app.repositories.base import SessionTaskRepository
from services.api.app.schemas.session import ConversationTurn, GetSessionResponse
from services.api.app.schemas.task import GetTaskResponse


@dataclass
class _State:
    sessions: dict[str, GetSessionResponse] = field(default_factory=dict)
    tasks: dict[str, GetTaskResponse] = field(default_factory=dict)


class InMemorySessionTaskRepository(SessionTaskRepository):
    def __init__(self) -> None:
        self._state = _State()

    def create_session(self, session: GetSessionResponse) -> None:
        self._state.sessions[session.session_id] = session

    def get_session(self, session_id: str) -> GetSessionResponse | None:
        return self._state.sessions.get(session_id)

    def submit_message(
        self,
        *,
        session_id: str,
        turn: ConversationTurn,
        task: GetTaskResponse,
        session_updated_at: datetime,
    ) -> bool:
        session = self._state.sessions.get(session_id)
        if session is None:
            return False

        session.turns.append(turn)
        session.updated_at = session_updated_at
        self._state.tasks[task.task_id] = task
        return True

    def get_task(self, task_id: str) -> GetTaskResponse | None:
        return self._state.tasks.get(task_id)

    def close(self) -> None:
        return None
