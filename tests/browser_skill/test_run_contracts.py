"""Synthetic process-local facts only; none of these tests proves store durability."""

import json
import pickle
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.browser_skill.models import BrowserOwner, VerificationResult
from app.browser_skill.registry import Publication, canonical_digest
from app.browser_skill.results import ResultState
from app.browser_skill.run_contracts import (
    BrowserAcceptedView,
    BrowserArtifactView,
    BrowserCancelView,
    BrowserDraftView,
    BrowserProgressView,
    BrowserRunRef,
    BrowserRunView,
    CommitFact,
    OwnedRunValue,
    _mint_commit_fact,
    compare_run_update,
    project_accepted,
    project_draft,
    project_result,
    project_run,
)
from app.browser_skill.scheduling import CancelSnapshot
from app.browser_skill.trajectory import (
    DraftManifest,
    ParameterApproval,
    PublicationReference,
    SkillDraft,
)
from tests.browser_skill.factories import DIGEST, binding


def reference(revision: int = 5, **changes: Any) -> BrowserRunRef:
    data = dict(owner=binding().owner, task_id="task", run_id="run", state_revision=revision)
    data.update(changes)
    return BrowserRunRef(**data)


def synthetic_fact(revision: int = 5) -> CommitFact:
    return _mint_commit_fact(reference(revision))


def cancellation(**changes: Any) -> CancelSnapshot:
    data = dict(binding=binding(), run_id="run", revision=1)
    data.update(changes)
    return CancelSnapshot(**data)


def completed(cleanup: str = "pending") -> ResultState:
    return ResultState.model_validate(
        dict(
            business="completed",
            effect="acknowledged",
            cleanup=cleanup,
            verification=VerificationResult(status="verified", evidence_digest=DIGEST),
        )
    )


def running(revision: int = 5, **changes: Any) -> BrowserRunView:
    data = dict(progress=BrowserProgressView(phase="running", completed_steps=1, total_steps=3))
    data.update(changes)
    return project_run(synthetic_fact(revision), binding().owner, cancellation(), **data)


def terminal(revision: int = 5, cleanup: str = "pending") -> BrowserRunView:
    return project_run(
        synthetic_fact(revision),
        binding().owner,
        cancellation(),
        result=OwnedRunValue(reference(revision), completed(cleanup)),
        terminal_revision=4,
    )


def compare(before: BrowserRunView | None, after: BrowserRunView, **changes: Any) -> str:
    arguments = dict(task_id="task", run_id="run", request_generation=2, current_generation=2)
    arguments.update(changes)
    return compare_run_update(before, after, **arguments)


def test_synthetic_fact_explicit_projection_has_no_owner_or_durability_label() -> None:
    fact = synthetic_fact()
    projected = project_accepted(fact, binding().owner)
    assert projected.model_dump() == dict(
        kind="accepted", task_id="task", run_id="run", state_revision=5
    )
    assert repr(fact) == "<CommitFact: process-local>"
    assert "owner" not in reference().model_dump()
    with pytest.raises(TypeError, match="serialization_forbidden"):
        pickle.dumps(fact)


@pytest.mark.parametrize("fake", [True, {"committed": True}, {"durability": "durable"}, object()])
def test_request_values_cannot_supply_acceptance(fake: Any) -> None:
    with pytest.raises(TypeError, match="commit_fact_required"):
        project_accepted(fake, binding().owner)
    with pytest.raises(TypeError, match="commit_fact_required"):
        CommitFact(reference(), _seal=fake)


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "session_id"])
def test_acceptance_checks_every_owner_dimension(field: str) -> None:
    owner = dict(tenant_id="tenant", user_id="user", session_id="session")
    original = binding().owner
    owner = {key: getattr(original, key) for key in owner}
    owner[field] = "another"
    with pytest.raises(ValueError, match="owner_mismatch"):
        project_accepted(synthetic_fact(), BrowserOwner(**owner))


@pytest.mark.parametrize("value", [-1, 9_007_199_254_740_992, True, 1.0, "1", float("nan")])
def test_revision_requires_js_safe_strict_integer(value: Any) -> None:
    with pytest.raises(ValidationError):
        BrowserAcceptedView(task_id="task", run_id="run", state_revision=value)


def test_maximum_js_integer_roundtrips_without_extra_fields() -> None:
    value = BrowserAcceptedView(task_id="task", run_id="run", state_revision=9_007_199_254_740_991)
    assert BrowserAcceptedView.model_validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        BrowserAcceptedView.model_validate_json(value.model_dump_json()[:-1] + ',"profile_id":"x"}')


@pytest.mark.parametrize(
    "counts", [dict(completed_steps=1), dict(total_steps=2), dict(completed_steps=3, total_steps=2)]
)
def test_progress_rejects_incomplete_or_reversed_counts(counts: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        BrowserProgressView(phase="running", **counts)


def test_cancel_request_is_not_acknowledgement_or_terminal() -> None:
    view = project_run(
        synthetic_fact(),
        binding().owner,
        cancellation(requested=True),
        progress=BrowserProgressView(phase="running"),
    )
    assert view.cancel == BrowserCancelView(requested=True, acknowledged=False)
    assert view.status == "running" and view.result is None
    with pytest.raises(ValidationError, match="request_required"):
        BrowserCancelView(requested=False, acknowledged=True)
    cancelled = ResultState(
        business="cancelled",
        effect="not_sent",
        cleanup="terminated",
        error_code="browser_cancelled",
    )
    with pytest.raises(ValidationError, match="not_acknowledged"):
        project_run(
            synthetic_fact(),
            binding().owner,
            cancellation(requested=True),
            result=OwnedRunValue(reference(), cancelled),
            terminal_revision=4,
        )
    acknowledged = project_run(
        synthetic_fact(),
        binding().owner,
        cancellation(requested=True, acknowledged=True),
        result=OwnedRunValue(reference(), cancelled),
        terminal_revision=4,
    )
    assert acknowledged.status == "cancelled"


def test_unknown_effect_is_terminal_failure_without_replay() -> None:
    state = ResultState(
        business="failed",
        effect="unknown",
        cleanup="quarantined",
        error_code="browser_effect_unknown",
    )
    result = project_result(state, 4)
    assert (result.business, result.error_code, result.automatic_replay) == (
        "failed",
        "browser_effect_unknown",
        False,
    )
    for value in (True, 0, "false"):
        with pytest.raises(ValidationError):
            type(result).model_validate({**result.model_dump(), "automatic_replay": value})


def test_verified_business_survives_cleanup_failure() -> None:
    view = terminal(6, "failed")
    assert view.status == "completed"
    assert view.result is not None and view.result.cleanup == "failed"
    assert view.result.error_code is None and view.result.verification == "verified"
    assert compare(terminal(), view) == "apply"


def test_nonterminal_or_forged_result_cannot_project_terminal() -> None:
    with pytest.raises(ValueError, match="not_terminal"):
        project_result(ResultState(business="running", effect="not_sent", cleanup="pending"), 4)
    with pytest.raises(ValidationError, match="verification_inconsistent"):
        project_result(completed().model_copy(update={"verification": None}), 4)
    with pytest.raises(ValueError, match="revision_required"):
        running(result=OwnedRunValue(reference(), completed()))
    with pytest.raises(ValueError, match="terminal_missing"):
        running(terminal_revision=4)
    with pytest.raises(ValidationError, match="terminal_inconsistent"):
        running(result=OwnedRunValue(reference(), completed()), terminal_revision=9)


def artifact() -> BrowserArtifactView:
    return BrowserArtifactView(
        artifact_id="artifact",
        kind="report",
        media_type="application/pdf",
        size_bytes=100,
        expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        availability="available",
    )


def test_artifact_metadata_has_no_access_grant_or_url() -> None:
    view = running(artifacts=(OwnedRunValue(reference(), artifact()),))
    assert view.artifacts == (artifact(),)
    assert set(view.artifacts[0].model_dump()) == {
        "artifact_id",
        "kind",
        "media_type",
        "size_bytes",
        "expires_at",
        "availability",
    }
    with pytest.raises(ValidationError):
        BrowserArtifactView.model_validate({**artifact().model_dump(), "url": "forbidden"})
    with pytest.raises(ValidationError, match="aware"):
        BrowserArtifactView.model_validate(
            {**artifact().model_dump(), "expires_at": datetime(2027, 1, 1)}
        )
    with pytest.raises(ValidationError, match="artifact_duplicate"):
        running(artifacts=(OwnedRunValue(reference(), artifact()),) * 2)


@pytest.mark.parametrize(
    "change",
    [
        dict(task_id="other"),
        dict(run_id="other"),
        dict(state_revision=6),
        dict(owner=BrowserOwner(tenant_id="other", user_id="u", session_id="s")),
    ],
)
def test_child_result_and_artifact_require_owner_task_run_revision(change: dict[str, Any]) -> None:
    ref = reference(**change)
    with pytest.raises(ValueError, match="child_mismatch"):
        running(artifacts=(OwnedRunValue(ref, artifact()),))
    with pytest.raises(ValueError, match="child_mismatch"):
        project_run(
            synthetic_fact(),
            binding().owner,
            cancellation(),
            result=OwnedRunValue(ref, completed()),
            terminal_revision=4,
        )


@pytest.mark.parametrize(
    "change",
    [
        dict(run_id="other"),
        dict(revision=6),
        dict(
            binding=binding().model_copy(
                update={
                    "owner": BrowserOwner(tenant_id="other", user_id="u", session_id="s"),
                }
            )
        ),
    ],
)
def test_cancellation_requires_matching_owner_run_and_snapshot_revision(
    change: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="cancel_mismatch"):
        project_run(
            synthetic_fact(),
            binding().owner,
            cancellation(**change),
            progress=BrowserProgressView(phase="running"),
        )


def synthetic_draft() -> tuple[SkillDraft, Publication]:
    path = Path(__file__).resolve().parents[2] / "skills/browser/ecology9/search_contact.json"
    publication = Publication.model_validate_json(path.read_bytes())
    data = dict(
        schema_version="skill_draft.v1",
        draft_id="draft",
        base_revision=4,
        draft_revision=1,
        base_publication=PublicationReference.from_publication(publication).model_dump(mode="json"),
        parameter_whitelist=[
            ParameterApproval(name=name, purpose="fill_value", format="text").model_dump(
                mode="json"
            )
            for name in publication.skill.parameters
        ],
        skill=publication.skill.model_dump(mode="json"),
        state="draft",
        validation_digest=None,
        rejection=None,
    )
    data["digest"] = canonical_digest("skill_draft.v1", data)
    return SkillDraft(binding(), DraftManifest.model_validate_json(json.dumps(data))), publication


def test_draft_exposes_only_manifest_metadata_and_registered_names() -> None:
    draft, publication = synthetic_draft()
    view = project_draft(draft, binding(), publication, 4)
    assert view.parameter_names == tuple(item.name for item in draft.manifest.parameter_whitelist)
    assert view.parameter_names and view.executable is False
    assert set(view.model_dump()) == {
        "draft_id",
        "draft_revision",
        "base_revision",
        "base_publication_digest",
        "draft_digest",
        "state",
        "rejection",
        "parameter_names",
        "executable",
    }
    assert running(draft=OwnedRunValue(reference(), view)).draft == view
    for value in (True, 0):
        with pytest.raises(ValidationError):
            BrowserDraftView.model_validate({**view.model_dump(), "executable": value})
    with pytest.raises(ValidationError):
        BrowserDraftView.model_validate(
            {**view.model_dump(), "parameter_names": ("raw value with space",)}
        )
    with pytest.raises(ValueError):
        project_draft(draft, binding().model_copy(update={"binding_revision": 99}), publication, 4)
    with pytest.raises(ValueError):
        project_draft(draft, binding(), publication, 5)


def test_generation_and_target_fences_drop_late_response() -> None:
    assert compare(None, running(), request_generation=1) == "drop"
    assert compare(running(), terminal(6), current_generation=3) == "drop"
    assert compare(None, running(), task_id="other") == "drop"
    assert compare(None, running(), run_id="other") == "drop"
    assert compare(None, running()) == "apply"


@pytest.mark.parametrize("generation", [True, -1, 9_007_199_254_740_992, "2"])
def test_generation_is_strict_js_safe_integer(generation: Any) -> None:
    with pytest.raises(ValueError, match="generation_invalid"):
        compare(None, running(), request_generation=generation)


def test_revision_is_monotonic_and_equal_revision_conflicts_are_visible() -> None:
    before = running()
    assert compare(before, running(4)) == "drop"
    assert compare(before, before) == "noop"
    assert compare(before, running(progress=BrowserProgressView(phase="verifying"))) == "conflict"
    assert compare(before, running(6)) == "apply"
    wrong_current = before.model_copy(update={"run_id": "other"})
    assert compare(wrong_current, running(6)) == "conflict"


def test_terminal_cannot_be_replaced_or_reenter_progress() -> None:
    before = terminal()
    failed = ResultState(
        business="failed",
        effect="unknown",
        cleanup="quarantined",
        error_code="browser_effect_unknown",
    )
    replacement = project_run(
        synthetic_fact(6),
        binding().owner,
        cancellation(),
        result=OwnedRunValue(reference(6), failed),
        terminal_revision=4,
    )
    assert compare(before, replacement) == "conflict"
    assert compare(before, running(6)) == "drop"
    changed_terminal_revision = terminal(6).model_copy(
        update={
            "result": project_result(completed(), 5),
        }
    )
    assert compare(before, changed_terminal_revision) == "conflict"


def test_cancel_and_progress_facts_cannot_regress() -> None:
    pending = project_run(
        synthetic_fact(),
        binding().owner,
        cancellation(requested=True),
        progress=BrowserProgressView(phase="running"),
    )
    assert compare(pending, running(6)) == "conflict"
    acked = pending.model_copy(
        update={"cancel": BrowserCancelView(requested=True, acknowledged=True)}
    )
    later = pending.model_copy(update={"state_revision": 6})
    assert compare(acked, later) == "conflict"
    for counts in (
        dict(completed_steps=0, total_steps=3),
        dict(completed_steps=1, total_steps=4),
        {},
    ):
        assert (
            compare(running(), running(6, progress=BrowserProgressView(phase="running", **counts)))
            == "conflict"
        )


def test_malformed_model_copy_does_not_bypass_wire_invariants() -> None:
    malformed = terminal().model_copy(update={"status": "running"})
    with pytest.raises(ValidationError, match="progress_inconsistent"):
        compare(None, malformed)
    with pytest.raises(ValidationError):
        running(
            artifacts=(
                OwnedRunValue(
                    reference(),
                    artifact().model_copy(
                        update={"size_bytes": True},
                    ),
                ),
            )
        )
    with pytest.raises(ValidationError):
        running(
            artifacts=(
                OwnedRunValue(
                    reference().model_copy(
                        update={"state_revision": -1},
                    ),
                    artifact(),
                ),
            )
        )
    with pytest.raises(ValidationError):
        BrowserRunView.model_validate_json(running().model_dump_json()[:-1] + ',"credentials":{}}')
