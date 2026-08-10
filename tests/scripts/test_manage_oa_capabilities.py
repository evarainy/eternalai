from __future__ import annotations

import asyncio
import ctypes
import json
import os
import subprocess
import uuid
from collections.abc import Coroutine, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.session import make_async_engine as real_make_async_engine
from app.event_loop import make_event_loop
from app.infra.persistence.capability_registry.schema import (
    capabilities as production_capabilities,
)
from app.ports.capability_registry import CapabilitySpec
from scripts import manage_oa_capabilities as manager
from scripts.reset_test_db import validate_test_database_url
from scripts.smoke.capabilities import expected_oa_capabilities, schema_digest


@dataclass(frozen=True, slots=True)
class _RegistrySandbox:
    database_url: str
    schema_name: str
    table: sa.Table


def _run(coroutine: Coroutine[Any, Any, Any]) -> Any:
    with asyncio.Runner(loop_factory=make_event_loop) as runner:
        return runner.run(coroutine)


@pytest.fixture
def postgresql_registry_sandbox() -> Iterator[_RegistrySandbox]:
    raw_database_url = os.environ.get("DATABASE_URL")
    if raw_database_url is None:
        raise AssertionError("DATABASE_URL must be set for PostgreSQL proof tests")
    database_url = validate_test_database_url(raw_database_url)
    schema_name = f"p2_registry_bootstrap_{uuid.uuid4().hex}"
    metadata = sa.MetaData()
    table = production_capabilities.to_metadata(metadata, schema=schema_name)

    async def create_sandbox() -> None:
        engine = real_make_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(sa.schema.CreateSchema(schema_name))
                await connection.run_sync(metadata.create_all)
        finally:
            await engine.dispose()

    async def drop_sandbox() -> None:
        assert schema_name.startswith("p2_registry_bootstrap_")
        engine = real_make_async_engine(database_url)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(metadata.drop_all)
                await connection.execute(sa.schema.DropSchema(schema_name))
        finally:
            await engine.dispose()

    _run(create_sandbox())
    try:
        yield _RegistrySandbox(
            database_url=database_url,
            schema_name=schema_name,
            table=table,
        )
    finally:
        _run(drop_sandbox())


async def _read_sandbox_rows(
    sandbox: _RegistrySandbox,
) -> tuple[dict[str, Any], ...]:
    engine = real_make_async_engine(sandbox.database_url)
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    sa.select(sandbox.table).order_by(
                        sandbox.table.c.capability_id
                    )
                )
            ).mappings().all()
        return tuple(dict(row) for row in rows)
    finally:
        await engine.dispose()


def _recording_engine_factory(
    database_url: str,
    runs: list[list[str]],
) -> AsyncEngine:
    operations: list[str] = []
    runs.append(operations)
    engine = real_make_async_engine(database_url)

    @sa.event.listens_for(engine.sync_engine, "before_execute")
    def record_operation(
        _connection: object,
        statement: object,
        _multiparams: object,
        _params: object,
        _execution_options: object,
    ) -> None:
        operations.append(type(statement).__name__)

    return engine


def _read_audit_records(audit_dir: Path) -> tuple[dict[str, Any], ...]:
    return tuple(
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(audit_dir.glob("*.json"))
    )


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


def _canonical_predecessor_catalog() -> tuple[CapabilitySpec, CapabilitySpec]:
    pending, system_messages = expected_oa_capabilities()
    return (
        pending.model_copy(
            update={
                "name": "Synthetic predecessor pending",
                "short_description": "Synthetic predecessor contract",
                "version": "1.0.0",
            }
        ),
        system_messages,
    )


def _fingerprints(catalog: tuple[CapabilitySpec, ...]) -> frozenset[str]:
    return frozenset(manager._capability_fingerprint(item) for item in catalog)


def _install_authorized_sets(
    monkeypatch: pytest.MonkeyPatch,
    legacy: tuple[CapabilitySpec, ...],
    known_disabled: tuple[CapabilitySpec, ...] = (),
    known_unchanged: tuple[CapabilitySpec, ...] = (),
    pending_predecessor: tuple[CapabilitySpec, ...] = (),
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
    monkeypatch.setattr(
        manager,
        "_PENDING_CANONICAL_PREDECESSOR_FINGERPRINTS",
        frozenset(
            manager._exact_capability_fingerprint(item)
            for item in pending_predecessor
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


def test_pending_canonical_spec_keeps_id_and_publishes_todo_business_schema() -> None:
    pending, system_messages = expected_oa_capabilities()

    assert pending.capability_id == "oa.list_pending_workflows"
    assert pending.name == "OA 待办事宜查询"
    assert pending.short_description == "查询当前 OA 用户的待办事宜列表。"
    assert pending.version == "2.0.0"
    assert set(pending.output_schema["properties"]) == {
        "workflows",
        "returned_count",
        "authoritative_count",
        "is_complete",
    }
    assert pending.output_schema["properties"]["is_complete"]["const"] is True
    assert set(pending.output_schema["$defs"]["OAPendingWorkflow"]["properties"]) == {
        "todo_id",
        "title",
        "status",
        "received_at",
        "created_at",
        "workflow_type_id",
    }
    assert system_messages.capability_id == "oa.list_system_messages"
    assert system_messages.version == "1.0.0"
    assert manager._exact_capability_fingerprint(system_messages) == (
        "f4ab443e6dbf6e487e0ac63af5ca7f2ad160d6ae3721bd189a4cb52e43837902"
    )


def test_production_legacy_fingerprint_sets_are_exact_and_disjoint() -> None:
    assert len(manager._AUTHORIZED_LEGACY_FINGERPRINTS) == 9
    assert len(manager._KNOWN_PREEXISTING_DISABLED_FINGERPRINTS) == 1
    assert len(manager._KNOWN_PREEXISTING_UNCHANGED_FINGERPRINTS) == 2
    assert (
        manager._AUTHORIZED_LEGACY_FINGERPRINTS
        & manager._KNOWN_PREEXISTING_DISABLED_FINGERPRINTS
        == set()
    )
    assert manager._PENDING_CANONICAL_PREDECESSOR_FINGERPRINTS == {
        "6e8fd8061fcfa8bff76167107cd7464c8f1486da7cb91e90aa76ff9795902a40"
    }


def test_plan_accepts_only_exact_pending_canonical_predecessor_update() -> None:
    predecessor, system_messages = _canonical_predecessor_catalog()
    predecessor_fingerprints = frozenset(
        {manager._exact_capability_fingerprint(predecessor)}
    )

    ready = manager._plan_registry_management(
        (predecessor, system_messages),
        authorized_legacy_fingerprints=frozenset(),
        known_disabled_fingerprints=frozenset(),
        known_unchanged_fingerprints=frozenset(),
        pending_predecessor_fingerprints=predecessor_fingerprints,
    )
    pending_drift = manager._plan_registry_management(
        (
            predecessor.model_copy(update={"owner": "drifted-owner"}),
            system_messages,
        ),
        authorized_legacy_fingerprints=frozenset(),
        known_disabled_fingerprints=frozenset(),
        known_unchanged_fingerprints=frozenset(),
        pending_predecessor_fingerprints=predecessor_fingerprints,
    )
    system_drift = manager._plan_registry_management(
        (
            predecessor,
            system_messages.model_copy(update={"owner": "drifted-owner"}),
        ),
        authorized_legacy_fingerprints=frozenset(),
        known_disabled_fingerprints=frozenset(),
        known_unchanged_fingerprints=frozenset(),
        pending_predecessor_fingerprints=predecessor_fingerprints,
    )
    extra = manager._plan_registry_management(
        (
            predecessor,
            system_messages,
            predecessor.model_copy(
                update={"capability_id": "oa.unexpected_extra"}
            ),
        ),
        authorized_legacy_fingerprints=frozenset(),
        known_disabled_fingerprints=frozenset(),
        known_unchanged_fingerprints=frozenset(),
        pending_predecessor_fingerprints=predecessor_fingerprints,
    )

    assert ready.state == "ready_canonical_update"
    assert ready.deployment_path == "canonical_update"
    assert ready.canonical_found_count == 2
    assert ready.canonical_valid_count == 1
    assert ready.update_count == 1
    assert ready.insert_count == 0
    assert ready.disable_count == 0
    assert pending_drift.state == "precondition_failed"
    assert system_drift.state == "precondition_failed"
    assert extra.state == "precondition_failed"
    assert extra.unknown_oa_count == 1


def test_canonical_update_allows_only_exact_preexisting_managed_residue() -> None:
    predecessor, system_messages = _canonical_predecessor_catalog()
    legacy = _legacy_catalog()
    disabled_legacy = tuple(
        item.model_copy(update={"status": "disabled"}) for item in legacy
    )
    known_disabled = _known_disabled_catalog()
    known_unchanged = _known_unchanged_catalog()

    result = manager._plan_registry_management(
        (
            predecessor,
            system_messages,
            *disabled_legacy,
            *known_disabled,
            *known_unchanged,
        ),
        authorized_legacy_fingerprints=_fingerprints(legacy),
        known_disabled_fingerprints=_fingerprints(known_disabled),
        known_unchanged_fingerprints=frozenset(
            manager._exact_capability_fingerprint(item)
            for item in known_unchanged
        ),
        pending_predecessor_fingerprints=frozenset(
            {manager._exact_capability_fingerprint(predecessor)}
        ),
    )

    assert result.state == "ready_canonical_update"
    assert result.update_count == 1
    assert result.legacy_active_count == 0
    assert result.unknown_oa_count == 0


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
            values = {
                getattr(key, "key", str(key)): value.value
                for key, value in statement._values.items()
            }
            updated = 0
            if set(values) == {"status"}:
                canonical_ids = {
                    item.capability_id for item in expected_oa_capabilities()
                }
                for row in self.rows:
                    if (
                        row["status"] == "active"
                        and row["capability_id"] not in canonical_ids
                    ):
                        row["status"] = values["status"]
                        updated += 1
            else:
                assert "capability_id" not in values
                for row in self.rows:
                    if (
                        row["capability_id"] == "oa.list_pending_workflows"
                        and row["status"] == "active"
                    ):
                        row.update(values)
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


def test_exact_canonical_predecessor_dry_run_plans_one_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    predecessor = _canonical_predecessor_catalog()
    engine = _FakeEngine(predecessor)
    _install_authorized_sets(
        monkeypatch,
        (),
        pending_predecessor=(predecessor[0],),
    )
    monkeypatch.setattr(manager, "make_async_engine", lambda _url: engine)

    result = asyncio.run(
        manager._manage_registry("synthetic-database-url", apply=False)
    )

    assert result.state == "dry_run"
    assert result.deployment_path == "canonical_update"
    assert result.canonical_found_count == 2
    assert result.canonical_valid_count == 1
    assert result.update_count == 1
    assert result.insert_count == 0
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


def test_apply_updates_only_pending_canonical_row_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    predecessor = _canonical_predecessor_catalog()
    engine = _FakeEngine(predecessor)
    _install_authorized_sets(
        monkeypatch,
        (),
        pending_predecessor=(predecessor[0],),
    )
    monkeypatch.setattr(manager, "make_async_engine", lambda _url: engine)

    result = asyncio.run(
        manager._manage_registry("synthetic-database-url", apply=True)
    )

    assert result.state == "applied"
    assert result.deployment_path == "canonical_update"
    assert result.update_count == 1
    assert result.insert_count == 0
    assert result.disable_count == 0
    assert engine.connection.operations == ["Select", "Update", "Select"]
    assert "Insert" not in engine.connection.operations
    assert "Delete" not in engine.connection.operations
    assert tuple(row["capability_id"] for row in engine.connection.rows) == (
        "oa.list_pending_workflows",
        "oa.list_system_messages",
    )
    assert tuple(
        CapabilitySpec.model_validate(row) for row in engine.connection.rows
    ) == expected_oa_capabilities()


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
    rows_after_first = tuple(
        CapabilitySpec.model_validate(dict(row))
        for row in engine.connection.rows
    )
    second = asyncio.run(
        manager._manage_registry("synthetic-database-url", apply=True)
    )
    rows_after_second = tuple(
        CapabilitySpec.model_validate(dict(row))
        for row in engine.connection.rows
    )

    assert first.state == "applied"
    assert first.deployment_path == "empty"
    assert first.insert_count == 2
    assert first.disable_count == 0
    assert first_operations == ["Select", "Insert", "Select"]
    assert second.state == "already_applied"
    assert second.insert_count == 0
    assert second.disable_count == 0
    assert second.update_count == 0
    assert rows_after_first == expected_oa_capabilities()
    assert rows_after_second == rows_after_first
    assert engine.connection.operations == ["Select", "Insert", "Select", "Select"]
    assert engine.connection.operations[len(first_operations) :] == ["Select"]


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


def test_canonical_update_rowcount_mismatch_rolls_back_predecessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    predecessor = _canonical_predecessor_catalog()
    engine = _FakeEngine(predecessor)
    engine.connection.update_rowcount_override = 0
    _install_authorized_sets(
        monkeypatch,
        (),
        pending_predecessor=(predecessor[0],),
    )
    monkeypatch.setattr(manager, "make_async_engine", lambda _url: engine)

    with pytest.raises(manager.RegistryManagementError) as error:
        asyncio.run(
            manager._manage_registry("synthetic-database-url", apply=True)
        )

    assert error.value.code == "canonical_update_rowcount_mismatch"
    assert engine.connection.operations == ["Select", "Update"]
    assert engine.last_transaction is not None
    assert engine.last_transaction.rolled_back is True
    assert tuple(
        CapabilitySpec.model_validate(row) for row in engine.connection.rows
    ) == predecessor


def test_verify_missing_capabilities_is_read_only_and_returns_three(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = _FakeEngine(())
    monkeypatch.setattr(manager, "get_database_url", lambda: "synthetic-database-url")
    monkeypatch.setattr(manager, "make_async_engine", lambda _url: engine)

    exit_code = manager.main(["--verify"])

    output = capsys.readouterr()
    assert exit_code == 3
    assert "capability_registry_preflight=missing" in output.out
    assert (
        "missing_required_capability_ids="
        "oa.list_pending_workflows,oa.list_system_messages"
    ) in output.out
    assert engine.connect_count == 1
    assert engine.begin_count == 0
    assert engine.connection.operations == ["Select"]
    assert engine.connection.rows == []


def test_verify_exact_canonical_registry_passes_without_writes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = _FakeEngine(expected_oa_capabilities())
    monkeypatch.setattr(manager, "get_database_url", lambda: "synthetic-database-url")
    monkeypatch.setattr(manager, "make_async_engine", lambda _url: engine)

    exit_code = manager.main(["--verify"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert "capability_registry_preflight=passed" in output.out
    assert "missing_required_capability_ids=none" in output.out
    assert engine.connect_count == 1
    assert engine.begin_count == 0
    assert engine.connection.operations == ["Select"]


def test_audit_reservation_failure_stops_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    database_reads: list[bool] = []

    def read_database_url() -> str:
        database_reads.append(True)
        return "synthetic-database-url"

    def fail_audit_persistence(
        _path: Path,
        _record: manager.RegistryAuditRecord,
    ) -> None:
        raise OSError("synthetic audit failure")

    monkeypatch.setattr(manager, "get_database_url", read_database_url)
    monkeypatch.setattr(manager, "_persist_audit_record", fail_audit_persistence)

    exit_code = manager.main(
        ["--apply", "--audit-dir", str(tmp_path / "audit")]
    )

    output = capsys.readouterr()
    assert exit_code == 1
    assert "registry management failed: audit_record_failed" in output.err
    assert database_reads == []


def test_audit_replacement_failure_preserves_previous_record_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "audit" / "attempt.json"
    attempt_id = "synthetic-attempt"
    initial = manager._build_audit_record(
        None,
        attempt_id=attempt_id,
        exit_code=1,
        error_code="apply_incomplete",
    )
    manager._persist_audit_record(path, initial)
    previous_text = path.read_text(encoding="utf-8")
    replacements: list[tuple[Path, Path]] = []

    def fail_replace(source: Path, destination: Path) -> None:
        replacements.append((source, destination))
        raise OSError("synthetic atomic replace failure")

    monkeypatch.setattr(manager, "_durable_replace", fail_replace)
    completed = manager._build_audit_record(
        None,
        attempt_id=attempt_id,
        exit_code=0,
        error_code=None,
    )

    with pytest.raises(OSError, match="atomic replace failure"):
        manager._persist_audit_record(path, completed)

    assert len(replacements) == 1
    assert replacements[0][1] == path
    assert path.read_text(encoding="utf-8") == previous_text


def test_audit_file_fsync_is_followed_by_durable_replace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "audit" / "attempt.json"
    path.parent.mkdir()
    manager._persist_directory_ready_marker(path.parent)
    record = manager._build_audit_record(
        None,
        attempt_id="synthetic-attempt",
        exit_code=1,
        error_code="apply_incomplete",
    )
    events: list[tuple[str, Path | None]] = []
    actual_fsync = manager.os.fsync
    actual_replace = manager.os.replace

    def recording_fsync(descriptor: int) -> None:
        actual_fsync(descriptor)
        events.append(("file_fsync", None))

    def recording_replace(source: Path, destination: Path) -> None:
        actual_replace(source, destination)
        events.append(("durable_replace", destination))

    monkeypatch.setattr(manager.os, "fsync", recording_fsync)
    monkeypatch.setattr(
        manager,
        "_durable_replace",
        recording_replace,
        raising=False,
    )

    manager._persist_audit_record(path, record)

    assert events == [
        ("file_fsync", None),
        ("durable_replace", path),
    ]
    assert json.loads(path.read_text(encoding="utf-8"))["attempt_id"] == (
        "synthetic-attempt"
    )


def test_first_audit_write_durably_creates_missing_ancestors_in_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    level1 = tmp_path / "level1"
    level2 = level1 / "level2"
    level3 = level2 / "level3"
    path = level3 / "attempt.json"
    ready_marker = level3 / ".capability-registry-bootstrap-ready"
    record = manager._build_audit_record(
        None,
        attempt_id="synthetic-attempt",
        exit_code=1,
        error_code="apply_incomplete",
    )
    destinations: list[Path] = []
    actual_replace = manager.os.replace

    def recording_directory_creation(
        missing_directories: tuple[Path, ...],
    ) -> None:
        assert missing_directories == (level3, level2, level1)
        for target in reversed(missing_directories):
            target.mkdir()
            destinations.append(target)

    def recording_durable_replace(source: Path, destination: Path) -> None:
        actual_replace(source, destination)
        destinations.append(destination)

    monkeypatch.setattr(
        manager,
        "_create_missing_directories_durably",
        recording_directory_creation,
        raising=False,
    )
    monkeypatch.setattr(
        manager,
        "_durable_replace",
        recording_durable_replace,
    )

    manager._persist_audit_record(path, record)

    assert destinations == [level1, level2, level3, ready_marker, path]
    assert json.loads(path.read_text(encoding="utf-8"))["attempt_id"] == (
        "synthetic-attempt"
    )


def test_posix_durable_replace_persists_parent_after_replace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.tmp"
    destination = tmp_path / "audit.json"
    events: list[tuple[str, Path]] = []

    def recording_replace(actual_source: Path, actual_destination: Path) -> None:
        events.append(("replace_source", Path(actual_source)))
        events.append(("replace_destination", Path(actual_destination)))

    def recording_parent_persistence(directory: Path) -> None:
        events.append(("persist_parent", directory))

    monkeypatch.setattr(manager.os, "replace", recording_replace)
    monkeypatch.setattr(
        manager,
        "_persist_parent_directory",
        recording_parent_persistence,
    )

    manager._replace_posix_and_persist_parent(source, destination)

    assert events == [
        ("replace_source", source),
        ("replace_destination", destination),
        ("persist_parent", destination.parent),
    ]


def test_posix_missing_directories_are_created_root_to_leaf_and_synced_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    level1 = tmp_path / "level1"
    level2 = level1 / "level2"
    level3 = level2 / "level3"
    events: list[tuple[str, Path]] = []
    actual_mkdir = Path.mkdir

    def recording_mkdir(directory: Path) -> None:
        actual_mkdir(directory)
        events.append(("mkdir", directory))

    def recording_parent_persistence(directory: Path) -> None:
        events.append(("persist_parent", directory))

    monkeypatch.setattr(Path, "mkdir", recording_mkdir)
    monkeypatch.setattr(
        manager,
        "_persist_parent_directory",
        recording_parent_persistence,
    )

    manager._create_posix_directories_durably((level3, level2, level1))

    assert events == [
        ("mkdir", level1),
        ("mkdir", level2),
        ("mkdir", level3),
        ("persist_parent", level2),
        ("persist_parent", level1),
        ("persist_parent", tmp_path),
    ]


def test_posix_directory_race_only_accepts_an_actual_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raced_directory = tmp_path / "raced-directory"
    raced_directory.mkdir()
    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("synthetic", encoding="utf-8")
    persisted: list[Path] = []
    monkeypatch.setattr(
        manager,
        "_persist_parent_directory",
        persisted.append,
    )

    manager._create_posix_directories_durably((raced_directory,))

    assert persisted == [tmp_path]
    with pytest.raises(FileExistsError):
        manager._create_posix_directories_durably((not_a_directory,))
    assert persisted == [tmp_path]


def test_windows_missing_directories_publish_root_to_leaf_without_replace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    level1 = tmp_path / "level1"
    level2 = level1 / "level2"
    level3 = level2 / "level3"
    publications: list[tuple[Path, Path]] = []

    def recording_publish(source: Path, destination: Path) -> None:
        assert source.parent == destination.parent
        assert source.name.startswith(f".{destination.name}.")
        source.rename(destination)
        publications.append((source, destination))

    monkeypatch.setattr(
        manager,
        "_publish_windows_directory_write_through",
        recording_publish,
    )

    manager._create_windows_directories_durably((level3, level2, level1))

    assert [destination for _source, destination in publications] == [
        level1,
        level2,
        level3,
    ]
    assert level3.is_dir()


def test_windows_directory_race_fails_before_staging_or_publish(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "raced-directory"
    target.mkdir()
    publications: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        manager,
        "_publish_windows_directory_write_through",
        lambda source, destination: publications.append((source, destination)),
    )

    with pytest.raises(FileExistsError, match="appeared during durable creation"):
        manager._create_windows_directories_durably((target,))

    assert publications == []
    assert tuple(tmp_path.glob(".raced-directory.*.tmp")) == ()


def test_posix_parent_fsync_failure_still_closes_directory_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[tuple[str, object]] = []
    expected_flags = manager.os.O_RDONLY | getattr(
        manager.os,
        "O_DIRECTORY",
        0,
    )

    def open_directory(directory: Path, flags: int) -> int:
        events.append(("open", (directory, flags)))
        return 73

    def fail_fsync(descriptor: int) -> None:
        events.append(("fsync", descriptor))
        raise OSError("synthetic directory fsync failure")

    def close_directory(descriptor: int) -> None:
        events.append(("close", descriptor))

    monkeypatch.setattr(manager.os, "open", open_directory)
    monkeypatch.setattr(manager.os, "fsync", fail_fsync)
    monkeypatch.setattr(manager.os, "close", close_directory)

    with pytest.raises(OSError, match="directory fsync failure"):
        manager._persist_parent_directory(tmp_path)

    assert events == [
        ("open", (tmp_path, expected_flags)),
        ("fsync", 73),
        ("close", 73),
    ]


def test_windows_durable_moves_use_expected_write_through_flags(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str, int]] = []

    class MoveFileEx:
        argtypes: object = None
        restype: object = None

        def __call__(self, source: str, destination: str, flags: int) -> int:
            calls.append((source, destination, flags))
            return 1

    class Kernel32:
        MoveFileExW = MoveFileEx()

    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda _name, *, use_last_error: Kernel32(),
        raising=False,
    )
    source = tmp_path / "source.tmp"
    destination = tmp_path / "audit.json"
    directory_source = tmp_path / "directory.tmp"
    directory_destination = tmp_path / "audit"

    manager._replace_windows_write_through(source, destination)
    manager._publish_windows_directory_write_through(
        directory_source,
        directory_destination,
    )

    assert calls == [
        (str(source), str(destination), 0x00000001 | 0x00000008),
        (str(directory_source), str(directory_destination), 0x00000008),
    ]


def test_windows_durable_replace_failure_is_an_os_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class MoveFileEx:
        argtypes: object = None
        restype: object = None

        def __call__(self, _source: str, _destination: str, _flags: int) -> int:
            return 0

    class Kernel32:
        MoveFileExW = MoveFileEx()

    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        lambda _name, *, use_last_error: Kernel32(),
        raising=False,
    )
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        ctypes,
        "WinError",
        lambda error_code: OSError(error_code, "synthetic MoveFileExW failure"),
        raising=False,
    )

    with pytest.raises(OSError, match="MoveFileExW failure") as exc_info:
        manager._replace_windows_write_through(
            tmp_path / "source.tmp",
            tmp_path / "audit.json",
        )

    assert exc_info.value.errno == 5


def test_initial_ancestor_durability_failure_stops_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    level1 = tmp_path / "level1"
    audit_dir = level1 / "level2" / "level3"
    destinations: list[Path] = []
    database_reads: list[bool] = []
    management_calls: list[bool] = []

    def fail_first_directory(
        missing_directories: tuple[Path, ...],
    ) -> None:
        destinations.append(missing_directories[-1])
        raise OSError("synthetic ancestor durability failure")

    def fail_final_replace(_source: Path, destination: Path) -> None:
        destinations.append(destination)
        raise OSError("synthetic final durability failure")

    def read_database_url() -> str:
        database_reads.append(True)
        return "synthetic-database-url"

    async def manage(
        _database_url: str,
        *,
        apply: bool,
        plan_observer: object,
    ) -> manager.RegistryManagementPlan:
        management_calls.append(apply)
        assert plan_observer is not None
        raise AssertionError("Registry management must not run")

    monkeypatch.setattr(
        manager,
        "_create_missing_directories_durably",
        fail_first_directory,
        raising=False,
    )
    monkeypatch.setattr(manager, "_durable_replace", fail_final_replace)
    monkeypatch.setattr(manager, "get_database_url", read_database_url)
    monkeypatch.setattr(manager, "_manage_registry", manage)

    exit_code = manager.main(
        ["--apply", "--audit-dir", str(audit_dir)]
    )

    output = capsys.readouterr()
    assert exit_code == 1
    assert "registry management failed: audit_record_failed" in output.err
    assert destinations == [level1]
    assert database_reads == []
    assert management_calls == []


def test_visible_directory_without_ready_marker_stops_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    audit_dir = tmp_path / "level1" / "level2" / "level3"
    audit_dir.mkdir(parents=True)
    database_reads: list[bool] = []

    def unexpected_database_read() -> str:
        database_reads.append(True)
        raise AssertionError("database configuration must not be read")

    monkeypatch.setattr(manager, "get_database_url", unexpected_database_read)

    exit_code = manager.main(
        ["--apply", "--audit-dir", str(audit_dir)]
    )

    output = capsys.readouterr()
    assert exit_code == 1
    assert "registry management failed: audit_record_failed" in output.err
    assert database_reads == []
    assert tuple(audit_dir.glob("*.json")) == ()


def test_invalid_binary_ready_marker_stops_with_fixed_error_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    audit_dir = tmp_path / "level1" / "level2" / "level3"
    audit_dir.mkdir(parents=True)
    marker_path = audit_dir / manager._AUDIT_DIRECTORY_READY_MARKER
    marker_path.write_bytes(b"\xff" * len(manager._AUDIT_DIRECTORY_READY_CONTENT))
    database_reads: list[bool] = []

    def unexpected_database_read() -> str:
        database_reads.append(True)
        raise AssertionError("database configuration must not be read")

    monkeypatch.setattr(manager, "get_database_url", unexpected_database_read)

    exit_code = manager.main(
        ["--apply", "--audit-dir", str(audit_dir)]
    )

    output = capsys.readouterr()
    assert exit_code == 1
    assert output.err == "registry management failed: audit_record_failed\n"
    assert database_reads == []
    assert tuple(audit_dir.glob("*.json")) == ()


def test_parent_directory_persistence_failure_stops_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    database_reads: list[bool] = []
    management_calls: list[bool] = []

    def read_database_url() -> str:
        database_reads.append(True)
        return "synthetic-database-url"

    def fail_durable_replace(_source: Path, _destination: Path) -> None:
        raise OSError("synthetic parent directory persistence failure")

    async def manage(
        _database_url: str,
        *,
        apply: bool,
        plan_observer: object,
    ) -> manager.RegistryManagementPlan:
        management_calls.append(apply)
        assert plan_observer is not None
        return manager.RegistryManagementPlan(
            state="already_applied",
            deployment_path="canonical",
            canonical_found_count=2,
            canonical_valid_count=2,
            legacy_active_count=0,
            unknown_oa_count=0,
            insert_count=0,
            disable_count=0,
        )

    monkeypatch.setattr(manager, "get_database_url", read_database_url)
    monkeypatch.setattr(manager, "_manage_registry", manage)
    monkeypatch.setattr(
        manager,
        "_durable_replace",
        fail_durable_replace,
        raising=False,
    )

    exit_code = manager.main(
        ["--apply", "--audit-dir", str(tmp_path / "audit")]
    )

    output = capsys.readouterr()
    assert exit_code == 1
    assert "registry management failed: audit_record_failed" in output.err
    assert database_reads == []
    assert management_calls == []


def test_plan_audit_failure_rolls_back_before_registry_dml(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    audit_dir = tmp_path / "audit"
    engine = _FakeEngine(())
    actual_persist = manager._persist_audit_record
    persistence_count = 0

    def fail_plan_checkpoint(
        path: Path,
        record: manager.RegistryAuditRecord,
    ) -> None:
        nonlocal persistence_count
        persistence_count += 1
        if persistence_count == 2:
            raise OSError("synthetic plan audit failure")
        actual_persist(path, record)

    monkeypatch.setattr(manager, "get_database_url", lambda: "synthetic-url")
    monkeypatch.setattr(manager, "make_async_engine", lambda _url: engine)
    monkeypatch.setattr(
        manager,
        "_persist_audit_record",
        fail_plan_checkpoint,
    )

    exit_code = manager.main(
        ["--apply", "--audit-dir", str(audit_dir)]
    )

    output = capsys.readouterr()
    (audit,) = _read_audit_records(audit_dir)
    assert exit_code == 1
    assert "registry management failed: audit_record_failed" in output.err
    assert persistence_count == 3
    assert engine.connection.operations == ["Select"]
    assert engine.connection.rows == []
    assert audit["plan_state"] == "ready_empty"
    assert audit["planned_insert_count"] == 2
    assert audit["final_result"] == "failure"
    assert audit["error_code"] == "audit_record_failed"


def test_final_audit_failure_preserves_one_fail_closed_record(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    audit_dir = tmp_path / "audit"
    actual_persist = manager._persist_audit_record
    persistence_count = 0

    def fail_final_persistence(
        path: Path,
        record: manager.RegistryAuditRecord,
    ) -> None:
        nonlocal persistence_count
        persistence_count += 1
        if persistence_count == 3:
            raise OSError("synthetic final audit failure")
        actual_persist(path, record)

    async def manage(
        _database_url: str,
        *,
        apply: bool,
        plan_observer: object,
    ) -> manager.RegistryManagementPlan:
        assert apply is True
        plan = manager.RegistryManagementPlan(
            state="applied",
            deployment_path="empty",
            canonical_found_count=2,
            canonical_valid_count=2,
            legacy_active_count=0,
            unknown_oa_count=0,
            insert_count=2,
            disable_count=0,
            plan_state="ready_empty",
        )
        assert callable(plan_observer)
        plan_observer(plan)
        return plan

    monkeypatch.setattr(manager, "get_database_url", lambda: "synthetic-url")
    monkeypatch.setattr(manager, "_manage_registry", manage)
    monkeypatch.setattr(
        manager,
        "_persist_audit_record",
        fail_final_persistence,
    )

    exit_code = manager.main(
        ["--apply", "--audit-dir", str(audit_dir)]
    )

    output = capsys.readouterr()
    (audit,) = _read_audit_records(audit_dir)
    assert exit_code == 1
    assert "registry management failed: audit_record_failed" in output.err
    assert persistence_count == 3
    assert audit["plan_state"] == "ready_empty"
    assert audit["final_result"] == "failure"
    assert audit["exit_code"] == 1
    assert audit["error_code"] == "apply_incomplete"


def test_failed_apply_persists_safe_audit_and_returns_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    private_url = "postgresql+asyncpg://private-user:private-pass@private-host/db"
    audit_dir = tmp_path / "failed-registry-audit"
    plan = manager.RegistryManagementPlan(
        state="ready_empty",
        deployment_path="empty",
        canonical_found_count=0,
        canonical_valid_count=0,
        legacy_active_count=0,
        unknown_oa_count=0,
        insert_count=2,
        disable_count=0,
    )

    async def fail_management(
        _database_url: str,
        *,
        apply: bool,
        plan_observer: object,
    ) -> manager.RegistryManagementPlan:
        assert apply is True
        assert plan_observer is not None
        raise manager.RegistryManagementError(
            "postcondition_failed",
            plan=plan,
        )

    monkeypatch.setattr(manager, "get_database_url", lambda: private_url)
    monkeypatch.setattr(manager, "_manage_registry", fail_management)

    exit_code = manager.main(
        ["--apply", "--audit-dir", str(audit_dir)]
    )

    output = capsys.readouterr()
    (audit,) = _read_audit_records(audit_dir)
    audit_text = json.dumps(audit)
    assert exit_code == 2
    assert "registry management failed: postcondition_failed" in output.err
    assert audit["attempt_id"]
    assert audit["plan_state"] == "ready_empty"
    assert audit["deployment_path"] == "empty"
    assert audit["planned_insert_count"] == 2
    assert audit["planned_disable_count"] == 0
    assert audit["planned_update_count"] == 0
    assert audit["final_result"] == "failure"
    assert audit["exit_code"] == 2
    assert audit["error_code"] == "postcondition_failed"
    for sensitive_value in (
        private_url,
        "private-user",
        "private-pass",
        "private-host",
    ):
        assert sensitive_value not in audit_text


def test_postgresql_apply_is_idempotent_field_by_field(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    postgresql_registry_sandbox: _RegistrySandbox,
) -> None:
    sandbox = postgresql_registry_sandbox
    runs: list[list[str]] = []
    audit_dir = tmp_path / "idempotent-audit"
    monkeypatch.setattr(manager, "capabilities", sandbox.table)
    monkeypatch.setattr(manager, "get_database_url", lambda: sandbox.database_url)
    monkeypatch.setattr(
        manager,
        "make_async_engine",
        lambda _url: _recording_engine_factory(sandbox.database_url, runs),
    )

    first_exit_code = manager.main(
        ["--apply", "--audit-dir", str(audit_dir)]
    )
    first_output = capsys.readouterr()
    rows_after_first = _run(_read_sandbox_rows(sandbox))
    second_exit_code = manager.main(
        ["--apply", "--audit-dir", str(audit_dir)]
    )
    second_output = capsys.readouterr()
    rows_after_second = _run(_read_sandbox_rows(sandbox))
    expected_rows = tuple(
        item.model_dump(mode="python") for item in expected_oa_capabilities()
    )

    assert first_exit_code == 0
    assert "registry_management=applied" in first_output.out
    assert second_exit_code == 0
    assert "registry_management=already_applied" in second_output.out
    assert "planned_insert=0" in second_output.out
    assert "planned_disable=0" in second_output.out
    assert "planned_update=0" in second_output.out
    assert rows_after_first == expected_rows
    assert rows_after_second == rows_after_first
    assert runs == [["Select", "Insert", "Select"], ["Select"]]
    audit_records = _read_audit_records(audit_dir)
    assert len(audit_records) == 2
    assert tuple(record["plan_state"] for record in audit_records) == (
        "ready_empty",
        "already_applied",
    )
    assert all(record["final_result"] == "success" for record in audit_records)


def test_postgresql_mid_transaction_failure_rolls_back_and_returns_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    postgresql_registry_sandbox: _RegistrySandbox,
) -> None:
    sandbox = postgresql_registry_sandbox
    runs: list[list[str]] = []
    audit_dir = tmp_path / "rollback-audit"
    monkeypatch.setattr(manager, "capabilities", sandbox.table)
    monkeypatch.setattr(manager, "get_database_url", lambda: sandbox.database_url)
    monkeypatch.setattr(
        manager,
        "make_async_engine",
        lambda _url: _recording_engine_factory(sandbox.database_url, runs),
    )
    original_read = manager._read_registry_catalog
    read_count = 0

    async def fail_after_insert(
        connection: AsyncConnection,
        *,
        for_update: bool,
    ) -> tuple[CapabilitySpec, ...]:
        nonlocal read_count
        read_count += 1
        if read_count == 2:
            raise manager.RegistryManagementError(
                "injected_mid_transaction_failure"
            )
        return await original_read(connection, for_update=for_update)

    monkeypatch.setattr(manager, "_read_registry_catalog", fail_after_insert)

    exit_code = manager.main(
        ["--apply", "--audit-dir", str(audit_dir)]
    )
    output = capsys.readouterr()
    remaining_rows = _run(_read_sandbox_rows(sandbox))

    assert exit_code == 2
    assert "injected_mid_transaction_failure" in output.err
    assert runs == [["Select", "Insert"]]
    assert remaining_rows == ()
    (audit,) = _read_audit_records(audit_dir)
    assert audit["attempt_id"]
    assert audit["plan_state"] == "ready_empty"
    assert audit["deployment_path"] == "empty"
    assert audit["canonical_found_count"] == 0
    assert audit["canonical_valid_count"] == 0
    assert audit["legacy_active_count"] == 0
    assert audit["unknown_oa_count"] == 0
    assert audit["planned_insert_count"] == 2
    assert audit["planned_disable_count"] == 0
    assert audit["planned_update_count"] == 0
    assert audit["final_result"] == "failure"
    assert audit["exit_code"] == 2
    assert audit["error_code"] == "injected_mid_transaction_failure"


@pytest.mark.parametrize("expected_apply", [False, True])
def test_cli_requires_explicit_apply_and_never_prints_database_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    expected_apply: bool,
    tmp_path: Path,
) -> None:
    private_url = "postgresql+asyncpg://private-user:private-pass@private-host/db"
    received: list[tuple[str, bool]] = []
    audit_dir = tmp_path / "registry-audit"
    argv = (
        ["--apply", "--audit-dir", str(audit_dir)]
        if expected_apply
        else []
    )

    async def manage(
        database_url: str,
        *,
        apply: bool,
        plan_observer: object,
    ) -> manager.RegistryManagementPlan:
        received.append((database_url, apply))
        plan = manager.RegistryManagementPlan(
            state="applied" if apply else "dry_run",
            deployment_path="legacy",
            canonical_found_count=0 if not apply else 2,
            canonical_valid_count=0 if not apply else 2,
            legacy_active_count=9 if not apply else 0,
            unknown_oa_count=0,
            insert_count=2,
            disable_count=9,
            plan_state="ready_legacy",
        )
        if callable(plan_observer):
            plan_observer(plan)
        return plan

    monkeypatch.setattr(manager, "get_database_url", lambda: private_url)
    monkeypatch.setattr(manager, "_manage_registry", manage)

    result = manager.main(argv)

    output = capsys.readouterr()
    assert result == 0
    assert received == [(private_url, expected_apply)]
    assert private_url not in output.out
    assert private_url not in output.err
    assert "planned_update=0" in output.out
    assert f"official_apply_command={manager._OFFICIAL_APPLY_COMMAND}" in output.out
    if expected_apply:
        (audit,) = _read_audit_records(audit_dir)
        audit_text = json.dumps(audit)
        assert audit["attempt_id"]
        assert audit["plan_state"] == "ready_legacy"
        assert audit["deployment_path"] == "legacy"
        assert audit["final_result"] == "success"
        assert audit["exit_code"] == 0
        assert audit["error_code"] is None
        for sensitive_value in (
            private_url,
            "private-user",
            "private-pass",
            "private-host",
        ):
            assert sensitive_value not in audit_text
    else:
        assert not audit_dir.exists()


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
