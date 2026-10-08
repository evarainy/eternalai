import asyncio
import secrets

import pytest

from app.browser_skill.models import (
    BrowserOperationError,
    BrowserSessionRef,
    Coverage,
    ExecutionContext,
    PageEvidence,
    ReadEvidence,
    ReadFieldEvidence,
    ReadSpec,
    VerificationResult,
)
from app.browser_skill.site_rules import ExpectedDownload, RegisteredTraversal
from app.browser_skill.verifier import IndependentVerifier
from tests.browser_skill.fakes import FakeWorld, SyntheticRecord


def verify(world: FakeWorld) -> VerificationResult:
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

    async def faulty_read(
        session: BrowserSessionRef, spec: ReadSpec, ctx: ExecutionContext,
    ) -> ReadEvidence:
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

    setattr(world, "read", faulty_read)
    if expected == "invalid_response":
        with pytest.raises(BrowserOperationError) as error:
            verify(world)
        assert error.value.failure.code == expected
    else:
        assert verify(world).status == expected


def _expected_download(world: FakeWorld) -> ExpectedDownload:
    return ExpectedDownload(
        step_id=world.skill.steps[0].step_id,
        object_type=world.plan.read_rule.object_type,
        media_type="text/plain", max_bytes=1024, ttl_seconds=60,
        content_rule_digest="a" * 64,
    )


def test_download_expectation_requires_independent_file_verifier() -> None:
    world = FakeWorld()
    world.verifier = IndependentVerifier(
        world, world.site, world.read_spec,
        expected_downloads=(_expected_download(world),),
    )
    assert verify(world).status == "unsupported"
    assert world.reads == 0


def test_file_proof_is_bound_into_final_digest_and_keeps_raw_read_digest() -> None:
    world = FakeWorld()
    ordinary = verify(world)
    requested: list[tuple[ExpectedDownload, ...]] = []

    async def verify_files(
        session: BrowserSessionRef, context: ExecutionContext,
        expected: tuple[ExpectedDownload, ...],
    ) -> str:
        assert session == world.session and context is world.context
        requested.append(expected)
        return "b" * 64

    expected = _expected_download(world)
    world.verifier = IndependentVerifier(
        world, world.site, world.read_spec,
        expected_downloads=(expected,), verify_downloads=verify_files,
    )
    result = verify(world)
    assert result.status == "verified"
    assert requested == [(expected,)]
    assert result.evidence_digest != ordinary.evidence_digest
    assert result.read_evidence_digest == ordinary.evidence_digest


def test_invalid_file_proof_cannot_verify() -> None:
    world = FakeWorld()

    async def invalid_file_proof(
        session: BrowserSessionRef, context: ExecutionContext,
        expected: tuple[ExpectedDownload, ...],
    ) -> str:
        return "unverified"

    world.verifier = IndependentVerifier(
        world, world.site, world.read_spec,
        expected_downloads=(_expected_download(world),),
        verify_downloads=invalid_file_proof,
    )
    assert verify(world).status == "incomplete"


def test_page_chain_requires_unique_complete_terminal_coverage() -> None:
    world = FakeWorld()
    traversal = RegisteredTraversal(
        region_id=world.view.scope.region_id,
        object_type=world.plan.read_rule.object_type,
        mode="paged", advance_step_id=world.skill.steps[0].step_id,
        max_pages=3, max_rows=3,
        page_key_rule_digest="1" * 64,
        row_key_rule_digest="2" * 64,
        ordinal_rule_digest="3" * 64,
        total_rule_digest="4" * 64,
        terminal_rule_digest="5" * 64,
    )
    first = PageEvidence(
        scope=world.view.scope, page_key_digest="a" * 64,
        row_key_digests=("c" * 64,), row_ordinals=(0,), declared_total=2,
        terminal_seen=False, evidence_digest="e" * 64,
    )
    last = PageEvidence(
        scope=world.view.scope, page_key_digest="b" * 64,
        row_key_digests=("d" * 64,), row_ordinals=(1,), declared_total=2,
        terminal_seen=True, evidence_digest="f" * 64,
    )
    assert IndependentVerifier._page_chain(traversal, (first, last)) is not None
    assert IndependentVerifier._page_chain(traversal, (
        first, last.model_copy(update={"page_key_digest": first.page_key_digest}),
    )) is None
    assert IndependentVerifier._page_chain(traversal, (
        first, last.model_copy(update={"row_ordinals": (0,)}),
    )) is None
    assert IndependentVerifier._page_chain(traversal, (
        first, last.model_copy(update={"terminal_seen": False}),
    )) is None
