from __future__ import annotations

import asyncio
import copy
import json
import shutil
from pathlib import Path
from typing import Any

from app.infra.adapters.oa.adapter import OAReadAdapter
from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    build_structural_fingerprint,
)
from app.infra.adapters.oa.provider import (
    LiveOAReadProvider,
    ReplayOAReadProvider,
)
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.ports.capability_gateway import RequestOrgContext

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_PACK = (
    REPO_ROOT
    / "tests"
    / "contract_packs"
    / "oa"
    / "ecology9-pending-workflows-v1"
)
EXPECTED_REPLAY_DATA: dict[str, Any] = {
    "workflows": [
        {
            "workflow_id": "workflow-synthetic-001",
            "title": "workflow-title-synthetic-001",
            "status": "pending",
            "applicant": "applicant-synthetic-001",
            "current_step": "step-synthetic-001",
            "approver": "approver-synthetic-001",
            "created_at": "2000-01-01T00:00:00+00:00",
            "expired": False,
        },
        {
            "workflow_id": "workflow-synthetic-002",
            "title": "workflow-title-synthetic-002",
            "status": "pending",
            "applicant": "applicant-synthetic-002",
            "current_step": "step-synthetic-002",
            "approver": None,
            "created_at": None,
            "expired": True,
        },
    ]
}


class CountingReplayProvider(ReplayOAReadProvider):
    def __init__(self, contract_pack_dir: Path) -> None:
        super().__init__(contract_pack_dir)
        self.calls = 0

    async def list_pending_workflows(self) -> OAPendingWorkflowCollection:
        self.calls += 1
        return await super().list_pending_workflows()


def test_replay_adapter_returns_every_normalized_field_exactly() -> None:
    adapter = OAReadAdapter(ReplayOAReadProvider(CONTRACT_PACK))

    result = asyncio.run(adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "success"
    assert result.error_code is None
    assert result.raw_payload_ref is None
    assert result.data == EXPECTED_REPLAY_DATA


def test_replay_adapter_runs_through_gateway_with_completed_status() -> None:
    gateway = CapabilityGateway(OAReadAdapter(ReplayOAReadProvider(CONTRACT_PACK)))

    result = asyncio.run(
        gateway.execute_capability(
            "task-replay-001",
            "session-replay-001",
            "ai-user-replay-001",
            "oa.list_pending_workflows",
            {},
            RequestOrgContext(request_id="trace-replay-001"),
        )
    )

    assert result.status == "completed"
    assert result.error_code is None
    assert result.data == EXPECTED_REPLAY_DATA


def test_unknown_capability_returns_adapter_error_and_gateway_failed() -> None:
    provider = CountingReplayProvider(CONTRACT_PACK)
    adapter = OAReadAdapter(provider)
    direct_result = asyncio.run(adapter.execute("oa.unlisted_capability", {}, {}))

    gateway = CapabilityGateway(adapter)
    gateway_result = asyncio.run(
        gateway.execute_capability(
            "task-unknown-001",
            "session-unknown-001",
            "ai-user-unknown-001",
            "oa.unlisted_capability",
            {},
            RequestOrgContext(request_id="trace-unknown-001"),
        )
    )

    assert direct_result.status == "error"
    assert direct_result.error_code == "adapter_error"
    assert direct_result.data is None
    assert gateway_result.status == "failed"
    assert gateway_result.error_code == "adapter_error"
    assert gateway_result.data is None
    assert provider.calls == 0


def test_extra_capability_arguments_fail_closed_without_provider_call() -> None:
    provider = CountingReplayProvider(CONTRACT_PACK)
    adapter = OAReadAdapter(provider)

    result = asyncio.run(
        adapter.execute(
            "oa.list_pending_workflows",
            {"page_size": 100},
            {"ignored": "context"},
        )
    )

    assert result.status == "error"
    assert result.error_code == "adapter_error"
    assert result.data is None
    assert provider.calls == 0


def test_live_provider_has_explicit_error_and_never_falls_back_to_replay() -> None:
    adapter = OAReadAdapter(LiveOAReadProvider())

    result = asyncio.run(adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "error"
    assert result.error_code == "adapter_error"
    assert result.data is None


def test_replay_rejects_structural_fingerprint_mismatch(tmp_path: Path) -> None:
    pack = tmp_path / CONTRACT_PACK.name
    shutil.copytree(CONTRACT_PACK, pack)
    sample_path = pack / "sample.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["workflows"][0]["status"] = 1
    sample_path.write_text(json.dumps(sample), encoding="utf-8")
    adapter = OAReadAdapter(ReplayOAReadProvider(pack))

    result = asyncio.run(adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "error"
    assert result.error_code == "adapter_error"
    assert result.data is None


def test_replay_maps_model_violation_to_payload_invalid(tmp_path: Path) -> None:
    pack = tmp_path / CONTRACT_PACK.name
    shutil.copytree(CONTRACT_PACK, pack)
    sample_path = pack / "sample.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    sample["workflows"][0]["unexpected"] = "not-allowed"
    sample_path.write_text(json.dumps(sample), encoding="utf-8")
    fingerprint = build_structural_fingerprint(sample)
    (pack / "fingerprint.json").write_text(
        json.dumps(fingerprint),
        encoding="utf-8",
    )
    adapter = OAReadAdapter(ReplayOAReadProvider(pack))

    result = asyncio.run(adapter.execute("oa.list_pending_workflows", {}, {}))

    assert result.status == "error"
    assert result.error_code == "adapter_payload_invalid"
    assert result.data is None


def test_structural_fingerprint_excludes_values_and_array_length() -> None:
    first = {"workflows": [copy.deepcopy(EXPECTED_REPLAY_DATA["workflows"][0])]}
    second_item = copy.deepcopy(first["workflows"][0])
    second_item.update(
        {
            "workflow_id": "completely-different-workflow",
            "title": "completely-different-title",
            "status": "different-status",
            "applicant": "completely-different-applicant",
            "current_step": "completely-different-step",
            "approver": "completely-different-approver",
            "created_at": "2099-12-31T23:59:59+00:00",
            "expired": True,
        }
    )
    second = {"workflows": [second_item, copy.deepcopy(second_item)]}

    first_fingerprint = build_structural_fingerprint(first)
    second_fingerprint = build_structural_fingerprint(second)

    assert first_fingerprint == second_fingerprint
    rendered = json.dumps(first_fingerprint)
    for business_value in first["workflows"][0].values():
        assert str(business_value) not in rendered
