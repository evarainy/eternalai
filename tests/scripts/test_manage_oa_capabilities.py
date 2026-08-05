from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from app.ports.capability_registry import CapabilitySpec
from scripts import manage_oa_capabilities as manager
from scripts.smoke.capabilities import expected_oa_capabilities, schema_digest


def _legacy_catalog(count: int = 9) -> tuple[CapabilitySpec, ...]:
    template = expected_oa_capabilities()[0]
    return tuple(
        template.model_copy(
            update={
                "capability_id": f"oa.legacy_placeholder_{index}",
                "name": f"Legacy placeholder {index}",
            }
        )
        for index in range(count)
    )


def _known_disabled_catalog() -> tuple[CapabilitySpec, ...]:
    template = expected_oa_capabilities()[0]
    return (
        template.model_copy(
            update={
                "capability_id": "oa.preexisting_disabled",
                "name": "Preexisting disabled",
                "status": "disabled",
            }
        ),
    )


def _known_unchanged_catalog() -> tuple[CapabilitySpec, ...]:
    template = expected_oa_capabilities()[0]
    return (
        template.model_copy(
            update={
                "capability_id": "oa.preexisting_draft",
                "name": "Preexisting draft",
                "status": "draft",
            }
        ),
    )


def _fingerprints(catalog: tuple[CapabilitySpec, ...]) -> frozenset[str]:
    return frozenset(manager._capability_fingerprint(item) for item in catalog)


def _install_authorized_sets(
    monkeypatch: pytest.MonkeyPatch,
    legacy: tuple[CapabilitySpec, ...],
    known_disabled: tuple[CapabilitySpec, ...] = (),
    known_unchanged: tuple[CapabilitySpec, ...] = (),
) -> None:
    monkeypatch.setattr(
        manager,
        "_AUTHORIZED_LEGACY_FINGERPRINTS",
        _fingerprints(legacy),
    )
    monkeypatch.setattr(
        manager,
        "_KNOWN_PREEXISTING_DISABLED_FINGERPRINTS",
        _fingerprints(known_disabled),
    )
    monkeypatch.setattr(
        manager,
        "_KNOWN_PREEXISTING_UNCHANGED_FINGERPRINTS",
        frozenset(
            manager._exact_capability_fingerprint(item)
            for item in known_unchanged
        ),
    )


def test_canonical_specs_include_exact_schema_digests() -> None:
    for capability in expected_oa_capabilities():
        assert capability.input_schema_digest == schema_digest(
            capability.input_schema
        )
        assert capability.output_schema_digest == schema_digest(
            capability.output_schema
        )
        assert capability.status == "active"
        assert capability.type == "query"
        assert capability.target_system == "oa"
        assert capability.execution_identity == "user_delegated"
        assert capability.binding_required is True


def test_production_legacy_fingerprint_sets_are_exact_and_disjoint() -> None:
    assert len(manager._AUTHORIZED_LEGACY_FINGERPRINTS) == 9
    assert len(manager._KNOWN_PREEXISTING_DISABLED_FINGERPRINTS) == 1
    assert len(manager._KNOWN_PREEXISTING_UNCHANGED_FINGERPRINTS) == 2
    assert (
        manager._AUTHORIZED_LEGACY_FINGERPRINTS
        & manager._KNOWN_PREEXISTING_DISABLED_FINGERPRINTS
        == set()
    )


def test_plan_accepts_only_exact_legacy_precondition() -> None:
    legacy = _legacy_catalog()
    authorized = _fingerprints(legacy)
    ready = manager._plan_registry_management(
        legacy,
        authorized_legacy_fingerprints=authorized,
        known_disabled_fingerprints=frozenset(),
    )
    too_few = manager._plan_registry_management(
        legacy[:8],
        authorized_legacy_fingerprints=authorized,
        known_disabled_fingerprints=frozenset(),
    )
    too_many = manager._plan_registry_management(
        legacy
        + (
            legacy[0].model_copy(
                update={"capability_id": "oa.unknown_formal_capability"}
            ),
        ),
        authorized_legacy_fingerprints=authorized,
        known_disabled_fingerprints=frozenset(),
    )

    assert ready.state == "ready_legacy"
    assert ready.legacy_active_count == 9
    assert ready.insert_count == 2
    assert ready.disable_count == 9
    assert too_few.state == "precondition_failed"
    assert too_many.state == "precondition_failed"
    assert too_many.unknown_oa_count == 1


def test_plan_is_idempotent_after_exact_canonical_state() -> None:
    legacy = _legacy_catalog()
    disabled_legacy = tuple(
        item.model_copy(update={"status": "disabled"})
        for item in legacy
    )

    result = manager._plan_registry_management(
        disabled_legacy + expected_oa_capabilities(),
        authorized_legacy_fingerprints=_fingerprints(legacy),
        known_disabled_fingerprints=frozenset(),
    )

    assert result.state == "already_applied"
    assert result.canonical_found_count == 2
    assert result.canonical_valid_count == 2
    assert result.legacy_active_count == 0
    assert result.insert_count == 0
    assert result.disable_count == 0


def test_plan_rejects_partial_or_drifted_canonical_state() -> None:
    legacy = _legacy_catalog()
    authorized = _fingerprints(legacy)
    partial = legacy + (expected_oa_capabilities()[0],)
    drifted = (
        expected_oa_capabilities()[0].model_copy(
            update={"short_description": "drifted"}
        ),
        expected_oa_capabilities()[1],
    )

    assert (
        manager._plan_registry_management(
            partial,
            authorized_legacy_fingerprints=authorized,
            known_disabled_fingerprints=frozenset(),
        ).state
        == "precondition_failed"
    )
    assert (
        manager._plan_registry_management(
            drifted,
            authorized_legacy_fingerprints=authorized,
            known_disabled_fingerprints=frozenset(),
        ).state
        == "precondition_failed"
    )


def test_empty_registry_is_a_valid_two_insert_plan() -> None:
    result = manager._plan_registry_management(
        (),
        authorized_legacy_fingerprints=frozenset(),
        known_disabled_fingerprints=frozenset(),
    )

    assert result.state == "ready_empty"
    assert result.deployment_path == "empty"
    assert result.insert_count == 2
    assert result.disable_count == 0
    assert result.unknown_oa_count == 0


def test_canonical_id_with_wrong_target_fails_closed() -> None:
    wrong_target = expected_oa_capabilities()[0].model_copy(
        update={"target_system": "u8"}
    )

    result = manager._plan_registry_management(
        (wrong_target,),
        authorized_legacy_fingerprints=frozenset(),
        known_disabled_fingerprints=frozenset(),
    )

    assert result.state == "precondition_failed"
    assert result.canonical_found_count == 1
    assert result.canonical_valid_count == 0
    assert result.insert_count == 0


def test_legacy_field_drift_or_unknown_oa_row_fails_closed() -> None:
    legacy = _legacy_catalog()
    authorized = _fingerprints(legacy)
    drifted = legacy[0].model_copy(update={"owner": "unexpected-owner"})
    unknown = legacy[1].model_copy(
        update={"capability_id": "oa.unknown_capability"}
    )

    drift_result = manager._plan_registry_management(
        (drifted,) + legacy[1:],
        authorized_legacy_fingerprints=authorized,
        known_disabled_fingerprints=frozenset(),
    )
    unknown_result = manager._plan_registry_management(
        (unknown,) + legacy[1:],
        authorized_legacy_fingerprints=authorized,
        known_disabled_fingerprints=frozenset(),
    )

    assert drift_result.state == "precondition_failed"
    assert drift_result.unknown_oa_count == 1
    assert unknown_result.state == "precondition_failed"
    assert unknown_result.unknown_oa_count == 1


def test_known_preexisting_disabled_row_is_allowed_but_never_updated() -> None:
    legacy = _legacy_catalog()
    known_disabled = _known_disabled_catalog()

    result = manager._plan_registry_management(
        legacy + known_disabled,
        authorized_legacy_fingerprints=_fingerprints(legacy),
        known_disabled_fingerprints=_fingerprints(known_disabled),
    )

    assert result.state == "ready_legacy"
    assert result.disable_count == 9
    assert result.unknown_oa_count == 0


def test_known_unchanged_row_requires_its_exact_status_and_fields() -> None:
    legacy = _legacy_catalog()
    known_unchanged = _known_unchanged_catalog()
    exact = frozenset(
        manager._exact_capability_fingerprint(item)
        for item in known_unchanged
    )

    accepted = manager._plan_registry_management(
        legacy + known_unchanged,
        authorized_legacy_fingerprints=_fingerprints(legacy),
        known_disabled_fingerprints=frozenset(),
        known_unchanged_fingerprints=exact,
    )
    drifted = manager._plan_registry_management(
        legacy
        + (known_unchanged[0].model_copy(update={"status": "deprecated"}),),
        authorized_legacy_fingerprints=_fingerprints(legacy),
        known_disabled_fingerprints=frozenset(),
        known_unchanged_fingerprints=exact,
    )

    assert accepted.state == "ready_legacy"
    assert drifted.state == "precondition_failed"
    assert drifted.unknown_oa_count == 1


class _FakeMappings:
    def __init__(self, rows: Sequence[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _FakeResult:
    def __init__(
        self,
        rows: Sequence[dict[str, Any]],
        *,
        rowcount: int = -1,
    ) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self._rows)


class _FakeConnection:
    def __init__(self, catalog: tuple[CapabilitySpec, ...]) -> None:
        self.rows = [item.model_dump(mode="python") for item in catalog]
        self.operations: list[str] = []
        self.statements: list[object] = []
        self.update_rowcount_override: int | None = None

    async def execute(
        self,
        statement: object,
        parameters: Sequence[dict[str, Any]] | None = None,
    ) -> _FakeResult:
        operation = type(statement).__name__
        self.operations.append(operation)
        self.statements.append(statement)
        if isinstance(statement, sa.sql.Select):
            return _FakeResult(self.rows)
        if isinstance(statement, sa.sql.Update):
            canonical_ids = {item.capability_id for item in expected_oa_capabilities()}
            updated = 0
            for row in self.rows:
                if (
                    row["status"] == "active"
                    and row["capability_id"] not in canonical_ids
                ):
                    row["status"] = "disabled"
                    updated += 1
            return _FakeResult(
                (),
                rowcount=(
                    updated
                    if self.update_rowcount_override is None
                    else self.update_rowcount_override
                ),
            )
        if isinstance(statement, sa.sql.Insert):
            assert parameters is not None
            self.rows.extend(dict(item) for item in parameters)
            return _FakeResult(())
        pytest.fail(f"unexpected SQL operation: {operation}")


class _FakeConnectionContext:
    def __init__(
        self,
        connection: _FakeConnection,
        *,
        transactional: bool,
    ) -> None:
        self._connection = connection
        self._transactional = transactional
        self._snapshot: list[dict[str, Any]] | None = None
        self.rolled_back = False

    async def __aenter__(self) -> _FakeConnection:
        if self._transactional:
            self._snapshot = [dict(row) for row in self._connection.rows]
        return self._connection

    async def __aexit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        if _exc_type is not None and self._snapshot is not None:
            self._connection.rows = self._snapshot
            self.rolled_back = True
        return None


class _FakeEngine:
    def __init__(self, catalog: tuple[CapabilitySpec, ...]) -> None:
        self.connection = _FakeConnection(catalog)
        self.connect_count = 0
        self.begin_count = 0
        self.dispose_count = 0
        self.last_transaction: _FakeConnectionContext | None = None

    def connect(self) -> _FakeConnectionContext:
        self.connect_count += 1
        return _FakeConnectionContext(self.connection, transactional=False)

    def begin(self) -> _FakeConnectionContext:
        self.begin_count += 1
        self.last_transaction = _FakeConnectionContext(
            self.connection,
            transactional=True,
        )
        return self.last_transaction

    async def dispose(self) -> None:
        self.dispose_count += 1


def test_default_management_is_read_only_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = _legacy_catalog()
    engine = _FakeEngine(legacy)
    _install_authorized_sets(monkeypatch, legacy)
    monkeypatch.setattr(manager, "make_async_engine", lambda _url: engine)

    result = asyncio.run(
        manager._manage_registry("synthetic-database-url", apply=False)
    )

    assert result.state == "dry_run"
    assert engine.connect_count == 1
    assert engine.begin_count == 0
    assert engine.connection.operations == ["Select"]
    assert engine.dispose_count == 1
    assert result.insert_count == 2
    assert result.disable_count == 9


def test_empty_registry_dry_run_reports_two_inserts_and_zero_disables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _FakeEngine(())
    _install_authorized_sets(monkeypatch, ())
    monkeypatch.setattr(manager, "make_async_engine", lambda _url: engine)

    result = asyncio.run(
        manager._manage_registry("synthetic-database-url", apply=False)
    )

    assert result.state == "dry_run"
    assert result.deployment_path == "empty"
    assert result.insert_count == 2
    assert result.disable_count == 0
    assert engine.connection.operations == ["Select"]


def test_apply_uses_one_transaction_and_reaches_exact_postcondition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = _legacy_catalog()
    engine = _FakeEngine(legacy)
    _install_authorized_sets(monkeypatch, legacy)
    monkeypatch.setattr(manager, "make_async_engine", lambda _url: engine)

    result = asyncio.run(
        manager._manage_registry("synthetic-database-url", apply=True)
    )

    assert result.state == "applied"
    assert engine.connect_count == 0
    assert engine.begin_count == 1
    assert engine.connection.operations == ["Select", "Update", "Insert", "Select"]
    assert "Delete" not in engine.connection.operations
    assert engine.dispose_count == 1
    assert result.insert_count == 2
    assert result.disable_count == 9


def test_empty_registry_apply_then_reapply_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _FakeEngine(())
    _install_authorized_sets(monkeypatch, ())
    monkeypatch.setattr(manager, "make_async_engine", lambda _url: engine)

    first = asyncio.run(
        manager._manage_registry("synthetic-database-url", apply=True)
    )
    first_operations = list(engine.connection.operations)
    second = asyncio.run(
        manager._manage_registry("synthetic-database-url", apply=True)
    )

    assert first.state == "applied"
    assert first.deployment_path == "empty"
    assert first.insert_count == 2
    assert first.disable_count == 0
    assert first_operations == ["Select", "Insert", "Select"]
    assert second.state == "already_applied"
    assert second.insert_count == 0
    assert second.disable_count == 0
    assert engine.connection.operations == ["Select", "Insert", "Select", "Select"]


def test_apply_rejects_partial_state_without_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = _legacy_catalog()
    engine = _FakeEngine(legacy + (expected_oa_capabilities()[0],))
    _install_authorized_sets(monkeypatch, legacy)
    monkeypatch.setattr(manager, "make_async_engine", lambda _url: engine)

    result = asyncio.run(
        manager._manage_registry("synthetic-database-url", apply=True)
    )

    assert result.state == "precondition_failed"
    assert engine.connection.operations == ["Select"]


def test_manage_reads_full_registry_and_rejects_wrong_target_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_target = expected_oa_capabilities()[0].model_copy(
        update={"target_system": "u8"}
    )
    engine = _FakeEngine((wrong_target,))
    _install_authorized_sets(monkeypatch, ())
    monkeypatch.setattr(manager, "make_async_engine", lambda _url: engine)

    result = asyncio.run(
        manager._manage_registry("synthetic-database-url", apply=True)
    )

    assert result.state == "precondition_failed"
    assert engine.connection.operations == ["Select"]
    assert "WHERE" not in str(engine.connection.statements[0]).upper()


def test_update_rowcount_mismatch_rolls_back_without_inserts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = _legacy_catalog()
    engine = _FakeEngine(legacy)
    engine.connection.update_rowcount_override = 8
    _install_authorized_sets(monkeypatch, legacy)
    monkeypatch.setattr(manager, "make_async_engine", lambda _url: engine)

    with pytest.raises(manager.RegistryManagementError) as error:
        asyncio.run(
            manager._manage_registry("synthetic-database-url", apply=True)
        )

    assert error.value.code == "update_rowcount_mismatch"
    assert engine.connection.operations == ["Select", "Update"]
    assert engine.last_transaction is not None
    assert engine.last_transaction.rolled_back is True
    assert all(row["status"] == "active" for row in engine.connection.rows)


@pytest.mark.parametrize(("argv", "expected_apply"), [([], False), (["--apply"], True)])
def test_cli_requires_explicit_apply_and_never_prints_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    expected_apply: bool,
) -> None:
    private_url = "postgresql+asyncpg://private-user:private-pass@private-host/db"
    received: list[tuple[str, bool]] = []

    async def manage(database_url: str, *, apply: bool) -> manager.RegistryManagementPlan:
        received.append((database_url, apply))
        return manager.RegistryManagementPlan(
            state="applied" if apply else "dry_run",
            deployment_path="legacy",
            canonical_found_count=0 if not apply else 2,
            canonical_valid_count=0 if not apply else 2,
            legacy_active_count=9 if not apply else 0,
            unknown_oa_count=0,
            insert_count=2,
            disable_count=9,
        )

    monkeypatch.setattr(manager, "get_database_url", lambda: private_url)
    monkeypatch.setattr(manager, "_manage_registry", manage)

    result = manager.main(argv)

    output = capsys.readouterr()
    assert result == 0
    assert received == [(private_url, expected_apply)]
    assert private_url not in output.out
    assert private_url not in output.err
    assert f"official_apply_command={manager._OFFICIAL_APPLY_COMMAND}" in output.out


@pytest.mark.parametrize(
    "command",
    [
        ["uv", "run", "python", "scripts/manage_oa_capabilities.py", "--help"],
        ["uv", "run", "python", "-m", "scripts.manage_oa_capabilities", "--help"],
    ],
)
def test_help_subprocess_works_without_database_configuration(
    command: list[str],
) -> None:
    environment = dict(os.environ)
    environment.pop("DATABASE_URL", None)

    completed = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0
    assert "Official apply command" in completed.stdout
    assert manager._OFFICIAL_APPLY_COMMAND in " ".join(completed.stdout.split())
    assert "connection_failed" not in completed.stdout
    assert "connection_failed" not in completed.stderr
