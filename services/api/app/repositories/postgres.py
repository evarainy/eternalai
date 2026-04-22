from datetime import datetime

from sqlalchemy import Engine, text

from services.api.app.repositories.base import SessionTaskRepository
from services.api.app.schemas.session import ConversationTurn, GetSessionResponse
from services.api.app.schemas.task import GetTaskResponse


class PostgresSessionTaskRepository(SessionTaskRepository):
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_session(self, session: GetSessionResponse) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO sessions (session_id, channel, status, title, metadata, created_at, updated_at)
                    VALUES (:session_id, :channel, :status, :title, :metadata, :created_at, :updated_at)
                    """
                ),
                session.model_dump(mode="python", exclude={"turns"}),
            )

    def get_session(self, session_id: str) -> GetSessionResponse | None:
        with self._engine.begin() as connection:
            session_row = connection.execute(
                text(
                    """
                    SELECT session_id, channel, status, title, metadata, created_at, updated_at
                    FROM sessions
                    WHERE session_id = :session_id
                    """
                ),
                {"session_id": session_id},
            ).mappings().first()
            if session_row is None:
                return None

            turn_rows = connection.execute(
                text(
                    """
                    SELECT turn_id, session_id, task_id, user_input_type, user_input_text, status, created_at
                    FROM turns
                    WHERE session_id = :session_id
                    ORDER BY created_at ASC, turn_id ASC
                    """
                ),
                {"session_id": session_id},
            ).mappings().all()

        return GetSessionResponse(
            **dict(session_row),
            turns=[
                ConversationTurn(
                    **dict(row),
                    assistant_output_text=None,
                    citations=None,
                    transcript_text=None,
                    failure_reason=None,
                )
                for row in turn_rows
            ],
        )

    def submit_message(
        self,
        *,
        session_id: str,
        turn: ConversationTurn,
        task: GetTaskResponse,
        session_updated_at: datetime,
    ) -> bool:
        with self._engine.begin() as connection:
            updated = connection.execute(
                text(
                    """
                    UPDATE sessions
                    SET updated_at = :updated_at
                    WHERE session_id = :session_id
                    """
                ),
                {"session_id": session_id, "updated_at": session_updated_at},
            )
            if updated.rowcount != 1:
                return False

            connection.execute(
                text(
                    """
                    INSERT INTO tasks (task_id, task_type, status, session_id, created_at, updated_at)
                    VALUES (:task_id, :task_type, :status, :session_id, :created_at, :updated_at)
                    """
                ),
                task.model_dump(
                    mode="python",
                    include={"task_id", "task_type", "status", "session_id", "created_at", "updated_at"},
                ),
            )
            connection.execute(
                text(
                    """
                    INSERT INTO turns (turn_id, session_id, task_id, user_input_type, user_input_text, status, created_at)
                    VALUES (:turn_id, :session_id, :task_id, :user_input_type, :user_input_text, :status, :created_at)
                    """
                ),
                turn.model_dump(
                    mode="python",
                    include={"turn_id", "session_id", "task_id", "user_input_type", "user_input_text", "status", "created_at"},
                ),
            )
            return True

    def get_task(self, task_id: str) -> GetTaskResponse | None:
        with self._engine.begin() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT task_id, task_type, status, session_id, created_at, updated_at
                    FROM tasks
                    WHERE task_id = :task_id
                    """
                ),
                {"task_id": task_id},
            ).mappings().first()
        if row is None:
            return None
        return GetTaskResponse(
            **dict(row),
            knowledge_source_id=None,
            result_summary=None,
            result=None,
            error=None,
        )

    def close(self) -> None:
        self._engine.dispose()
