import pytest

from app.browser_skill.models import BrowserSessionRef
from app.infra.browser.resource_lifecycle import CapacityLedger, TerminationEvidence
from tests.browser_skill.factories import binding


@pytest.mark.parametrize(
    "field", ["session_ref", "manifest_digest", "resource_digest", "challenge"]
)
def test_termination_proof_must_bind_every_resource_field(field) -> None:
    ledger = CapacityLedger(1)
    session = BrowserSessionRef(session_ref="resource", binding=binding())
    record = ledger.reserve(session)
    record.state = "active"
    record.resource_digest, record.challenge = "a" * 64, "challenge"
    values = dict(
        session_ref=session.session_ref,
        manifest_digest="b" * 64,
        resource_digest="a" * 64,
        challenge="challenge",
        evidence_digest="c" * 64,
        remote_terminated=True,
    )
    values[field] = "e" * 64
    result = ledger.finish(record, TerminationEvidence(**values), "b" * 64)
    assert result.status == "quarantined" and ledger.occupied == 1
    with pytest.raises(ValueError, match="capacity_exhausted"):
        ledger.reserve(session.model_copy(update={"session_ref": "another"}))


def test_dispatched_slot_has_no_reservation_release_escape() -> None:
    ledger = CapacityLedger(1)
    record = ledger.reserve(BrowserSessionRef(session_ref="resource", binding=binding()))
    record.state = "acquiring"
    with pytest.raises(ValueError, match="was_dispatched"):
        ledger.release_reservation(record)
    assert ledger.occupied == 1
