from datetime import UTC, datetime
from uuid import uuid4

from services.api.app.persistence.db import build_engine
from services.api.app.repositories.postgres import PostgresSessionTaskRepository
from services.api.app.schemas.session import ConversationTurn, GetSessionResponse
from services.api.app.schemas.task import GetTaskResponse


def test_postgres_repository_round_trip(migrated_postgres_database_url: str) -> None:
    now = datetime.now(UTC)
    session_id = f"sess_{uuid4().hex}"
    turn_id = f"turn_{uuid4().hex}"
    task_id = f"task_{uuid4().hex}"
    repository = PostgresSessionTaskRepository(build_engine(migrated_postgres_database_url))

    try:
        session = GetSessionResponse(
            session_id=session_id,
            channel="web",
            status="active",
            created_at=now,
            updated_at=now,
            turns=[],
        )
        repository.create_session(session)

        task = GetTaskResponse(
            task_id=task_id,
            task_type="answer",
            status="queued",
            created_at=now,
            updated_at=now,
            session_id=session_id,
        )
        turn = ConversationTurn(
            turn_id=turn_id,
            session_id=session_id,
            user_input_type="text",
            user_input_text="repository round trip",
            task_id=task_id,
            status="accepted",
            created_at=now,
        )

        assert repository.submit_message(
            session_id=session_id,
            turn=turn,
            task=task,
            session_updated_at=now,
        ) is True

        stored_session = repository.get_session(session_id)
        stored_task = repository.get_task(task_id)

        assert stored_session is not None
        assert stored_session.turns[0].turn_id == turn_id
        assert stored_task is not None
        assert stored_task.status == "queued"
    finally:
        repository.close()
