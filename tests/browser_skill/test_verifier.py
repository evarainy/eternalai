import asyncio
import secrets

import pytest

from app.browser_skill.models import BrowserOperationError, Coverage, ReadFieldEvidence
from tests.browser_skill.fakes import FakeWorld, SyntheticRecord


def verify(world: FakeWorld):
    return asyncio.run(world.verifier.verify(world.session, world.confirmed, world.context))


def test_verifies_actual_independent_key_record_without_any_click() -> None:
    world = FakeWorld()
    result = verify(world)
    assert result.status == "verified" and result.evidence_digest is not None
    assert world.reads == 1 and len(world.sends) == 0


def test_wrong_selected_row_cannot_be_used_as_read_key_or_expected_result() -> None:
    world = FakeWorld()
    world.records[0].fields["state"] = secrets.token_urlsafe(24)
    # An unrelated displayed/selected record has the expected value, but its
    # independently confirmed key does not match the admission input.
    world.records.append(
        SyntheticRecord(
            secrets.token_urlsafe(24), world.binding, {"state": str(world._inputs["expected"])}
        )
    )
    assert verify(world).status == "mismatch"


@pytest.mark.parametrize(
    "case,expected",
    [
        ("duplicate", "mismatch"),
        ("wrong_owner", "mismatch"),
        ("empty", "incomplete"),
        ("missing", "incomplete"),
        ("partial", "incomplete"),
    ],
)
def test_incomplete_or_nonunique_records_cannot_verify(case: str, expected: str) -> None:
    world = FakeWorld()
    if case == "duplicate":
        world.records.append(world.records[0])
    elif case == "wrong_owner":
        world.records[0].owner = world.binding.model_copy(update={"binding_id": "other"})
    elif case == "empty":
        world.records = []
    elif case == "missing":
        world.records[0].fields = {}
    else:
        world.coverage = Coverage(state="partial", reason="pagination")
    assert verify(world).status == expected


@pytest.mark.parametrize("mutation", ["authorization", "binding", "confirmation"])
def test_rechecks_authorization_binding_and_confirmation_after_actual_read(mutation: str) -> None:
    world = FakeWorld()

    def change() -> None:
        if mutation == "authorization":
            world.allowed = False
        elif mutation == "binding":
            world.binding = world.binding.model_copy(update={"lease_epoch": 4})
        else:
            key = world.confirmed.business_key.model_copy(update={"confirmation_ref": "revoked"})
            world.confirmed = world.confirmed.model_copy(update={"business_key": key})

    world.after_read = change
    with pytest.raises(BrowserOperationError) as error:
        verify(world)
    assert error.value.failure.code in {"stale", "denied"}
    assert world.reads == 1


def test_unconfirmed_caller_key_is_denied_before_reader() -> None:
    world = FakeWorld()
    key = world.confirmed.business_key.model_copy(update={"confirmation_ref": "invented"})
    with pytest.raises(BrowserOperationError) as error:
        asyncio.run(
            world.verifier.verify(
                world.session,
                world.confirmed.model_copy(update={"business_key": key}),
                world.context,
            )
        )
    assert error.value.failure.code == "denied" and world.reads == 0


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("unsupported", "unsupported"),
        ("no_digest", "incomplete"),
        ("field_missing", "invalid_response"),
        ("field_extra", "invalid_response"),
    ],
)
def test_reader_evidence_is_checked_against_frozen_declared_fields(
    kind: str, expected: str
) -> None:
    world = FakeWorld()
    original = world.read

    async def faulty_read(session, spec, ctx):
        evidence = await original(session, spec, ctx)
        if kind == "unsupported":
            return evidence.model_copy(
                update={"fields": (ReadFieldEvidence(field_id="state", status="unsupported"),)}
            )
        if kind == "no_digest":
            return evidence.model_copy(update={"evidence_digest": None})
        if kind == "field_missing":
            return evidence.model_copy(update={"fields": ()})
        return evidence.model_copy(
            update={
                "fields": (*evidence.fields, ReadFieldEvidence(field_id="extra", status="matched"))
            }
        )

    world.read = faulty_read
    if expected == "invalid_response":
        with pytest.raises(BrowserOperationError) as error:
            verify(world)
        assert error.value.failure.code == expected
    else:
        assert verify(world).status == expected
