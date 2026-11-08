"""Background job kinds and the user-facing job service.

A kind declares its external effect, parameter parser, handler and authorization,
plus an optional asynchronous admission check run on the shared submit path. Only
``none``/``read_only`` kinds are accepted; write kinds are rejected when they are
registered. The first kind is ``system.worker_self_check``: the worker re-runs the
production read-only dependency checks in its own process and environment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from app.ports.auth import Principal
from app.ports.background_jobs import (
    BackgroundJobAcceptance,
    BackgroundJobAcceptRequest,
    BackgroundJobCancellation,
    BackgroundJobError,
    BackgroundJobLedgerPort,
    BackgroundJobOwner,
    BackgroundJobRunOwner,
    BackgroundJobView,
    ExternalEffect,
)
from app.ports.file_artifacts import JobOutputPublisher
from app.ports.identity_mapping import IdentityMappingPort

SELF_CHECK_KIND = "system.worker_self_check"
SELF_CHECK_NAMES = (
    "database",
    "redis",
    "organization_directory",
    "organization_directory_dispatch_policy",
)
_KIND_NAME = re.compile(r"^[a-z][a-z0-9_.]{2,63}$")

ReadonlyCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class JobOutcome:
    result_code: str
    result_summary: dict[str, Any]


@dataclass(frozen=True)
class JobExecution:
    """Worker-side resources a handler may use."""

    checks: Mapping[str, ReadonlyCheck]
    check_timeout_seconds: float
    # Bound by the runner to this execution's claim; None when file artifacts are off.
    outputs: JobOutputPublisher | None = None
    # Job and requester read from the ledger row by the runner; None outside an execution.
    job_id: UUID | None = None
    owner: BackgroundJobRunOwner | None = None


JobHandler = Callable[[BaseModel, JobExecution], Awaitable[JobOutcome]]
# (principal, trusted bound session id) -> None to accept, or a closed refusal code.
JobAdmission = Callable[[Principal, str | None], Awaitable[str | None]]


@dataclass(frozen=True)
class JobKindSpec:
    name: str
    external_effect: str
    parse_params: Callable[[Mapping[str, Any]], BaseModel]
    handler: JobHandler
    authorize: Callable[[Principal], bool]
    admit: JobAdmission | None = None

    def __post_init__(self) -> None:
        if self.external_effect not in ("none", "read_only"):
            raise BackgroundJobError("background_job_kind_write_unsupported")
        if _KIND_NAME.fullmatch(self.name) is None:
            raise BackgroundJobError("background_job_kind_invalid")


class SelfCheckParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hold_seconds: StrictInt = Field(ge=0)


async def _bounded(check: ReadonlyCheck, timeout_seconds: float) -> bool:
    try:
        async with asyncio.timeout(timeout_seconds):
            return bool(await check())
    except Exception:
        return False


async def run_self_check(params: BaseModel, execution: JobExecution) -> JobOutcome:
    assert isinstance(params, SelfCheckParams)
    for _ in range(params.hold_seconds):
        await asyncio.sleep(1)
    results = await asyncio.gather(
        *(
            _bounded(execution.checks[name], execution.check_timeout_seconds)
            for name in SELF_CHECK_NAMES
        )
    )
    checks = {
        name: "ok" if healthy else "failed"
        for name, healthy in zip(SELF_CHECK_NAMES, results, strict=True)
    }
    return JobOutcome(
        result_code="ok" if all(results) else "degraded",
        result_summary={"checks": checks},
    )


def build_job_kinds(self_check_max_hold_seconds: int) -> dict[str, JobKindSpec]:
    def parse_self_check(raw: Mapping[str, Any]) -> BaseModel:
        params = SelfCheckParams.model_validate(dict(raw))
        if params.hold_seconds > self_check_max_hold_seconds:
            raise ValueError("hold_seconds_out_of_range")
        return params

    spec = JobKindSpec(
        name=SELF_CHECK_KIND,
        external_effect="read_only",
        parse_params=parse_self_check,
        handler=run_self_check,
        authorize=lambda _principal: True,
    )
    return {spec.name: spec}


OA_PENDING_REPORT_KIND = "report.oa_pending_workflows_xlsx"
OA_PENDING_REPORT_PARAMS: Mapping[str, str] = {"report": "oa_pending_v1"}
_OA_PENDING_REPORT_NAMESPACE = UUID("6f3c2a4e-5b1d-4c8e-9a7f-2d0e1b3c4a59")


class OAPendingReportParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report: Literal["oa_pending_v1"]


def oa_pending_report_key(principal: Principal, session_id: str, client_request_id: str) -> UUID:
    """One acceptance per chat request: owner, bound session and client request id."""

    return uuid5(
        _OA_PENDING_REPORT_NAMESPACE,
        f"{principal.org_ctx.tenant_id}|{principal.ai_user_id}|{session_id}|"
        f"{client_request_id}|{OA_PENDING_REPORT_KIND}",
    )


async def _executed_by_worker(params: BaseModel, execution: JobExecution) -> JobOutcome:
    # The API process only accepts jobs; the worker registers the real handler.
    raise BackgroundJobError("background_job_kind_invalid")


def oa_pending_report_kind(
    tenant_id: str,
    *,
    identity_mapping: IdentityMappingPort | None = None,
    handler: JobHandler = _executed_by_worker,
) -> JobKindSpec:
    """The requester's own OA to-dos as xlsx; submitted only from a bound chat session.

    Admission reads binding metadata only (no decryption): a missing or revoked binding
    is ``oa_unbound``; a failed lookup is ``oa_unavailable``, never "unbound".
    """

    async def admit(principal: Principal, session_id: str | None) -> str | None:
        if session_id is None:
            return "report_session_required"
        if identity_mapping is None:
            return "oa_unavailable"
        mapping = await identity_mapping.get_mapping(
            principal.ai_user_id, "oa", tenant_id=principal.org_ctx.tenant_id
        )
        if mapping is None or mapping.bind_status == "revoked":
            return "oa_unbound"
        if mapping.bind_status == "verification_failed":
            return "oa_unavailable"
        return None

    return JobKindSpec(
        name=OA_PENDING_REPORT_KIND,
        external_effect="read_only",
        parse_params=lambda raw: OAPendingReportParams.model_validate(dict(raw)),
        handler=handler,
        authorize=lambda principal: principal.org_ctx.tenant_id == tenant_id,
        admit=admit,
    )


def parse_job_params(spec: JobKindSpec, raw: Mapping[str, Any]) -> BaseModel:
    try:
        return spec.parse_params(raw)
    except (ValidationError, ValueError, TypeError):
        raise BackgroundJobError("background_job_params_invalid") from None


def _owner(principal: Principal) -> BackgroundJobOwner:
    return BackgroundJobOwner(
        tenant_id=principal.org_ctx.tenant_id, ai_user_id=principal.ai_user_id
    )


class BackgroundJobService:
    def __init__(
        self, ledger: BackgroundJobLedgerPort, kinds: Mapping[str, JobKindSpec]
    ) -> None:
        self._ledger = ledger
        self._kinds = dict(kinds)

    @property
    def kinds(self) -> Mapping[str, JobKindSpec]:
        return self._kinds

    async def submit(
        self,
        principal: Principal,
        *,
        kind: str,
        params: Mapping[str, Any],
        idempotency_key: UUID,
        session_id: str | None = None,
    ) -> BackgroundJobAcceptance:
        """``session_id`` is the caller's verified bound chat session, never client input."""

        spec = self._kinds.get(kind)
        if spec is None or not spec.authorize(principal):
            raise BackgroundJobError("background_job_kind_invalid")
        canonical = parse_job_params(spec, params).model_dump(mode="json")
        if spec.admit is not None:
            refused = await spec.admit(principal, session_id)
            if refused is not None:
                raise BackgroundJobError(refused)
        fingerprint = hashlib.sha256(
            json.dumps(
                {"kind": kind, "params": canonical}, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).digest()
        return await self._ledger.accept(
            BackgroundJobAcceptRequest(
                owner=_owner(principal),
                session_id=session_id,
                kind=kind,
                external_effect=cast(ExternalEffect, spec.external_effect),
                params=canonical,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
            )
        )

    async def get(self, principal: Principal, job_id: UUID) -> BackgroundJobView:
        view = await self._ledger.get(_owner(principal), job_id)
        if view is None:
            raise BackgroundJobError("background_job_not_found")
        return view

    async def list(self, principal: Principal, limit: int) -> list[BackgroundJobView]:
        return await self._ledger.list(_owner(principal), limit)

    async def cancel(self, principal: Principal, job_id: UUID) -> BackgroundJobCancellation:
        result = await self._ledger.cancel(_owner(principal), job_id)
        if result is None:
            raise BackgroundJobError("background_job_not_found")
        return result

    async def worker_alive(self) -> bool:
        return await self._ledger.worker_alive()


__all__ = (
    "BackgroundJobService",
    "JobAdmission",
    "JobExecution",
    "JobHandler",
    "JobKindSpec",
    "JobOutcome",
    "OA_PENDING_REPORT_KIND",
    "OA_PENDING_REPORT_PARAMS",
    "OAPendingReportParams",
    "SELF_CHECK_KIND",
    "SELF_CHECK_NAMES",
    "SelfCheckParams",
    "build_job_kinds",
    "oa_pending_report_key",
    "oa_pending_report_kind",
    "parse_job_params",
    "run_self_check",
)
