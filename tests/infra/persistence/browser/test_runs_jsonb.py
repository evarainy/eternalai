"""Production JSONB readers with synthetic rows; no DB or authorization proof."""

import asyncio
import json
from dataclasses import replace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.browser_skill.models import TeachingPlan, TeachingPlanStep
from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.browser_skill.registry import canonical_digest
from app.browser_skill.write_workflow import _owner_digest
from app.infra.persistence.browser.runs import PostgreSQLBrowserRunStore
from app.ports.browser_run_store import BrowserRunAuthorityPort, BrowserRunStoreError, RunSnapshot
from tests.browser_skill.test_teaching import SyntheticRunTeaching
from tests.infra.browser.test_local_read_execution import registered_site


def _store() -> tuple[PostgreSQLBrowserRunStore, AsyncMock]:
    session = AsyncMock(spec=AsyncSession)
    store = PostgreSQLBrowserRunStore(
        session_factory=cast(async_sessionmaker[AsyncSession], Mock()),
        authority=Mock(spec=BrowserRunAuthorityPort),
        digest_keys={"fixture": b"k" * 32}, active_digest_key_id="fixture",
    )
    return store, session


def _row(value: object) -> Mock:
    result = Mock()
    result.mappings.return_value.one_or_none.return_value = value
    return result


@pytest.mark.parametrize("case", ["valid", "invalid_model", "digest_mismatch", "missing"])
def test_frozen_publication_jsonb_preserves_strict_digest_and_safe_diagnostics(
    case: str, caplog: pytest.LogCaptureFixture,
) -> None:
    fixture = SyntheticRunTeaching()
    manifest = fixture.base
    run = fixture.source_run
    store, session = _store()
    payload = json.loads(manifest.model_dump_json())
    assert isinstance(payload["capability_bindings"], list)
    assert isinstance(payload["site"]["navigation_origins"], list)
    if case == "invalid_model":
        payload["private_probe"] = "synthetic-private-value-never-export"
    elif case == "digest_mismatch":
        run = replace(run, admission=replace(run.admission, publication_digest=b"x" * 32))
    session.execute.return_value = _row(None if case == "missing" else {"manifest": payload})
    if case == "valid":
        loaded = asyncio.run(store._frozen_publication(session, run))
        assert loaded == manifest
        assert caplog.records == []
    else:
        with pytest.raises(BrowserRunStoreError) as failed:
            asyncio.run(store._frozen_publication(session, run))
        assert failed.value.code == (
            "browser_step_publication_unavailable" if case == "missing"
            else "browser_step_publication_invalid"
        )
        if case == "missing":
            assert caplog.records == []
        else:
            assert len(caplog.records) == 1
            record = caplog.records[0]
            assert record.name == "app.infra.persistence.browser.runs"
            prefix, diagnostic = record.getMessage().split(" ", 1)
            assert prefix == "browser_run_store_diagnostic"
            assert json.loads(diagnostic) == {
                "stage": ("frozen_publication_validation" if case == "invalid_model"
                          else "frozen_publication_digest"),
                "exception_type": "BrowserRunStoreError",
                "trusted_error_code": "browser_step_publication_invalid",
            }
            assert record.exc_info is None and record.stack_info is None
            assert "synthetic-private-value-never-export" not in caplog.text
            assert manifest.digest not in caplog.text and run.run_id not in caplog.text
    session.execute.assert_awaited_once()
    statement, parameters = session.execute.await_args.args
    assert "WHERE tenant_id=:tenant AND publication_digest=:digest" in str(statement)
    assert parameters == {"tenant": run.owner.tenant_id, "digest": run.admission.publication_digest}


def _initial_plan(run: RunSnapshot, manifest: BrowserPublicationManifest) -> TeachingPlan:
    return TeachingPlan(
        purpose="initial_validation", task_id=run.task_id, run_id=run.run_id,
        owner_digest=_owner_digest(run.owner), binding_id=run.admission.binding_id,
        binding_revision=run.admission.binding_revision,
        source_publication_digest=manifest.digest, publication_digest=manifest.digest,
        skill_digest=manifest.skill.digest, site_digest=manifest.site.digest,
        policy_digest=manifest.site.policy.digest, verifier_digest=manifest.verifier.digest,
        grant_digest="d" * 64, sealed_input_digest=run.admission.input_digest.hex(),
        parameter_set_ref="synthetic_parameters",
        steps=tuple(TeachingPlanStep(
            step_id=rule.step.step_id, primitive_id=rule.step.step_id,
            rule_digest=canonical_digest("browser_site_step.v1", rule.model_dump(mode="json")),
        ) for rule in manifest.site.steps),
    )


@pytest.mark.parametrize("case,code", [
    ("valid", None),
    ("wrong_owner", "browser_teaching_plan_mismatch"),
    ("wrong_rule", "browser_teaching_primitive_mismatch"),
    ("invalid_integer", "browser_teaching_plan_invalid"),
    ("invalid_source", "browser_teaching_source_invalid"),
    ("invalid_mac", "browser_write_audit_invalid"),
])
def test_in_session_teaching_plan_reads_signed_jsonb_and_rejects_invalid_facts(
    case: str, code: str | None,
) -> None:
    fixture = SyntheticRunTeaching()
    # Use the actual registered-site initial-validation contract, without a browser.
    manifest = registered_site().manifest
    run = replace(fixture.source_run, admission=replace(
        fixture.source_run.admission, publication_digest=bytes.fromhex(manifest.digest),
    ))
    plan = _initial_plan(run, manifest)
    store, session = _store()
    store._read_run = AsyncMock(return_value=run)
    plan_data = json.loads(plan.model_dump_json())
    assert isinstance(plan_data["steps"], list)
    if case == "wrong_owner":
        plan_data["owner_digest"] = "0" * 64
    elif case == "wrong_rule":
        plan_data["steps"][0]["rule_digest"] = "0" * 64
    elif case == "invalid_integer":
        plan_data["binding_revision"] = True
    event = {
        "schema_version": "browser.teaching_plan_record.v1", "run_id": run.run_id,
        "run_revision": run.state_revision, "key_id": "fixture", "plan": plan_data,
    }
    event["mac"] = store._write_mac(event, "fixture")
    if case == "invalid_mac":
        event["mac"] = "0" * 64
    source = json.loads(manifest.model_dump_json())
    if case == "invalid_source":
        source["capability_bindings"] = []
    session.execute.side_effect = [
        _row({"manifest": json.loads(manifest.model_dump_json())}),
        _row({"event_type": "browser.teaching_plan_record.v1", "payload": event}),
        _row({"manifest": source}),
    ]
    if code is None:
        loaded = asyncio.run(store.load_teaching_plan_in_session(session, run.admission))
        assert loaded == plan
        assert session.execute.await_count == 3
        assert session.execute.await_args_list[2].args[1] == {
            "tenant": run.owner.tenant_id,
            "digest": bytes.fromhex(plan.source_publication_digest), "initial": True,
        }
    else:
        with pytest.raises(BrowserRunStoreError) as failed:
            asyncio.run(store.load_teaching_plan_in_session(session, run.admission))
        assert failed.value.code == code
    store._read_run.assert_awaited_once_with(
        session, run.owner, run.task_id, run.run_id,
    )


@pytest.mark.parametrize("case", ["valid", "invalid_mac", "invalid_integer"])
def test_audited_step_jsonb_preserves_nested_tuples_and_mac(case: str) -> None:
    fixture = SyntheticRunTeaching()
    run = fixture.source_run
    step = fixture.steps[0]
    store, session = _store()
    step_data = json.loads(step.model_dump_json())
    assert isinstance(step_data["event"]["approved_labels"], list)
    assert isinstance(step_data["event"]["scope"]["frame_path"], list)
    if case == "invalid_integer":
        step_data["binding"]["binding_revision"] = True
    payload = {
        "schema_version": "browser.run_step_audit.v1", "run_id": run.run_id,
        "sequence": 1, "run_revision": run.state_revision,
        "key_id": "fixture", "step": step_data,
    }
    payload["mac"] = store._step_mac(payload, "fixture")
    if case == "invalid_mac":
        payload["mac"] = "0" * 64
    result = Mock()
    result.mappings.return_value.all.return_value = [{"payload": payload}]
    session.execute.return_value = result
    if case == "valid":
        assert asyncio.run(store._audited_steps(session, run)) == (step,)
    else:
        with pytest.raises(BrowserRunStoreError) as failed:
            asyncio.run(store._audited_steps(session, run))
        assert failed.value.code == "browser_step_audit_invalid"
    session.execute.assert_awaited_once()
    assert session.execute.await_args.args[1] == {"task": run.task_id, "run": run.run_id}
