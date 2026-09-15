"""Exact SQL planning and transaction failure tests with no database I/O."""

import asyncio
from copy import deepcopy

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.infra.adapters.oa.capabilities import expected_oa_capabilities
from app.infra.workflow.catalog import OVERVIEW_ID, production_workflow_capabilities
from scripts import manage_workflow_capability as management
from tests.scripts.test_manage_oa_capabilities import (
    postgresql_registry_sandbox as postgresql_registry_sandbox,
)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return [row.model_dump() for row in self.rows.values()]


class Transaction:
    def __init__(self, engine):
        self.engine = engine

    async def __aenter__(self):
        self.before = deepcopy(self.engine.rows)
        self.engine.begins += 1
        return self.engine

    async def __aexit__(self, error_type, error, traceback):
        if error_type:
            self.engine.rows = self.before
            self.engine.rollbacks += 1
        else:
            self.engine.commits += 1


class Engine:
    """SQL-level double, not evidence of PostgreSQL locking or concurrency."""

    def __init__(self, rows):
        self.rows = {item.capability_id: item for item in rows}
        self.statements = []
        self.begins = self.commits = self.rollbacks = 0
        self.fail_after_write = False
        self.insert_conflict = False

    def begin(self):
        return Transaction(self)

    async def execute(self, statement):
        self.statements.append(statement)
        if isinstance(statement, sa.sql.Select):
            if self.fail_after_write and self.writes:
                raise OSError("synthetic-private-error")
            return Result(self.rows)
        params = statement.compile().params
        if isinstance(statement, sa.sql.dml.Insert):
            if self.insert_conflict:
                raise IntegrityError("synthetic insert", {}, Exception("synthetic conflict"))
            from app.ports.capability_registry import CapabilitySpec

            row = CapabilitySpec.model_validate(params)
            self.rows[row.capability_id] = row
        elif isinstance(statement, sa.sql.dml.Update):
            assert params["capability_id_1"] == OVERVIEW_ID
            self.rows[OVERVIEW_ID] = self.rows[OVERVIEW_ID].model_copy(
                update={"status": params["status"]},
            )
        else:
            raise AssertionError("unexpected DML")

    @property
    def writes(self):
        return [s for s in self.statements if not isinstance(s, sa.sql.Select)]


def test_dry_run_apply_verify_disable_are_exact_and_transactional() -> None:
    leaves = expected_oa_capabilities()
    canonical = production_workflow_capabilities()[0]
    engine = Engine(leaves)
    result = asyncio.run(management.manage_registry(engine, "dry-run"))
    assert (result.state, result.insert_count, result.expected) == ("missing", 1, 1)
    assert engine.writes == []
    result = asyncio.run(management.manage_registry(engine, "verify"))
    assert result.error_code == "workflow_definition_unavailable"
    assert engine.writes == []
    result = asyncio.run(management.manage_registry(engine, "apply"))
    assert (result.state, result.insert_count, result.error_code) == ("active", 1, None)
    assert engine.rows == {item.capability_id: item for item in (*leaves, canonical)}
    writes = len(engine.writes)
    for mode in ("apply", "verify", "dry-run"):
        result = asyncio.run(management.manage_registry(engine, mode))
        assert result.state == "active" and result.error_code is None
        assert len(engine.writes) == writes
    result = asyncio.run(management.manage_registry(engine, "disable"))
    assert (result.state, result.update_count) == ("inactive", 1)
    assert engine.rows[OVERVIEW_ID] == canonical.model_copy(update={"status": "disabled"})
    result = asyncio.run(management.manage_registry(engine, "disable"))
    assert result.update_count == 0
    assert asyncio.run(management.manage_registry(engine, "verify")).error_code is not None
    result = asyncio.run(management.manage_registry(engine, "dry-run"))
    assert (result.state, result.update_count) == ("inactive", 1)
    result = asyncio.run(management.manage_registry(engine, "apply"))
    assert result.update_count == 1 and engine.rows[OVERVIEW_ID] == canonical
    assert all(engine.rows[item.capability_id] == item for item in leaves)
    assert not any(isinstance(s, sa.sql.dml.Delete) for s in engine.statements)
    locking = [
        str(s.compile(dialect=postgresql.dialect()))
        for s in engine.statements
        if isinstance(s, sa.sql.Select) and s._for_update_arg is not None
    ]
    assert locking and all("FOR UPDATE" in sql and "WHERE" in sql for sql in locking)


@pytest.mark.parametrize("fault", ["draft", "deprecated", "version", "field", "dependency"])
def test_conflicting_rows_are_never_overwritten(fault) -> None:
    canonical = production_workflow_capabilities()[0]
    leaves = list(expected_oa_capabilities())
    updates = {
        "draft": {"status": "draft"},
        "deprecated": {"status": "deprecated"},
        "version": {"version": "2.0.0"},
        "field": {"binding_required": False},
    }
    if fault == "dependency":
        leaves[0] = leaves[0].model_copy(update={"status": "disabled"})
    else:
        canonical = canonical.model_copy(update=updates[fault])
    engine = Engine([*leaves, canonical])
    before = deepcopy(engine.rows)
    result = asyncio.run(management.manage_registry(engine, "apply"))
    assert result.state == "conflict" and result.error_code is not None
    assert engine.rows == before and engine.writes == []
    assert engine.rollbacks == 1 and engine.commits == 0


def test_failed_postcondition_rolls_back_insert() -> None:
    engine = Engine(expected_oa_capabilities())
    engine.fail_after_write = True
    before = deepcopy(engine.rows)
    result = asyncio.run(management.manage_registry(engine, "apply"))
    assert result.error_code == "workflow_registry_unavailable"
    assert len(engine.writes) == 1
    assert engine.rows == before
    assert engine.rollbacks == 1 and engine.commits == 0


def test_concurrent_activation_cannot_overwrite_conflicting_row() -> None:
    # Deterministically inject the unique-constraint loser, without claiming
    # this unit test proves actual cross-connection PostgreSQL serialization.
    engine = Engine(expected_oa_capabilities())
    engine.insert_conflict = True
    result = asyncio.run(management.manage_registry(engine, "apply"))
    assert result.state == "conflict" and result.error_code == "workflow_contract_mismatch"
    assert len(engine.writes) == 1
    assert OVERVIEW_ID not in engine.rows
    assert engine.rollbacks == 1 and engine.commits == 0


def test_cli_modes_are_mutually_exclusive_and_default_to_read_only() -> None:
    parser = management._build_parser()
    assert parser.parse_args([]).mode == "dry-run"
    for mode in ("dry-run", "verify", "apply", "disable"):
        assert parser.parse_args([f"--{mode}"]).mode == mode
    for args in (["--apply", "--disable"], ["--id", "oa.other"], ["--definition", "custom.py"]):
        with pytest.raises(SystemExit) as error:
            parser.parse_args(args)
        assert error.value.code == 2


# Reuse the existing isolated PostgreSQL table fixture. These tests are collected
# but not executed by the no-DB implementation-phase command.


def test_pg_workflow_management_transactions(postgresql_registry_sandbox, monkeypatch) -> None:
    from app.db.session import make_async_engine
    from tests.runtime.test_production_workflow import _run_pg

    sandbox = postgresql_registry_sandbox
    monkeypatch.setattr(management, "capabilities", sandbox.table)

    async def exercise():
        engine = make_async_engine(sandbox.database_url)
        try:
            async with engine.begin() as connection:
                for item in expected_oa_capabilities():
                    await connection.execute(sa.insert(sandbox.table).values(**item.model_dump()))
            result = await management.manage_registry(engine, "dry-run")
            assert result.insert_count == 1 and result.error_code is None
            result = await management.manage_registry(engine, "apply")
            assert result.state == "active" and result.insert_count == 1
            result = await management.manage_registry(engine, "apply")
            assert (result.insert_count, result.update_count) == (0, 0)
            assert (await management.manage_registry(engine, "verify")).error_code is None
            result = await management.manage_registry(engine, "disable")
            assert result.update_count == 1 and result.state == "inactive"
            original = management._read_catalog

            async def fail_postcondition(connection, *, lock):
                if not lock:
                    raise OSError("synthetic-postcondition-failure")
                return await original(connection, lock=lock)

            monkeypatch.setattr(management, "_read_catalog", fail_postcondition)
            result = await management.manage_registry(engine, "apply")
            assert result.error_code == "workflow_registry_unavailable"
            async with engine.connect() as connection:
                rows = await original(connection, lock=False)
            expected = (
                *expected_oa_capabilities(),
                production_workflow_capabilities()[0].model_copy(update={"status": "disabled"}),
            )
            assert {row.capability_id: row for row in rows} == {
                row.capability_id: row for row in expected
            }
        finally:
            await engine.dispose()

    _run_pg(exercise())


def test_pg_concurrent_insert_keeps_conflicting_winner(
    postgresql_registry_sandbox, monkeypatch
) -> None:
    from app.db.session import make_async_engine
    from tests.runtime.test_production_workflow import _run_pg

    sandbox = postgresql_registry_sandbox
    monkeypatch.setattr(management, "capabilities", sandbox.table)

    async def exercise():
        engine = make_async_engine(sandbox.database_url)
        original = management._read_catalog
        winner = production_workflow_capabilities()[0].model_copy(update={"version": "9.0.0"})
        reads = 0

        async def race(connection, *, lock):
            nonlocal reads
            rows = await original(connection, lock=lock)
            reads += 1
            if lock and not any(row.capability_id == OVERVIEW_ID for row in rows):
                async with engine.begin() as competing:
                    await competing.execute(sa.insert(sandbox.table).values(**winner.model_dump()))
            return rows

        try:
            async with engine.begin() as connection:
                for item in expected_oa_capabilities():
                    await connection.execute(sa.insert(sandbox.table).values(**item.model_dump()))
            monkeypatch.setattr(management, "_read_catalog", race)
            result = await management.manage_registry(engine, "apply")
            assert result.state == "conflict" and reads == 1
            async with engine.connect() as connection:
                rows = await original(connection, lock=False)
            assert next(row for row in rows if row.capability_id == OVERVIEW_ID) == winner
        finally:
            await engine.dispose()

    _run_pg(exercise())
