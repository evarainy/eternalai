"""Dry-run-first management of exactly oa.read_overview; no schema changes."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.config import get_database_url
from app.db.session import make_async_engine
from app.event_loop import make_event_loop
from app.infra.adapters.oa.capabilities import expected_oa_capabilities
from app.infra.persistence.capability_registry.schema import capabilities
from app.infra.workflow.catalog import (
    OVERVIEW_ID,
    is_canonical_overview,
    production_workflow_capabilities,
)
from app.ports.capability_registry import CapabilitySpec


@dataclass(frozen=True)
class ManagementResult:
    state: str
    error_code: str | None = None
    insert_count: int = 0
    update_count: int = 0
    expected: int = 1
    capability_id: str = OVERVIEW_ID
    version: str = "1.0.0"


def plan_management(catalog: tuple[CapabilitySpec, ...], mode: str) -> ManagementResult:
    by_id = {item.capability_id: item for item in catalog}
    row = by_id.get(OVERVIEW_ID)
    if row is not None and not is_canonical_overview(row):
        return ManagementResult("conflict", "workflow_contract_mismatch")
    if mode == "disable":
        if row is None:
            return ManagementResult("missing", "workflow_definition_unavailable")
        return ManagementResult("inactive", update_count=int(row.status == "active"))
    if any(by_id.get(item.capability_id) != item for item in expected_oa_capabilities()):
        return ManagementResult("conflict", "workflow_dependency_invalid")
    if mode == "verify":
        if row is None or row.status != "active":
            return ManagementResult("inactive", "workflow_definition_unavailable")
        return ManagementResult("active")
    if row is None:
        return ManagementResult("missing", insert_count=1)
    if row.status == "disabled":
        return ManagementResult("inactive", update_count=1)
    return ManagementResult("active")


async def _read_catalog(
    connection: AsyncConnection,
    *,
    lock: bool,
) -> tuple[CapabilitySpec, ...]:
    ids = (OVERVIEW_ID, *(item.capability_id for item in expected_oa_capabilities()))
    statement = (
        sa.select(capabilities)
        .where(
            capabilities.c.capability_id.in_(ids),
        )
        .order_by(capabilities.c.capability_id)
    )
    if lock:
        statement = statement.with_for_update()
    rows = (await connection.execute(statement)).mappings().all()
    return tuple(CapabilitySpec.model_validate(dict(row)) for row in rows)


class _ManagementFailure(Exception):
    def __init__(self, result: ManagementResult) -> None:
        self.result = result


async def manage_registry(engine: AsyncEngine, mode: str) -> ManagementResult:
    """One transaction; locks cover the exact target and both dependencies."""
    write = mode in {"apply", "disable"}
    try:
        async with engine.begin() as connection:
            catalog = await _read_catalog(connection, lock=write)
            result = plan_management(catalog, mode)
            if result.error_code:
                raise _ManagementFailure(result)
            if not write:
                return result
            desired = production_workflow_capabilities()[0]
            if mode == "disable":
                desired = desired.model_copy(update={"status": "disabled"}, deep=True)
            if result.insert_count:
                await connection.execute(sa.insert(capabilities).values(**desired.model_dump()))
            elif result.update_count:
                await connection.execute(
                    sa.update(capabilities)
                    .where(
                        capabilities.c.capability_id == OVERVIEW_ID,
                    )
                    .values(status=desired.status)
                )
            after = await _read_catalog(connection, lock=False)
            if next((row for row in after if row.capability_id == OVERVIEW_ID), None) != desired:
                raise _ManagementFailure(ManagementResult("conflict", "workflow_contract_mismatch"))
            if mode == "apply" and plan_management(after, "verify").error_code:
                raise _ManagementFailure(
                    ManagementResult("conflict", "workflow_dependency_invalid")
                )
            return ManagementResult(
                "inactive" if mode == "disable" else "active",
                insert_count=result.insert_count,
                update_count=result.update_count,
            )
    except _ManagementFailure as exc:
        return exc.result
    except IntegrityError:
        return ManagementResult("conflict", "workflow_contract_mismatch")
    except Exception:
        return ManagementResult("unavailable", "workflow_registry_unavailable")


class _InvalidArguments(Exception):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's diagnostic includes arbitrary user-supplied argument values.
        raise _InvalidArguments from None


def _build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_mutually_exclusive_group()
    for name in ("dry-run", "verify", "apply", "disable"):
        modes.add_argument(f"--{name}", dest="mode", action="store_const", const=name)
    parser.set_defaults(mode="dry-run")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except _InvalidArguments:
        result = ManagementResult("invalid_arguments", "workflow_arguments_invalid")
        print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
        return 2

    async def run() -> ManagementResult:
        engine = make_async_engine(database_url=get_database_url())
        try:
            return await manage_registry(engine, args.mode)
        finally:
            await engine.dispose()

    try:
        with asyncio.Runner(loop_factory=make_event_loop) as runner:
            result = runner.run(run())
    except Exception:
        result = ManagementResult("unavailable", "workflow_registry_unavailable")
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return int(result.error_code is not None)


if __name__ == "__main__":
    raise SystemExit(main())
