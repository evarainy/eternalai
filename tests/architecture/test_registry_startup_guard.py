from __future__ import annotations

import asyncio
import re

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import TextClause

from app.infra.persistence.capability_registry.schema import capabilities
from app.main import app as production_app


class _RegistryStartupWriteDetected(AssertionError):
    """Raised when production startup attempts Registry DML."""


def _capability_registry_dml(statement: object) -> str | None:
    operations: tuple[tuple[type[object], str], ...] = (
        (Insert, "INSERT"),
        (Update, "UPDATE"),
        (Delete, "DELETE"),
    )
    for statement_type, operation in operations:
        if isinstance(statement, statement_type):
            table = getattr(statement, "table", None)
            if getattr(table, "name", None) == capabilities.name:
                return operation
    raw_sql: str | None = None
    if isinstance(statement, TextClause):
        raw_sql = statement.text
    elif isinstance(statement, str):
        raw_sql = statement
    if raw_sql is not None:
        normalized = " ".join(raw_sql.replace('"', "").upper().split())
        raw_patterns = (
            (r"\bINSERT\s+INTO\s+(?:[A-Z0-9_]+\.)?CAPABILITIES\b", "INSERT"),
            (r"\bUPDATE\s+(?:[A-Z0-9_]+\.)?CAPABILITIES\b", "UPDATE"),
            (r"\bDELETE\s+FROM\s+(?:[A-Z0-9_]+\.)?CAPABILITIES\b", "DELETE"),
        )
        for pattern, operation in raw_patterns:
            if re.search(pattern, normalized):
                return operation
    return None


def _install_execute_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    violations: list[str] = []

    async def isolated_execute(
        _executor: object,
        statement: object,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        operation = _capability_registry_dml(statement)
        if operation is not None:
            violations.append(operation)
            raise _RegistryStartupWriteDetected(
                f"FastAPI startup attempted {operation} on capabilities"
            )
        # Startup SQL is isolated from a real engine in this architecture test.
        # Only capabilities DML is a contract violation; unrelated statements
        # are accepted by the guard without being sent to a database.
        return object()

    async def isolated_driver_sql(
        _connection: object,
        statement: str,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        return await isolated_execute(_connection, statement)

    monkeypatch.setattr(AsyncSession, "execute", isolated_execute)
    monkeypatch.setattr(AsyncConnection, "execute", isolated_execute)
    monkeypatch.setattr(
        AsyncConnection,
        "exec_driver_sql",
        isolated_driver_sql,
    )
    return violations


async def _run_lifespan(application: FastAPI) -> None:
    async with application.router.lifespan_context(application):
        pass


def test_production_startup_does_not_write_capability_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    violations = _install_execute_guard(monkeypatch)

    asyncio.run(_run_lifespan(production_app))

    assert violations == []


def test_startup_guard_allows_unrelated_dml_without_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    violations = _install_execute_guard(monkeypatch)
    control_app = FastAPI()
    control_table = sa.table("startup_guard_control", sa.column("id"))

    async def unrelated_startup_write() -> None:
        async with AsyncSession() as session:
            await session.execute(sa.insert(control_table).values(id=1))

    control_app.router.add_event_handler("startup", unrelated_startup_write)

    asyncio.run(_run_lifespan(control_app))

    assert violations == []


@pytest.mark.parametrize(
    ("statement", "operation"),
    (
        (sa.insert(capabilities), "INSERT"),
        (sa.update(capabilities), "UPDATE"),
        (sa.delete(capabilities), "DELETE"),
    ),
)
def test_startup_guard_rejects_synthetic_capability_registry_mutant(
    monkeypatch: pytest.MonkeyPatch,
    statement: object,
    operation: str,
) -> None:
    violations = _install_execute_guard(monkeypatch)
    mutant_app = FastAPI()

    async def registry_startup_write() -> None:
        async with AsyncSession() as session:
            await session.execute(statement)

    mutant_app.router.add_event_handler("startup", registry_startup_write)

    with pytest.raises(
        _RegistryStartupWriteDetected,
        match=rf"startup attempted {operation} on capabilities",
    ):
        asyncio.run(_run_lifespan(mutant_app))

    assert violations == [operation]


@pytest.mark.parametrize(
    ("statement", "operation"),
    (
        ("INSERT INTO public.capabilities (capability_id) VALUES ('x')", "INSERT"),
        ("UPDATE capabilities SET status = 'active'", "UPDATE"),
        ("DELETE FROM capabilities WHERE capability_id = 'x'", "DELETE"),
    ),
)
def test_startup_guard_rejects_raw_driver_sql_registry_mutant(
    monkeypatch: pytest.MonkeyPatch,
    statement: str,
    operation: str,
) -> None:
    violations = _install_execute_guard(monkeypatch)
    mutant_app = FastAPI()

    async def registry_startup_write() -> None:
        await AsyncConnection.exec_driver_sql(None, statement)  # type: ignore[arg-type]

    mutant_app.router.add_event_handler("startup", registry_startup_write)

    with pytest.raises(
        _RegistryStartupWriteDetected,
        match=rf"startup attempted {operation} on capabilities",
    ):
        asyncio.run(_run_lifespan(mutant_app))

    assert violations == [operation]
