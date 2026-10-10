"""Profile payload recovery checks, with SQL results controlled at the seam."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock, Mock

import pytest

from app.infra.persistence.browser import profiles as module
from app.ports.browser_profile_store import (
    BrowserProfileCleanupFact,
    BrowserProfileError,
    BrowserProfileGeneration,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _context() -> SimpleNamespace:
    owner = SimpleNamespace(tenant_id="tenant", user_id="user", session_id="session")
    auth = SimpleNamespace(owner=owner)
    binding = SimpleNamespace(target_system="oa", binding_id="binding",
                              binding_revision=1, subject_digest=b"s" * 32)
    return SimpleNamespace(claim=SimpleNamespace(auth=auth, binding=binding,
                                                lease_epoch=2, provider_key="provider"),
                           task_id="task", run_id="run")


@pytest.mark.anyio
async def test_cleanup_candidate_refuses_recent_head_and_live_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    row = {"disposition": "retired", "disposition_changed_at": None,
           "created_at": now - timedelta(days=3),
           "lease_epoch": 2}
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._retention_seconds = 86_400
    store._get_cleanup_row = AsyncMock(return_value=row)
    session = Mock()
    clock = Mock()
    clock.scalar_one.return_value = now
    session.execute = AsyncMock(return_value=clock)
    with pytest.raises(BrowserProfileError) as pending:
        await store._cleanup_candidate(
            session, _context(), "generation",
            {"active_generation_id": None, "updated_at": now}, None,
        )
    assert pending.value.code == "browser_profile_retention_pending"
    assert session.execute.await_count == 1

    session.execute.reset_mock()
    result_now = Mock()
    result_now.scalar_one.return_value = now
    result_runs = Mock()
    result_runs.all.return_value = [("run",)]
    session.execute.side_effect = [result_now, result_runs]
    old = now - timedelta(days=3)
    with pytest.raises(BrowserProfileError) as referenced:
        await store._cleanup_candidate(
            session, _context(), "generation",
            {"active_generation_id": None, "updated_at": old}, None,
        )
    assert referenced.value.code == "browser_profile_cleanup_referenced"
    assert session.execute.await_count == 2
    assert "profile_generation_id=:generation_id OR" in str(
        session.execute.await_args_list[1].args[0]
    )
    assert "status IN ('running','waiting_user')" in str(
        session.execute.await_args_list[1].args[0]
    )


@pytest.mark.anyio
async def test_cleanup_candidate_never_accepts_orphan_without_transition_time() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._retention_seconds = 86_400
    store._get_cleanup_row = AsyncMock(return_value={
        "disposition": "orphan", "disposition_changed_at": None,
        "created_at": now - timedelta(days=3),
    })
    session = Mock()
    clock = Mock()
    clock.scalar_one.return_value = now
    session.execute = AsyncMock(return_value=clock)
    with pytest.raises(BrowserProfileError) as pending:
        await store._cleanup_candidate(
            session, _context(), "generation",
            {"active_generation_id": None,
             "updated_at": now - timedelta(days=3)}, None,
        )
    assert pending.value.code == "browser_profile_retention_pending"


@pytest.mark.anyio
async def test_retired_unreferenced_candidate_is_prepared_only() -> None:
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=3)
    row = {"disposition": "retired", "disposition_changed_at": old,
           "created_at": old, "lease_epoch": 2}
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._retention_seconds = 86_400
    store._get_cleanup_row = AsyncMock(return_value=row)
    session = Mock()
    clock = Mock()
    clock.scalar_one.return_value = now
    references = Mock()
    references.all.return_value = []
    session.execute = AsyncMock(side_effect=[clock, references])
    prepared = await store._cleanup_candidate(
        session, _context(), "generation",
        {"active_generation_id": None, "updated_at": old},
        {"capacity_held": False},
    )
    assert prepared is row
    assert session.execute.await_count == 2


@pytest.mark.anyio
async def test_orphan_with_database_transition_time_can_be_candidate() -> None:
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=3)
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._retention_seconds = 86_400
    store._get_cleanup_row = AsyncMock(return_value={
        "disposition": "orphan", "disposition_changed_at": old,
        "created_at": old, "lease_epoch": 2,
    })
    session = Mock()
    clock = Mock()
    clock.scalar_one.return_value = now
    references = Mock()
    references.all.return_value = []
    session.execute = AsyncMock(side_effect=[clock, references])
    await store._cleanup_candidate(
        session, _context(), "generation", None, {"capacity_held": False},
    )
    assert session.execute.await_count == 2


@pytest.mark.anyio
async def test_record_provider_release_fails_closed_without_proof_verifier() -> None:
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._cleanup = None
    store._get_generation = AsyncMock(return_value=_generation_row())
    session = Mock()
    session.execute = AsyncMock()
    store._locked = _locked_session(session)
    with pytest.raises(BrowserProfileError) as refused:
        await store.record_provider_cleanup_fact(_context(), "generation", b"synthetic-evidence")
    assert refused.value.code == "browser_profile_cleanup_proof_unavailable"
    session.execute.assert_not_awaited()


def _generation_row() -> dict[str, object]:
    return {
        "generation_id": "generation", "binding_revision": 1,
        "profile_revision": 2, "lease_epoch": 2,
        "provider_key": "provider", "capture_operation_id": "capture",
        "manifest_digest": b"m" * 32, "generation_ref_digest": b"g" * 32,
        "subject_digest": b"s" * 32, "origin_digest": b"o" * 32,
        "projection_digest": b"p" * 32, "captured_bytes": 17,
        "key_id": "fixture", "nonce": b"n" * 12, "ciphertext": b"c" * 17,
    }


def _locked_session(session: Mock, *, run: dict[str, object] | None = None,
                    head: dict[str, object] | None = None):
    @asynccontextmanager
    async def locked(
        *_args: object, **_kwargs: object,
    ) -> AsyncIterator[tuple[Mock, object, object, object]]:
        yield session, run or {
            "profile_generation_id": "generation",
            "capture_operation_id": "capture",
            "provider_key": "provider",
        }, head or {"active_generation_id": None}, {"capacity_held": False}

    return locked


def _mapped_result(row: object) -> Mock:
    result = Mock()
    result.mappings.return_value.one_or_none.return_value = row
    return result


def _scalar_result(value: object) -> Mock:
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


def _cleanup_row() -> dict[str, object]:
    return {
        "provider_key": "provider", "capture_operation_id": "capture",
        "generation_ref_digest": b"g" * 32,
    }


def _cleanup_receipt() -> dict[str, object]:
    return {**_cleanup_row(), "proof_digest": b"d" * 32}


@pytest.mark.anyio
async def test_record_validated_keeps_metadata_and_ciphertext_in_separate_rows() -> None:
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    generation = module._generation(_generation_row())
    store._capture = Mock()
    store._capture.verify_capture = AsyncMock(return_value=generation.fact)
    store._ensure_head = AsyncMock()
    session = Mock()
    session.execute = AsyncMock()
    store._locked = _locked_session(session, run={
        "capture_operation_id": "capture", "capture_status": "sent",
        "provider_manifest_digest": b"m" * 32, "profile_generation_id": None,
    }, head={"profile_revision": 1, "active_generation_id": None})
    await store.record_validated(
        _context(), generation, b"synthetic-capture-evidence",
        expected_profile_revision=1,
    )
    assert session.execute.await_count == 3
    metadata = session.execute.await_args_list[0]
    payload = session.execute.await_args_list[1]
    assert "INSERT INTO browser_profile_generations" in str(metadata.args[0])
    assert "ciphertext" not in str(metadata.args[0])
    assert "INSERT INTO browser_profile_payloads" in str(payload.args[0])
    assert payload.args[1]["ciphertext"] == generation.ciphertext
    assert payload.args[1]["nonce"] == generation.nonce
    assert payload.args[1]["key_id"] == generation.key_id


@pytest.mark.anyio
async def test_load_active_reconstructs_exact_owner_generation() -> None:
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    row = {**_generation_row(), "disposition": "promoted"}
    store._get_generation = AsyncMock(return_value=row)
    session = Mock()
    store._locked = _locked_session(session, run={
        "provider_key": "provider", "provider_manifest_digest": b"m" * 32,
    }, head={"profile_revision": 2, "active_generation_id": "generation"})
    head = await store.load_active(_context())
    assert head.revision == 2
    assert head.active is not None
    assert head.active.fact.generation_id == "generation"
    assert head.active.ciphertext == b"c" * 17
    assert store._get_generation.await_args.args[2] == "generation"


@pytest.mark.anyio
async def test_discovery_defers_retained_generation_without_release_receipt() -> None:
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._cleanup_candidate = AsyncMock(return_value=_cleanup_row())
    session = Mock()
    rows = Mock()
    rows.mappings.return_value.all.return_value = [{"generation_id": "generation"}]
    session.execute = AsyncMock(side_effect=[rows, _scalar_result(None)])
    store._locked = _locked_session(session)
    page = await store.discover_cleanup_candidates(_context())
    assert page.generation_ids == ()
    assert page.deferred == (("generation", "browser_profile_cleanup_proof_missing"),)


@pytest.mark.anyio
async def test_cleanup_proof_mismatch_makes_no_database_change() -> None:
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._get_generation = AsyncMock(return_value=_generation_row())
    store._cleanup = Mock()
    store._cleanup.verify_cleanup = AsyncMock(return_value=BrowserProfileCleanupFact(
        "wrong-provider", "capture", "generation", b"g" * 32, b"d" * 32,
    ))
    session = Mock()
    session.execute = AsyncMock()
    store._locked = _locked_session(session)
    with pytest.raises(BrowserProfileError) as refused:
        await store.record_provider_cleanup_fact(_context(), "generation", b"synthetic-evidence")
    assert refused.value.code == "browser_profile_cleanup_mismatch"
    session.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_record_provider_release_is_idempotent_for_exact_receipt() -> None:
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    row = _generation_row()
    store._get_generation = AsyncMock(return_value=row)
    generation: BrowserProfileGeneration = module._generation(row)
    store._cleanup = Mock()
    store._cleanup.verify_cleanup = AsyncMock(return_value=BrowserProfileCleanupFact(
        generation.fact.provider_key, generation.fact.capture_operation_id,
        generation.fact.generation_id, generation.fact.generation_ref_digest, b"d" * 32,
    ))
    session = Mock()
    session.execute = AsyncMock()
    session.execute.side_effect = [Mock(), _mapped_result(_cleanup_receipt())]
    store._locked = _locked_session(session)
    await store.record_provider_cleanup_fact(_context(), "generation", b"synthetic-evidence")
    assert store._get_generation.await_count == 2
    assert "ON CONFLICT DO NOTHING" in str(session.execute.await_args_list[0].args[0])
    assert "browser_profile_cleanup_receipts" in str(session.execute.await_args_list[1].args[0])


@pytest.mark.anyio
async def test_record_provider_release_rejects_conflicting_existing_receipt() -> None:
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._get_generation = AsyncMock(return_value=_generation_row())
    store._cleanup = Mock()
    store._cleanup.verify_cleanup = AsyncMock(return_value=BrowserProfileCleanupFact(
        "provider", "capture", "generation", b"g" * 32, b"d" * 32,
    ))
    session = Mock()
    session.execute = AsyncMock(side_effect=[Mock(), _mapped_result({
        **_cleanup_receipt(), "proof_digest": b"x" * 32,
    })])
    store._locked = _locked_session(session)
    with pytest.raises(BrowserProfileError) as refused:
        await store.record_provider_cleanup_fact(_context(), "generation", b"synthetic-evidence")
    assert refused.value.code == "browser_profile_cleanup_receipt_conflict"


@pytest.mark.anyio
async def test_cleanup_requires_durable_receipt_before_any_state_change() -> None:
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._cleanup_candidate = AsyncMock(return_value=_cleanup_row())
    session = Mock()
    session.execute = AsyncMock(return_value=_mapped_result(None))
    store._locked = _locked_session(session)
    with pytest.raises(BrowserProfileError) as refused:
        await store.cleanup(_context(), "generation")
    assert refused.value.code == "browser_profile_cleanup_proof_missing"
    assert session.execute.await_count == 1


@pytest.mark.anyio
async def test_cleanup_rejects_receipt_for_different_capture() -> None:
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._cleanup_candidate = AsyncMock(return_value=_cleanup_row())
    session = Mock()
    session.execute = AsyncMock(return_value=_mapped_result({
        **_cleanup_receipt(), "capture_operation_id": "other-capture",
    }))
    store._locked = _locked_session(session)
    with pytest.raises(BrowserProfileError) as refused:
        await store.cleanup(_context(), "generation")
    assert refused.value.code == "browser_profile_cleanup_receipt_conflict"
    assert session.execute.await_count == 1


@pytest.mark.anyio
async def test_cleanup_uses_exact_receipt_and_removes_payload_in_one_transaction() -> None:
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._cleanup_candidate = AsyncMock(return_value=_cleanup_row())
    session = Mock()
    session.execute = AsyncMock(side_effect=[
        _mapped_result(_cleanup_receipt()), _scalar_result("generation"),
        _scalar_result("generation"), _scalar_result(None),
    ])
    store._locked = _locked_session(session)
    await store.cleanup(_context(), "generation")
    assert store._cleanup_candidate.await_count == 1
    assert "SELECT * FROM browser_profile_cleanup_receipts" in str(
        session.execute.await_args_list[0].args[0]
    )
    assert "UPDATE browser_profile_generations" in str(session.execute.await_args_list[1].args[0])
    assert "DELETE FROM browser_profile_payloads" in str(session.execute.await_args_list[2].args[0])
    assert "SELECT 1 FROM browser_profile_payloads" in str(
        session.execute.await_args_list[3].args[0]
    )


@pytest.mark.anyio
async def test_cleanup_rejects_live_reference_before_receipt_lookup() -> None:
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._cleanup_candidate = AsyncMock(
        side_effect=BrowserProfileError("browser_profile_cleanup_referenced")
    )
    session = Mock()
    session.execute = AsyncMock()
    store._locked = _locked_session(session)
    with pytest.raises(BrowserProfileError) as refused:
        await store.cleanup(_context(), "generation")
    assert refused.value.code == "browser_profile_cleanup_referenced"
    session.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_cleanup_refuses_missing_payload_after_state_update() -> None:
    store = object.__new__(module.PostgreSQLBrowserProfileStore)
    store._cleanup_candidate = AsyncMock(return_value=_cleanup_row())
    session = Mock()
    session.execute = AsyncMock(side_effect=[
        _mapped_result(_cleanup_receipt()), _scalar_result("generation"),
        _scalar_result(None),
    ])
    store._locked = _locked_session(session)
    with pytest.raises(BrowserProfileError) as refused:
        await store.cleanup(_context(), "generation")
    assert refused.value.code == "browser_profile_payload_missing"
