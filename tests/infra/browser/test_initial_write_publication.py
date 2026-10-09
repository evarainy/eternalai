"""Prepared write publication retains MAC validation, confirmation and verification."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from app.browser_skill.models import BrowserOwner
from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    canonical_json,
    publication_digest,
)
from app.infra.browser.teaching_installation import (
    InstalledReplayProofLookup,
    PostgreSQLTeachingPublicationReview,
)
from app.infra.persistence.browser.publications import PostgreSQLBrowserPublicationStore
from app.infra.persistence.browser.runs import PostgreSQLBrowserRunStore
from app.ports.browser_publication_store import BrowserPublicationError
from app.version_binding import capability_version_bindings
from tests.browser_skill.test_submission import _write_run
from tests.infra.browser.test_local_read_execution import registered_site


def write_manifest() -> BrowserPublicationManifest:
    original = registered_site().manifest
    data = original.model_dump(mode="json")
    capability = original.capability.model_copy(update={"type": "action"})
    data["capability_snapshot_json"] = canonical_json(capability.model_dump(mode="json"))
    data["capability_bindings"] = [
        item.model_dump(mode="json") for item in capability_version_bindings(capability)
    ]
    data["effect"] = "controlled_write"
    for step in data["skill"]["steps"]:
        step.update(operation="click", effect="may_write")
    for rule in data["site"]["steps"]:
        rule["step"].update(operation="click", effect="may_write")
        rule["effect"].update(operation="click", actual_effect="may_write")
    data["site"]["digest"] = publication_digest("browser_site.publication.v1", data["site"])
    data["skill"]["site_digest"] = data["site"]["digest"]
    data["skill"]["digest"] = publication_digest("browser_skill.v1", data["skill"])
    data["digest"] = publication_digest("browser_publication.durable.v1", data)
    return BrowserPublicationManifest.model_validate(data)


def test_initial_write_activation_requires_the_existing_review_transition() -> None:
    async def scenario() -> None:
        manifest = write_manifest()
        owner = BrowserOwner(tenant_id="tenant_1", user_id="user_1", session_id="session_1")
        store = PostgreSQLBrowserPublicationStore(
            Mock(), Mock(), Mock(), review_authority=Mock(),
        )
        store._transition = AsyncMock(return_value="reviewed")
        result = await store.activate_initial(
            owner, manifest, task_id="task_1", run_id="run_1", approval_ref="approval_1",
        )
        assert result == "reviewed"
        store._transition.assert_awaited_once_with(
            owner, manifest.skill.skill_id, manifest.digest, 0, "activate",
            initial=(manifest, "task_1", "run_1", "approval_1"),
        )
        store._review = None
        with pytest.raises(BrowserPublicationError) as caught:
            await store.activate_initial(
                owner, manifest, task_id="task_1", run_id="run_1", approval_ref="approval_1",
            )
        assert caught.value.args == ("browser_publication_initial_invalid",)
        assert store._transition.await_count == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("missing", [None, "plan", "consumption", "verified_run", "binding"])
def test_write_review_cannot_publish_without_real_validation_and_consumed_confirmation(missing):
    async def scenario() -> None:
        owner = BrowserOwner(tenant_id="tenant_1", user_id="user_1", session_id="session_1")
        manifest = write_manifest()
        snapshot = _write_run(owner, completed=True)
        snapshot = replace(snapshot, admission=replace(
            snapshot.admission, publication_digest=bytes.fromhex(manifest.digest),
        ))
        sessions, authority = Mock(), Mock(check_run=AsyncMock())
        runs = PostgreSQLBrowserRunStore(
            session_factory=sessions, authority=authority,
            digest_keys={"fixture": b"k" * 32}, active_digest_key_id="fixture",
        )
        runs._read_run = AsyncMock(return_value=snapshot)
        runs.load_teaching_plan_in_session = AsyncMock(
            return_value=None if missing == "plan" else Mock(
                purpose="initial_validation", publication_digest=manifest.digest,
            ),
        )
        preparation = Mock(request_id="confirmation_1", action_digest="a" * 64)
        runs._prepared_write = AsyncMock(return_value=preparation)
        runs._write_event = AsyncMock(return_value=None if missing == "consumption" else {
            "request_id": preparation.request_id, "action_digest": preparation.action_digest,
            "worker_epoch": snapshot.worker_epoch, "lease_epoch": snapshot.lease_epoch,
        })
        lookup = InstalledReplayProofLookup(sessions)
        lookup.install_runs(runs)
        review = PostgreSQLTeachingPublicationReview(sessions, lookup)
        row = {"binding_id": "binding_1", "binding_revision": 1,
               "input_digest": b"i" * 32, "verification_evidence_digest": b"v" * 32}
        result = Mock()
        result.mappings.return_value.one_or_none.return_value = (
            None if missing == "verified_run" else row
        )
        result.scalar_one_or_none.return_value = None if missing == "binding" else 1
        session = Mock(execute=AsyncMock(return_value=result))
        approved = await review._initial_run(session, owner, manifest, "task_1", "run_1")
        assert (approved is not None) == (missing is None)
        authority.check_run.assert_awaited_once_with(session, snapshot, "read")
        if missing == "plan":
            runs._prepared_write.assert_not_awaited()
            session.execute.assert_not_awaited()
        elif missing == "consumption":
            session.execute.assert_not_awaited()
        else:
            query = str(session.execute.call_args_list[0].args[0])
            assert "bp.state='prepared'" in query
            assert "br.verification='verified'" in query
            assert "br.effect='acknowledged'" in query
    asyncio.run(scenario())
