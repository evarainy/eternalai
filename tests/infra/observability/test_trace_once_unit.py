"""SQL/sanitizer wiring only; this does not prove PostgreSQL concurrency."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.infra.observability.postgresql_trace import PostgreSQLTraceWriter, TraceSanitizationError
from app.ports.trace import TraceEvent


class Session:
    def __init__(self):
        self.rows = {}
        self.sql = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, statement, parameters):
        sql = str(statement)
        self.sql.append(sql)
        key = parameters["event_id"]
        if sql.startswith("INSERT"):
            assert "ON CONFLICT (event_id) DO NOTHING" in sql
            row = dict(parameters)
            row["attributes"] = json.loads(row["attributes"])
            self.rows.setdefault(key, row)
            return None
        assert sql.startswith("SELECT")
        row = {k: v for k, v in self.rows[key].items() if k not in {"event_id", "created_at"}}
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one=lambda: row))

    async def commit(self):
        self.commits += 1


def test_idempotent_trace_uses_insert_only_and_compares_sanitized_payload():
    async def run():
        session = Session()
        writer = PostgreSQLTraceWriter(lambda: session)
        event = TraceEvent(
            trace_id="synthetic-trace",
            task_id="synthetic-task",
            session_id="synthetic-chat",
            tenant_id="synthetic-tenant",
            ai_user_id="synthetic-user",
            event_type="task_completed",
            status="ok",
            attributes={"safe": "first"},
        )
        await writer.record_event_once(event, "synthetic-key")
        first = dict(session.rows["synthetic-key"])
        await writer.record_event_once(event, "synthetic-key")
        assert session.rows["synthetic-key"] == first
        assert session.commits == 2 and len(session.rows) == 1
        with pytest.raises(TraceSanitizationError, match="idempotency conflict"):
            await writer.record_event_once(
                event.model_copy(update={"ai_user_id": "other"}), "synthetic-key"
            )
        assert session.commits == 2
        with pytest.raises(TraceSanitizationError, match="empty"):
            await writer.record_event_once(event, "")
        assert len(session.sql) == 6
        # The custom sanitizer may further narrow data; persisted comparison must use it.
        writer.set_sanitizer(lambda value: {"safe": value["safe"]})
        await writer.record_event_once(
            event.model_copy(update={"attributes": {"safe": "first", "unapproved": "x"}}),
            "synthetic-key",
        )
        assert session.rows["synthetic-key"] == first and session.commits == 3

    asyncio.run(run())
