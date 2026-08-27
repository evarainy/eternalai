"""Dry-run-first, transactional management for the two canonical OA capabilities."""

from __future__ import annotations

import argparse
import asyncio
import errno
import hashlib
import json
import os
import stat
import sys
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.config import get_database_url
from app.db.session import make_async_engine
from app.event_loop import make_event_loop
from app.infra.persistence.capability_registry.schema import capabilities
from app.ports.capability_registry import CapabilitySpec
from scripts.smoke.capabilities import (
    OA_CAPABILITY_CONTEXT_PROBES,
    REQUIRED_ACTIVE_OA_CAPABILITY_IDS,
    OARegistryClassification,
    classify_oa_registry,
    expected_oa_capabilities,
)

# P2-CAPABILITY-AUTOMATION-LEVEL-001 之前 CapabilitySpec 的完整字段集。
# 上面四组指纹常量是对当时生产行的观测记录，只能用当时的字段集比对。
_LEGACY_FINGERPRINT_FIELDS: frozenset[str] = frozenset(
    {
        "capability_id",
        "name",
        "type",
        "intent_tags",
        "input_schema",
        "output_schema",
        "input_schema_digest",
        "output_schema_digest",
        "risk_level",
        "owner",
        "version",
        "status",
        "short_description",
        "target_system",
        "execution_identity",
        "binding_required",
        "policy_digest",
    }
)

# These historical fingerprints are the audit boundary for the one authorized
# cleanup. Evidence: the nine rows share the PostgreSQL transaction version of
# the two canonical inserts; the separately listed disabled row has an older
# version. They cover every CapabilitySpec field that existed when observed,
# with only ``status`` normalized where the corresponding family requires it.
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
_PENDING_CANONICAL_PREDECESSOR_FINGERPRINTS = frozenset(
    {
        # Exact P2-SMOKE-AUTH-DIAG-001 canonical row before the D1 in-place
        # data-source and output-contract correction. No normalized fields.
        "6e8fd8061fcfa8bff76167107cd7464c8f1486da7cb91e90aa76ff9795902a40",
    }
)
_OFFICIAL_APPLY_COMMAND = (
    "uv run python -m scripts.manage_oa_capabilities --apply"
)
_OFFICIAL_VERIFY_COMMAND = (
    "uv run python -m scripts.manage_oa_capabilities --verify"
)
_DEFAULT_AUDIT_DIR = (
    Path.home()
    / ".eternalai"
    / "audit"
    / "capability-registry-bootstrap"
)
_AUDIT_DIRECTORY_READY_MARKER = ".capability-registry-bootstrap-ready"
_AUDIT_DIRECTORY_READY_CONTENT = b"capability-registry-bootstrap-v1\n"
_VERIFY_FAILED_EXIT_CODE = 3


class _FixedParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise RegistryManagementError("invalid_arguments")


class RegistryManagementError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        plan: RegistryManagementPlan | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.plan = plan


class RegistryManagementContextError(RuntimeError):
    def __init__(
        self,
        plan: RegistryManagementPlan,
        cause: Exception,
    ) -> None:
        super().__init__("registry_management_context")
        self.plan = plan
        self.cause = cause


class RegistryAuditError(RuntimeError):
    """Raised when a plan checkpoint cannot be persisted before Registry DML."""


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
    update_count: int = 0
    plan_state: str | None = None


@dataclass(frozen=True, slots=True)
class RegistryAuditRecord:
    attempt_id: str
    timestamp_utc: str
    plan_state: str | None
    deployment_path: str | None
    canonical_expected_count: int
    canonical_found_count: int | None
    canonical_valid_count: int | None
    legacy_active_count: int | None
    unknown_oa_count: int | None
    planned_insert_count: int | None
    planned_disable_count: int | None
    planned_update_count: int | None
    final_result: str
    exit_code: int
    error_code: str | None


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
        if args.audit_dir is not None and not args.apply:
            raise RegistryManagementError("invalid_arguments")
    except RegistryManagementError as exc:
        print(f"registry management failed: {exc.code}", file=sys.stderr)
        return 2

    audit_dir = (
        _DEFAULT_AUDIT_DIR
        if args.audit_dir is None
        else args.audit_dir.expanduser()
    )
    audit_attempt_id: str | None = None
    audit_path: Path | None = None
    plan_observer: Callable[[RegistryManagementPlan], None] | None = None
    if args.apply:
        audit_attempt_id = uuid.uuid4().hex
        audit_path = _audit_record_path(audit_dir, audit_attempt_id)
        try:
            _persist_audit_record(
                audit_path,
                _build_audit_record(
                    None,
                    attempt_id=audit_attempt_id,
                    exit_code=1,
                    error_code="apply_incomplete",
                ),
            )
        except OSError:
            print("registry management failed: audit_record_failed", file=sys.stderr)
            return 1

        def persist_plan_checkpoint(plan: RegistryManagementPlan) -> None:
            assert audit_path is not None
            assert audit_attempt_id is not None
            try:
                _persist_audit_record(
                    audit_path,
                    _build_audit_record(
                        plan,
                        attempt_id=audit_attempt_id,
                        exit_code=1,
                        error_code="apply_incomplete",
                    ),
                )
            except OSError as exc:
                raise RegistryAuditError("audit_record_failed") from exc

        plan_observer = persist_plan_checkpoint

    result: RegistryManagementPlan | None = None
    verification: OARegistryClassification | None = None
    error_code: str | None = None
    failure_message: str | None = None
    try:
        database_url = get_database_url()
        with asyncio.Runner(loop_factory=make_event_loop) as runner:
            if args.verify:
                verification = runner.run(_verify_registry(database_url))
            else:
                result = runner.run(
                    _manage_registry(
                        database_url,
                        apply=args.apply,
                        plan_observer=plan_observer,
                    )
                )
        if verification is not None:
            exit_code = 0 if verification.state == "passed" else _VERIFY_FAILED_EXIT_CODE
            error_code = (
                None
                if exit_code == 0
                else f"registry_preflight_{verification.state}"
            )
        else:
            assert result is not None
            exit_code = (
                0
                if result.state in {"dry_run", "applied", "already_applied"}
                else 1
            )
            error_code = None if exit_code == 0 else result.state
    except Exception as exc:
        failure = exc.cause if isinstance(exc, RegistryManagementContextError) else exc
        if isinstance(exc, RegistryManagementContextError):
            result = exc.plan
        elif isinstance(exc, RegistryManagementError):
            result = exc.plan
        exit_code, error_code = _classify_failure(failure)
        failure_message = f"registry management failed: {error_code}"

    if audit_attempt_id is not None and audit_path is not None:
        audit_record = _build_audit_record(
            result,
            attempt_id=audit_attempt_id,
            exit_code=exit_code,
            error_code=error_code,
        )
        try:
            _persist_audit_record(audit_path, audit_record)
        except OSError:
            if failure_message is not None:
                print(failure_message, file=sys.stderr)
            print("registry management failed: audit_record_failed", file=sys.stderr)
            return 1
        print(f"registry_audit_path={audit_path}")

    if failure_message is not None:
        print(failure_message, file=sys.stderr)
    elif verification is not None:
        _print_verification(verification)
    elif result is not None:
        _print_management_result(result)
    return exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = _FixedParser(
        description=(
            "Read-only by default; checks the two canonical OA capabilities "
            "without reading .env files."
        ),
        epilog=f"Official apply command: {_OFFICIAL_APPLY_COMMAND}",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="apply one strictly checked transaction; omitted means read-only",
    )
    mode.add_argument(
        "--verify",
        action="store_true",
        help="fail unless the required canonical capabilities are deployment-ready",
    )
    parser.add_argument(
        "--audit-dir",
        type=Path,
        help="override the per-apply JSON audit directory for --apply",
    )
    return parser


def _build_audit_record(
    plan: RegistryManagementPlan | None,
    *,
    attempt_id: str,
    exit_code: int,
    error_code: str | None,
) -> RegistryAuditRecord:
    plan_state = None
    if plan is not None:
        plan_state = plan.state if plan.plan_state is None else plan.plan_state
    return RegistryAuditRecord(
        attempt_id=attempt_id,
        timestamp_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        plan_state=plan_state,
        deployment_path=None if plan is None else plan.deployment_path,
        canonical_expected_count=len(REQUIRED_ACTIVE_OA_CAPABILITY_IDS),
        canonical_found_count=(
            None if plan is None else plan.canonical_found_count
        ),
        canonical_valid_count=(
            None if plan is None else plan.canonical_valid_count
        ),
        legacy_active_count=None if plan is None else plan.legacy_active_count,
        unknown_oa_count=None if plan is None else plan.unknown_oa_count,
        planned_insert_count=None if plan is None else plan.insert_count,
        planned_disable_count=None if plan is None else plan.disable_count,
        planned_update_count=None if plan is None else plan.update_count,
        final_result="success" if exit_code == 0 else "failure",
        exit_code=exit_code,
        error_code=error_code,
    )


def _audit_record_path(audit_dir: Path, attempt_id: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return audit_dir / f"{timestamp}_{attempt_id}.json"


def _classify_failure(error: Exception) -> tuple[int, str]:
    if isinstance(error, RegistryManagementError):
        return 2, error.code
    if isinstance(error, RegistryAuditError):
        return 1, "audit_record_failed"
    if isinstance(error, (IntegrityError, ValidationError)):
        return 1, "registry_payload_invalid"
    if isinstance(error, (OperationalError, OSError, TimeoutError)):
        return 1, "connection_failed"
    if isinstance(error, DBAPIError):
        return 1, "registry_operation_failed"
    if isinstance(error, (RuntimeError, ValueError)):
        return 1, "configuration_failed"
    return 1, "unexpected_error"


def _persist_audit_record(path: Path, record: RegistryAuditRecord) -> None:
    _ensure_durable_directory(path.parent)
    temporary_path = path.with_name(
        f".{path.name}.{uuid.uuid4().hex}.tmp"
    )
    with temporary_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(
                asdict(record),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())
    _durable_replace(temporary_path, path)


def _ensure_durable_directory(directory: Path) -> None:
    missing_directories: list[Path] = []
    candidate = directory
    while True:
        try:
            metadata = candidate.stat()
        except FileNotFoundError:
            if candidate.is_symlink():
                raise FileExistsError(
                    f"audit directory path is a broken symlink: {candidate}"
                ) from None
            missing_directories.append(candidate)
            parent = candidate.parent
            if parent == candidate:
                raise FileNotFoundError(
                    f"no existing ancestor for audit directory: {directory}"
                ) from None
            candidate = parent
            continue
        if not stat.S_ISDIR(metadata.st_mode):
            raise NotADirectoryError(
                f"audit directory ancestor is not a directory: {candidate}"
            )
        break

    if missing_directories:
        _create_missing_directories_durably(tuple(missing_directories))
        _persist_directory_ready_marker(directory)
        return

    _require_directory_ready_marker(directory)


def _persist_directory_ready_marker(directory: Path) -> None:
    marker_path = directory / _AUDIT_DIRECTORY_READY_MARKER
    temporary_path = marker_path.with_name(
        f".{marker_path.name}.{uuid.uuid4().hex}.tmp"
    )
    with temporary_path.open("xb") as stream:
        stream.write(_AUDIT_DIRECTORY_READY_CONTENT)
        stream.flush()
        os.fsync(stream.fileno())
    _durable_replace(temporary_path, marker_path)


def _require_directory_ready_marker(directory: Path) -> None:
    marker_path = directory / _AUDIT_DIRECTORY_READY_MARKER
    try:
        metadata = marker_path.lstat()
    except FileNotFoundError:
        raise OSError(
            errno.ENOENT,
            "existing audit directory has no durable initialization marker",
        ) from None
    if not stat.S_ISREG(metadata.st_mode) or marker_path.is_symlink():
        raise OSError(
            errno.EINVAL,
            "audit directory initialization marker is not a regular file",
        )
    expected_size = len(_AUDIT_DIRECTORY_READY_CONTENT)
    if metadata.st_size != expected_size:
        raise OSError(
            errno.EINVAL,
            "audit directory initialization marker is invalid",
        )
    with marker_path.open("rb") as stream:
        content = stream.read(expected_size + 1)
    if content != _AUDIT_DIRECTORY_READY_CONTENT:
        raise OSError(
            errno.EINVAL,
            "audit directory initialization marker is invalid",
        )


def _create_missing_directories_durably(
    missing_directories: tuple[Path, ...],
) -> None:
    if os.name == "posix":
        _create_posix_directories_durably(missing_directories)
        return
    if os.name == "nt":
        _create_windows_directories_durably(missing_directories)
        return
    raise OSError(
        errno.ENOTSUP,
        f"durable audit directory creation unsupported on os.name={os.name!r}",
    )


def _create_posix_directories_durably(
    missing_directories: tuple[Path, ...],
) -> None:
    for target in reversed(missing_directories):
        try:
            target.mkdir()
        except FileExistsError:
            if not target.is_dir():
                raise

    for child in missing_directories:
        _persist_parent_directory(child.parent)


def _create_windows_directories_durably(
    missing_directories: tuple[Path, ...],
) -> None:
    for target in reversed(missing_directories):
        if target.exists() or target.is_symlink():
            raise FileExistsError(
                f"audit directory appeared during durable creation: {target}"
            )
        temporary_directory = target.with_name(
            f".{target.name}.{uuid.uuid4().hex}.tmp"
        )
        temporary_directory.mkdir()
        _publish_windows_directory_write_through(temporary_directory, target)


def _durable_replace(source: Path, destination: Path) -> None:
    if os.name == "nt":
        _replace_windows_write_through(source, destination)
        return
    if os.name != "posix":
        raise OSError(
            errno.ENOTSUP,
            f"durable audit replacement unsupported on os.name={os.name!r}",
        )

    _replace_posix_and_persist_parent(source, destination)


def _replace_posix_and_persist_parent(
    source: Path,
    destination: Path,
) -> None:
    os.replace(source, destination)
    _persist_parent_directory(destination.parent)


def _persist_parent_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_windows_write_through(source: Path, destination: Path) -> None:
    _move_windows_write_through(
        source,
        destination,
        replace_existing=True,
    )


def _publish_windows_directory_write_through(
    source: Path,
    destination: Path,
) -> None:
    _move_windows_write_through(
        source,
        destination,
        replace_existing=False,
    )


def _move_windows_write_through(
    source: Path,
    destination: Path,
    *,
    replace_existing: bool,
) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file_ex = kernel32.MoveFileExW
    move_file_ex.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
    ]
    move_file_ex.restype = wintypes.BOOL

    replace_existing_flag = 0x00000001
    write_through = 0x00000008
    flags = write_through
    if replace_existing:
        flags |= replace_existing_flag
    if not move_file_ex(
        str(source),
        str(destination),
        flags,
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def _print_management_result(result: RegistryManagementPlan) -> None:
    print(f"registry_management={result.state}")
    print(f"canonical_expected={len(REQUIRED_ACTIVE_OA_CAPABILITY_IDS)}")
    print(f"canonical_found={result.canonical_found_count}")
    print(f"canonical_valid={result.canonical_valid_count}")
    print(f"legacy_active={result.legacy_active_count}")
    print(f"unknown_oa={result.unknown_oa_count}")
    print(f"planned_insert={result.insert_count}")
    print(f"planned_disable={result.disable_count}")
    print(f"planned_update={result.update_count}")
    print(f"registry_deployment_path={result.deployment_path}")
    print(f"official_apply_command={_OFFICIAL_APPLY_COMMAND}")


def _print_verification(result: OARegistryClassification) -> None:
    print(f"capability_registry_preflight={result.state}")
    print(f"required_capabilities_expected={len(REQUIRED_ACTIVE_OA_CAPABILITY_IDS)}")
    print(f"required_capabilities_found={result.found_count}")
    print(f"required_capabilities_valid={result.valid_count}")
    print(
        "missing_required_capability_ids="
        + _render_capability_ids(result.missing_capability_ids)
    )
    print(
        "inactive_required_capability_ids="
        + _render_capability_ids(result.inactive_capability_ids)
    )
    print(
        "contract_mismatch_capability_ids="
        + _render_capability_ids(result.contract_mismatch_capability_ids)
    )
    print(
        "unexpected_active_oa_capability_ids="
        + _render_capability_ids(result.unexpected_active_capability_ids)
    )
    print(f"active_capabilities_total={result.active_total_count}")
    print(f"intent_context_probes_expected={len(OA_CAPABILITY_CONTEXT_PROBES)}")
    print(f"intent_context_probes_visible={result.visible_probe_count}")
    print(f"official_verify_command={_OFFICIAL_VERIFY_COMMAND}")


def _render_capability_ids(capability_ids: tuple[str, ...]) -> str:
    return ",".join(capability_ids) if capability_ids else "none"


async def _verify_registry(database_url: str) -> OARegistryClassification:
    engine = make_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            catalog = await _read_registry_catalog(connection, for_update=False)
        return classify_oa_registry(catalog)
    finally:
        try:
            await engine.dispose()
        except Exception:
            pass


async def _manage_registry(
    database_url: str,
    *,
    apply: bool,
    plan_observer: Callable[[RegistryManagementPlan], None] | None = None,
) -> RegistryManagementPlan:
    engine = make_async_engine(database_url)
    try:
        if not apply:
            async with engine.connect() as connection:
                catalog = await _read_registry_catalog(connection, for_update=False)
            plan = _plan_registry_management(catalog)
            if plan.state in {
                "ready_canonical_update",
                "ready_empty",
                "ready_legacy",
            }:
                return RegistryManagementPlan(
                    state="dry_run",
                    deployment_path=plan.deployment_path,
                    canonical_found_count=plan.canonical_found_count,
                    canonical_valid_count=plan.canonical_valid_count,
                    legacy_active_count=plan.legacy_active_count,
                    unknown_oa_count=plan.unknown_oa_count,
                    insert_count=plan.insert_count,
                    disable_count=plan.disable_count,
                    update_count=plan.update_count,
                    plan_state=plan.state,
                )
            return plan

        return await _apply_registry(engine, plan_observer=plan_observer)
    finally:
        try:
            await engine.dispose()
        except Exception:
            pass


async def _apply_registry(
    engine: AsyncEngine,
    *,
    plan_observer: Callable[[RegistryManagementPlan], None] | None,
) -> RegistryManagementPlan:
    plan: RegistryManagementPlan | None = None
    try:
        async with engine.begin() as connection:
            catalog = await _read_registry_catalog(connection, for_update=True)
            plan = _plan_registry_management(catalog)
            if plan_observer is not None:
                plan_observer(plan)
            if plan.state == "already_applied":
                return plan
            if plan.state not in {
                "ready_canonical_update",
                "ready_empty",
                "ready_legacy",
            }:
                return plan

            if plan.state == "ready_canonical_update":
                expected_pending = expected_oa_capabilities()[0]
                update_values = expected_pending.model_dump(mode="python")
                update_values.pop("capability_id")
                update_result = await connection.execute(
                    sa.update(capabilities)
                    .where(
                        capabilities.c.capability_id
                        == expected_pending.capability_id,
                        capabilities.c.status == "active",
                    )
                    .values(**update_values)
                )
                if update_result.rowcount != plan.update_count:
                    raise RegistryManagementError(
                        "canonical_update_rowcount_mismatch",
                        plan=plan,
                    )
            legacy_ids = _authorized_active_legacy_ids(catalog)
            if plan.state == "ready_legacy" and legacy_ids:
                update_result = await connection.execute(
                    sa.update(capabilities)
                    .where(
                        capabilities.c.capability_id.in_(legacy_ids),
                        capabilities.c.status == "active",
                    )
                    .values(status="disabled")
                )
                if update_result.rowcount != plan.disable_count:
                    raise RegistryManagementError(
                        "update_rowcount_mismatch",
                        plan=plan,
                    )
            if plan.insert_count:
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
                raise RegistryManagementError(
                    "postcondition_failed",
                    plan=plan,
                )
            return RegistryManagementPlan(
                state="applied",
                deployment_path=plan.deployment_path,
                canonical_found_count=postcondition.canonical_found_count,
                canonical_valid_count=postcondition.canonical_valid_count,
                legacy_active_count=postcondition.legacy_active_count,
                unknown_oa_count=postcondition.unknown_oa_count,
                insert_count=plan.insert_count,
                disable_count=plan.disable_count,
                update_count=plan.update_count,
                plan_state=plan.state,
            )
    except RegistryManagementError as exc:
        if exc.plan is None and plan is not None:
            raise RegistryManagementError(exc.code, plan=plan) from exc
        raise
    except Exception as exc:
        if plan is not None:
            raise RegistryManagementContextError(plan, exc) from exc
        raise


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
    pending_predecessor_fingerprints: frozenset[str] | None = None,
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
    pending_predecessor_fingerprints = (
        _PENDING_CANONICAL_PREDECESSOR_FINGERPRINTS
        if pending_predecessor_fingerprints is None
        else pending_predecessor_fingerprints
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
        if _legacy_capability_fingerprint(item) in authorized_legacy_fingerprints
    )
    known_disabled = tuple(
        item
        for item in noncanonical_oa
        if _legacy_capability_fingerprint(item) in known_disabled_fingerprints
    )
    known_unchanged = tuple(
        item
        for item in noncanonical_oa
        if _legacy_exact_capability_fingerprint(item)
        in known_unchanged_fingerprints
    )
    unknown_oa = tuple(
        item
        for item in noncanonical_oa
        if _legacy_capability_fingerprint(item)
        not in authorized_legacy_fingerprints | known_disabled_fingerprints
        and _legacy_exact_capability_fingerprint(item)
        not in known_unchanged_fingerprints
    )
    legacy_active = tuple(
        item for item in authorized_legacy if item.status == "active"
    )
    legacy_disabled = tuple(
        item for item in authorized_legacy if item.status == "disabled"
    )
    known_disabled_valid = all(item.status == "disabled" for item in known_disabled)
    pending_predecessor = by_id.get("oa.list_pending_workflows")
    pending_predecessor_valid = (
        pending_predecessor is not None
        and _legacy_exact_capability_fingerprint(pending_predecessor)
        in pending_predecessor_fingerprints
    )

    if (
        not canonical_found
        and not noncanonical_oa
    ):
        state = "ready_empty"
        deployment_path = "empty"
        insert_count = len(REQUIRED_ACTIVE_OA_CAPABILITY_IDS)
        disable_count = 0
        update_count = 0
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
        update_count = 0
    elif (
        len(canonical_found) == len(REQUIRED_ACTIVE_OA_CAPABILITY_IDS)
        and len(canonical_valid) == 1
        and pending_predecessor_valid
        and len(authorized_legacy)
        in {0, len(authorized_legacy_fingerprints)}
        and not legacy_active
        and len(legacy_disabled) == len(authorized_legacy)
        and len(known_disabled) in {0, len(known_disabled_fingerprints)}
        and known_disabled_valid
        and len(known_unchanged) in {0, len(known_unchanged_fingerprints)}
        and not unknown_oa
    ):
        state = "ready_canonical_update"
        deployment_path = "canonical_update"
        insert_count = 0
        disable_count = 0
        update_count = 1
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
        update_count = 0
    else:
        state = "precondition_failed"
        deployment_path = "invalid"
        insert_count = 0
        disable_count = 0
        update_count = 0

    return RegistryManagementPlan(
        state=state,
        deployment_path=deployment_path,
        canonical_found_count=len(canonical_found),
        canonical_valid_count=len(canonical_valid),
        legacy_active_count=len(legacy_active),
        unknown_oa_count=len(unknown_oa),
        insert_count=insert_count,
        disable_count=disable_count,
        update_count=update_count,
    )


def _legacy_capability_fingerprint(capability: CapabilitySpec) -> str:
    payload = _legacy_fingerprint_payload(capability)
    payload["status"] = "<managed-status>"
    return _fingerprint_payload(payload)


def _legacy_exact_capability_fingerprint(capability: CapabilitySpec) -> str:
    return _fingerprint_payload(_legacy_fingerprint_payload(capability))


def _exact_capability_fingerprint(capability: CapabilitySpec) -> str:
    return _fingerprint_payload(capability.model_dump(mode="json"))


def _legacy_fingerprint_payload(
    capability: CapabilitySpec,
) -> dict[str, object]:
    payload = capability.model_dump(mode="json")
    return {field: payload[field] for field in _LEGACY_FINGERPRINT_FIELDS}


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
        and _legacy_capability_fingerprint(item)
        in _AUTHORIZED_LEGACY_FINGERPRINTS
    )


if __name__ == "__main__":
    raise SystemExit(main())
