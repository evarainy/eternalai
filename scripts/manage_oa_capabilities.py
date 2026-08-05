"""Dry-run-first, transactional management for the two canonical OA capabilities."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.config import get_database_url
from app.db.session import make_async_engine
from app.event_loop import make_event_loop
from app.infra.persistence.capability_registry.schema import capabilities
from app.ports.capability_registry import CapabilitySpec
from scripts.smoke.capabilities import (
    REQUIRED_ACTIVE_OA_CAPABILITY_IDS,
    expected_oa_capabilities,
)

# These full-row fingerprints are the audit boundary for the one authorized cleanup.
# Evidence: the nine rows share the PostgreSQL transaction version of the two
# canonical inserts; the separately listed disabled row has an older version.
# Fingerprints cover every CapabilitySpec field with only ``status`` normalized.
_AUTHORIZED_LEGACY_FINGERPRINTS = frozenset(
    {
        "06cf128fd35c2db5a8c1c157add6a190d097bfcf3bb708aa63218b5149b42238",
        "17b42e68645c6fd9e5a906b359baf6acdf8c63564b47c8f2691eb20f1e625f25",
        "1ab1b33ddfa90d96ef0bbe60aa7dc3727d8f0ef44fa54132bf2d340a235f088b",
        "252a3c2996a57cd3c0182ca28c90cae47cc54ceca2e3af00755cbc7bf3d03357",
        "78e0934e01c48b1365c7fab608c96f8bfb94d78a14be7f1c14b51d47d5f39206",
        "b4dcefc82070255b2662b6e5c05434dbce3521c4389482928d07b9620185cb83",
        "ba9c680f443d56e96545e3cffcaf33f6f7fa327cf7b26aab31575f48671c6b08",
        "e5ea135799273959b4a104e98db13528cf6cce292643b70f60c65aab1e76690f",
        "f94f9bee2b8c637b40eb7133a5295be58cf1edcb02b588012e8a756995edd74e",
    }
)
_KNOWN_PREEXISTING_DISABLED_FINGERPRINTS = frozenset(
    {"d9205546d84abc3028266f7f753c23ac36d5c656e3019a3d0bb948f87aeb2124"}
)
_KNOWN_PREEXISTING_UNCHANGED_FINGERPRINTS = frozenset(
    {
        "19e89ed2b3c71fa8e254f1744ccca99d6d5b7e5787718130870879a2e9982dfe",
        "c495cf5daff2919bb7589cc49015af7b5388b3863aea7c8473de8a5fd3f516b1",
    }
)
_OFFICIAL_APPLY_COMMAND = (
    "uv run python -m scripts.manage_oa_capabilities --apply"
)


class _FixedParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise RegistryManagementError("invalid_arguments")


class RegistryManagementError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RegistryManagementPlan:
    state: str
    deployment_path: str
    canonical_found_count: int
    canonical_valid_count: int
    legacy_active_count: int
    unknown_oa_count: int
    insert_count: int
    disable_count: int


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        database_url = get_database_url()
        with asyncio.Runner(loop_factory=make_event_loop) as runner:
            result = runner.run(_manage_registry(database_url, apply=args.apply))
        print(f"registry_management={result.state}")
        print(f"canonical_expected={len(REQUIRED_ACTIVE_OA_CAPABILITY_IDS)}")
        print(f"canonical_found={result.canonical_found_count}")
        print(f"canonical_valid={result.canonical_valid_count}")
        print(f"legacy_active={result.legacy_active_count}")
        print(f"unknown_oa={result.unknown_oa_count}")
        print(f"planned_insert={result.insert_count}")
        print(f"planned_disable={result.disable_count}")
        print(f"registry_deployment_path={result.deployment_path}")
        print(f"official_apply_command={_OFFICIAL_APPLY_COMMAND}")
        return 0 if result.state in {"dry_run", "applied", "already_applied"} else 1
    except RegistryManagementError as exc:
        print(f"registry management failed: {exc.code}", file=sys.stderr)
        return 2
    except (IntegrityError, ValidationError):
        print("registry management failed: registry_payload_invalid", file=sys.stderr)
        return 1
    except (OperationalError, OSError, TimeoutError):
        print("registry management failed: connection_failed", file=sys.stderr)
        return 1
    except DBAPIError:
        print("registry management failed: registry_operation_failed", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError):
        print("registry management failed: configuration_failed", file=sys.stderr)
        return 1
    except Exception:
        print("registry management failed: unexpected_error", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = _FixedParser(
        description=(
            "Read-only by default; checks the two canonical OA capabilities "
            "without reading .env files."
        ),
        epilog=f"Official apply command: {_OFFICIAL_APPLY_COMMAND}",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply one strictly checked transaction; omitted means read-only",
    )
    return parser


async def _manage_registry(
    database_url: str,
    *,
    apply: bool,
) -> RegistryManagementPlan:
    engine = make_async_engine(database_url)
    try:
        if not apply:
            async with engine.connect() as connection:
                catalog = await _read_registry_catalog(connection, for_update=False)
            plan = _plan_registry_management(catalog)
            if plan.state in {"ready_empty", "ready_legacy"}:
                return RegistryManagementPlan(
                    state="dry_run",
                    deployment_path=plan.deployment_path,
                    canonical_found_count=plan.canonical_found_count,
                    canonical_valid_count=plan.canonical_valid_count,
                    legacy_active_count=plan.legacy_active_count,
                    unknown_oa_count=plan.unknown_oa_count,
                    insert_count=plan.insert_count,
                    disable_count=plan.disable_count,
                )
            return plan

        async with engine.begin() as connection:
            catalog = await _read_registry_catalog(connection, for_update=True)
            plan = _plan_registry_management(catalog)
            if plan.state == "already_applied":
                return plan
            if plan.state not in {"ready_empty", "ready_legacy"}:
                return plan

            legacy_ids = _authorized_active_legacy_ids(catalog)
            if legacy_ids:
                update_result = await connection.execute(
                    sa.update(capabilities)
                    .where(
                        capabilities.c.capability_id.in_(legacy_ids),
                        capabilities.c.status == "active",
                    )
                    .values(status="disabled")
                )
                if update_result.rowcount != plan.disable_count:
                    raise RegistryManagementError("update_rowcount_mismatch")
            await connection.execute(
                sa.insert(capabilities),
                [
                    item.model_dump(mode="python")
                    for item in expected_oa_capabilities()
                ],
            )
            updated_catalog = await _read_registry_catalog(
                connection,
                for_update=False,
            )
            postcondition = _plan_registry_management(updated_catalog)
            if postcondition.state != "already_applied":
                raise RegistryManagementError("postcondition_failed")
            return RegistryManagementPlan(
                state="applied",
                deployment_path=plan.deployment_path,
                canonical_found_count=postcondition.canonical_found_count,
                canonical_valid_count=postcondition.canonical_valid_count,
                legacy_active_count=postcondition.legacy_active_count,
                unknown_oa_count=postcondition.unknown_oa_count,
                insert_count=plan.insert_count,
                disable_count=plan.disable_count,
            )
    finally:
        try:
            await engine.dispose()
        except Exception:
            pass


async def _read_registry_catalog(
    connection: AsyncConnection,
    *,
    for_update: bool,
) -> tuple[CapabilitySpec, ...]:
    statement = sa.select(capabilities).order_by(capabilities.c.capability_id)
    if for_update:
        statement = statement.with_for_update()
    rows = (await connection.execute(statement)).mappings().all()
    return tuple(CapabilitySpec.model_validate(dict(row)) for row in rows)


def _plan_registry_management(
    catalog: tuple[CapabilitySpec, ...],
    *,
    authorized_legacy_fingerprints: frozenset[str] | None = None,
    known_disabled_fingerprints: frozenset[str] | None = None,
    known_unchanged_fingerprints: frozenset[str] | None = None,
) -> RegistryManagementPlan:
    authorized_legacy_fingerprints = (
        _AUTHORIZED_LEGACY_FINGERPRINTS
        if authorized_legacy_fingerprints is None
        else authorized_legacy_fingerprints
    )
    known_disabled_fingerprints = (
        _KNOWN_PREEXISTING_DISABLED_FINGERPRINTS
        if known_disabled_fingerprints is None
        else known_disabled_fingerprints
    )
    known_unchanged_fingerprints = (
        _KNOWN_PREEXISTING_UNCHANGED_FINGERPRINTS
        if known_unchanged_fingerprints is None
        else known_unchanged_fingerprints
    )
    expected = {item.capability_id: item for item in expected_oa_capabilities()}
    by_id = {item.capability_id: item for item in catalog}
    canonical_found = tuple(
        by_id[capability_id]
        for capability_id in REQUIRED_ACTIVE_OA_CAPABILITY_IDS
        if capability_id in by_id
    )
    canonical_valid = tuple(
        item
        for capability_id, expected_item in expected.items()
        if (item := by_id.get(capability_id)) == expected_item
    )
    noncanonical_oa = tuple(
        item
        for item in catalog
        if item.target_system == "oa"
        and item.capability_id not in REQUIRED_ACTIVE_OA_CAPABILITY_IDS
    )
    authorized_legacy = tuple(
        item
        for item in noncanonical_oa
        if _capability_fingerprint(item) in authorized_legacy_fingerprints
    )
    known_disabled = tuple(
        item
        for item in noncanonical_oa
        if _capability_fingerprint(item) in known_disabled_fingerprints
    )
    known_unchanged = tuple(
        item
        for item in noncanonical_oa
        if _exact_capability_fingerprint(item) in known_unchanged_fingerprints
    )
    unknown_oa = tuple(
        item
        for item in noncanonical_oa
        if _capability_fingerprint(item)
        not in authorized_legacy_fingerprints | known_disabled_fingerprints
        and _exact_capability_fingerprint(item)
        not in known_unchanged_fingerprints
    )
    legacy_active = tuple(
        item for item in authorized_legacy if item.status == "active"
    )
    legacy_disabled = tuple(
        item for item in authorized_legacy if item.status == "disabled"
    )
    known_disabled_valid = all(item.status == "disabled" for item in known_disabled)

    if (
        not canonical_found
        and not noncanonical_oa
    ):
        state = "ready_empty"
        deployment_path = "empty"
        insert_count = len(REQUIRED_ACTIVE_OA_CAPABILITY_IDS)
        disable_count = 0
    elif (
        not canonical_found
        and len(authorized_legacy) == len(authorized_legacy_fingerprints)
        and len(legacy_active) == len(authorized_legacy_fingerprints)
        and not legacy_disabled
        and len(known_disabled) in {0, len(known_disabled_fingerprints)}
        and known_disabled_valid
        and len(known_unchanged) in {0, len(known_unchanged_fingerprints)}
        and not unknown_oa
    ):
        state = "ready_legacy"
        deployment_path = "legacy"
        insert_count = len(REQUIRED_ACTIVE_OA_CAPABILITY_IDS)
        disable_count = len(legacy_active)
    elif (
        len(canonical_valid) == len(REQUIRED_ACTIVE_OA_CAPABILITY_IDS)
        and len(authorized_legacy) in {
            0,
            len(authorized_legacy_fingerprints),
        }
        and not legacy_active
        and len(legacy_disabled) == len(authorized_legacy)
        and len(known_disabled) in {0, len(known_disabled_fingerprints)}
        and known_disabled_valid
        and len(known_unchanged) in {0, len(known_unchanged_fingerprints)}
        and not unknown_oa
    ):
        state = "already_applied"
        deployment_path = "already"
        insert_count = 0
        disable_count = 0
    else:
        state = "precondition_failed"
        deployment_path = "invalid"
        insert_count = 0
        disable_count = 0

    return RegistryManagementPlan(
        state=state,
        deployment_path=deployment_path,
        canonical_found_count=len(canonical_found),
        canonical_valid_count=len(canonical_valid),
        legacy_active_count=len(legacy_active),
        unknown_oa_count=len(unknown_oa),
        insert_count=insert_count,
        disable_count=disable_count,
    )


def _capability_fingerprint(capability: CapabilitySpec) -> str:
    payload = capability.model_dump(mode="json")
    payload["status"] = "<managed-status>"
    return _fingerprint_payload(payload)


def _exact_capability_fingerprint(capability: CapabilitySpec) -> str:
    return _fingerprint_payload(capability.model_dump(mode="json"))


def _fingerprint_payload(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _authorized_active_legacy_ids(
    catalog: tuple[CapabilitySpec, ...],
) -> tuple[str, ...]:
    return tuple(
        item.capability_id
        for item in catalog
        if item.target_system == "oa"
        and item.status == "active"
        and _capability_fingerprint(item) in _AUTHORIZED_LEGACY_FINGERPRINTS
    )


if __name__ == "__main__":
    raise SystemExit(main())
