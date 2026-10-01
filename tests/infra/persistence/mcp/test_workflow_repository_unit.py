"""Exercise production checkpoint serialization without a database or migration."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.infra.persistence.mcp.workflow_repository import PostgreSQLWorkflowStore
from app.mcp.models import McpFailure
from tests.mcp.test_operations import fixture


class Session:
    def __init__(self, operation_id):
        self.operation_id = operation_id
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, statement):
        self.statements.append(statement.compile().params)
        return SimpleNamespace(scalar_one_or_none=lambda: self.operation_id)


def test_real_transition_keeps_public_output_separate_and_checkpoint_expiry_exact():
    async def run():
        h = await fixture()
        session = Session(h.op.operation_id)
        store = SimpleNamespace(
            sessions=SimpleNamespace(begin=lambda: session),
            encrypt=lambda payload, aad: json.dumps(payload).encode(),
            decrypt=lambda payload, aad: json.loads(payload),
        )
        repository = PostgreSQLWorkflowStore(store)
        op = h.op.model_copy(update={"state": "SENDING", "send_started": True})
        visible = {"artifactId": "a" * 32, "payloadHash": "b" * 64}
        completed = await repository.transition(
            op,
            state="VERIFIED_SUCCESS",
            safe_output={**visible, "internal": 7},
            public_result=visible,
        )
        assert completed.public_result == visible and completed.safe_output["internal"] == 7
        sql_values = session.statements[0]
        serialized = json.loads(sql_values["encrypted_payload"])
        assert serialized["public_result"] == visible
        row = {
            **repository._aad(completed),
            "encrypted_payload": sql_values["encrypted_payload"],
            "state": completed.state,
            "revision": completed.revision,
            "send_started": completed.send_started,
            "attempt_id": completed.attempt_id,
            "expires_at": sql_values["expires_at"],
            "checkpoint": session.statements[1]["checkpoint"],
        }
        assert repository._decode(row) == completed
        with pytest.raises(McpFailure, match="mcp_checkpoint_inconsistent"):
            repository._decode({**row, "expires_at": completed.expires_at + timedelta(seconds=1)})
        failed = await repository.transition(
            op, state="FAILED", safe_output={"internal": 7}, public_result=visible
        )
        assert failed.public_result is None
        deadline = h.op.expires_at - timedelta(seconds=2)
        renewed = await repository.transition(
            h.op,
            state="WAITING_LOCAL_CONFIRM",
            renewed_context=h.op.context,
            renewed_action_digest="c" * 64,
            renewed_gate_expires_at=deadline,
            gate_request_id="d" * 32,
        )
        assert renewed.expires_at == deadline and renewed.gate_request_id == "d" * 32
        assert session.statements[-2]["expires_at"] == deadline
        assert session.statements[-1]["checkpoint"]["gate_request_id"] == "d" * 32

    asyncio.run(run())
