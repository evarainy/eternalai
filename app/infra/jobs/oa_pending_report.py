"""Worker side of the OA pending-workflow report kind.

The worker reads only ``oa.list_pending_workflows`` with the requester's own saved OA
session, through a minimal Gateway (registry limited to that descriptor, OA identity
mapping, MinimalPolicyGuard, PostgreSQL trace and HumanGate). No login, renewal,
directory, MCP or browser component is built; an unclassified read failure is probed once
with the official OA heartbeat so a dead Session reports ``oa_session_expired``.
Generation, read-back check and publication run in that order; a mismatch never
publishes. Logs carry stage, job, counts, elapsed time and closed codes only: never
titles, states, user names, cookies or content.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from time import monotonic
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.background_jobs import (
    JobExecution,
    JobKindSpec,
    JobOutcome,
    oa_pending_report_kind,
)
from app.config import OAReportWorkerSettings
from app.infra.adapters.oa.adapter import OA_LIST_PENDING_WORKFLOWS
from app.infra.adapters.oa.contracts import OAPendingWorkflowCollection
from app.infra.adapters.oa.factory import build_oa_read_provider_adapter
from app.infra.adapters.oa.provider import OASessionHeartbeat
from app.infra.auth.postgresql import PostgreSQLCredentialStore, PostgreSQLPrincipalRoleReader
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.human_gate import PostgreSQLHumanGate
from app.infra.identity.postgresql import PostgreSQLOAIdentityMapping
from app.infra.observability.postgresql_trace import PostgreSQLTraceWriter
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.infra.spreadsheets.oa_pending_report import (
    ReportRow,
    build_report,
    report_filename,
    verify_report,
)
from app.ports.background_jobs import BackgroundJobError, BackgroundJobRunOwner
from app.ports.capability_gateway import CapabilityGatewayPort
from app.ports.capability_registry import CapabilitySpec
from app.ports.credential_binding import OASessionHeartbeatResult
from app.ports.file_artifacts import XLSX_MEDIA_TYPE, ChunkWriter, FileArtifactError
from app.ports.request_context import RequestOrgContext

_LOGGER = logging.getLogger("uvicorn.error.background_jobs")
# Gateway error code -> closed job result code. Anything else is "OA unavailable": the
# Gateway does not tell credential-store faults from upstream or transport failures.
_FAILURES = {
    "identity_expired": "oa_session_expired",
    "identity_unbound": "oa_unbound",
    "identity_revoked": "oa_unbound",
    "policy_denied": "oa_forbidden",
    "upstream_permission_denied": "oa_forbidden",
}
# One heartbeat with the requester's own saved Session; None when it proves nothing.
WorkerSessionProbe = Callable[[BackgroundJobRunOwner], Awaitable[OASessionHeartbeatResult | None]]


class _PendingOnlyRegistry:
    """Read-only view of the PostgreSQL registry exposing only the pending descriptor."""

    def __init__(self, registry: PostgreSQLCapabilityRegistry) -> None:
        self._registry = registry

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        if capability_id != OA_LIST_PENDING_WORKFLOWS:
            return None
        return await self._registry.get(capability_id)

    async def list(
        self, target_system: str | None = None, type: str | None = None, status: str | None = None
    ) -> list[CapabilitySpec]:
        found = await self._registry.list(target_system=target_system, type=type, status=status)
        return [spec for spec in found if spec.capability_id == OA_LIST_PENDING_WORKFLOWS]

    async def create(self, capability: CapabilitySpec) -> CapabilitySpec:
        raise PermissionError("capability_registry_read_only")

    async def update(self, capability_id: str, patch: dict[str, Any]) -> CapabilitySpec:
        raise PermissionError("capability_registry_read_only")

    async def disable(self, capability_id: str) -> CapabilitySpec:
        raise PermissionError("capability_registry_read_only")


async def read_pending_workflows(
    gateway: CapabilityGatewayPort,
    role_reader: PostgreSQLPrincipalRoleReader,
    owner: BackgroundJobRunOwner,
    *,
    task_id: str,
    session_probe: WorkerSessionProbe | None = None,
) -> OAPendingWorkflowCollection:
    """One full read of the requester's own OA to-dos; failures are closed job codes."""

    assert owner.session_id is not None
    roles = await role_reader.list_roles(owner.ai_user_id, tenant_id=owner.tenant_id)
    result = await gateway.execute_capability(
        task_id=task_id,
        session_id=owner.session_id,
        ai_user_id=owner.ai_user_id,
        capability_id=OA_LIST_PENDING_WORKFLOWS,
        arguments={},
        request_context=RequestOrgContext(
            request_id=str(uuid4()),
            tenant_id=owner.tenant_id,
            roles=list(roles),
            channel="web",
        ),
    )
    if result.status != "completed" or result.data is None:
        code = _FAILURES.get(result.error_code or "")
        if code is None:
            # OA's ambiguous rejections (e.g. 002) mean expiry only when its heartbeat agrees.
            dead = session_probe is not None and await session_probe(owner) == "dead"
            code = "oa_session_expired" if dead else "oa_unavailable"
        raise BackgroundJobError(code)
    try:
        return OAPendingWorkflowCollection.model_validate(result.data, strict=True)
    except ValidationError:
        raise BackgroundJobError("oa_incomplete") from None


def _log(
    stage: str, job: object, *, rows: object = "-", started: float | None = None, code: str = "-"
) -> None:
    elapsed = "-" if started is None else int((monotonic() - started) * 1000)
    _LOGGER.info(
        "report_job stage=%s job=%s rows=%s elapsed_ms=%s code=%s", stage, job, rows, elapsed, code
    )


class OAPendingReportHandler:
    def __init__(
        self,
        gateway: CapabilityGatewayPort,
        role_reader: PostgreSQLPrincipalRoleReader,
        clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        *,
        session_probe: WorkerSessionProbe | None = None,
    ) -> None:
        self._gateway = gateway
        self._roles = role_reader
        self._clock = clock
        self._session_probe = session_probe

    async def __call__(self, params: BaseModel, execution: JobExecution) -> JobOutcome:
        started = monotonic()
        try:
            return await self._run(execution, started)
        except BackgroundJobError as error:
            _log("failed", execution.job_id, started=started, code=error.code)
            raise
        except asyncio.CancelledError:
            _log("failed", execution.job_id, started=started, code="cancelled")
            raise
        except Exception:
            _log("failed", execution.job_id, started=started, code="handler_failed")
            raise

    async def _run(self, execution: JobExecution, started: float) -> JobOutcome:
        owner, outputs, job_id = execution.owner, execution.outputs, execution.job_id
        if owner is None or job_id is None or owner.session_id is None:
            raise BackgroundJobError("report_session_required")
        if outputs is None:
            raise BackgroundJobError("file_artifacts_unavailable")
        read_started_at = self._clock()
        collection = await read_pending_workflows(
            self._gateway,
            self._roles,
            owner,
            task_id=f"report-job:{job_id}",
            session_probe=self._session_probe,
        )
        read_finished_at = self._clock()
        rows = [
            ReportRow(
                item.title, item.status, item.received_at, item.created_at, item.workflow_type_id
            )
            for item in collection.workflows
        ]
        _log("oa_read", job_id, rows=len(rows), started=started)

        generated_at = self._clock()
        times = {
            "generated_at": generated_at,
            "read_started_at": read_started_at,
            "read_finished_at": read_finished_at,
        }
        content = await asyncio.to_thread(build_report, rows, **times)
        _log("generate", job_id, rows=len(rows), started=started)
        if not await asyncio.to_thread(verify_report, content, rows, **times):
            raise BackgroundJobError("report_verify_failed")
        _log("verify", job_id, rows=len(rows), started=started)

        async def write(emit: ChunkWriter) -> None:
            await emit(content)

        try:
            artifact_id = await outputs.publish(
                write, report_filename(generated_at), XLSX_MEDIA_TYPE
            )
        except FileArtifactError as error:
            _LOGGER.warning("report_job_publish_failed job=%s code=%s", job_id, error.code)
            raise BackgroundJobError("report_publish_failed") from None
        _log("publish", job_id, rows=len(rows), started=started)
        _log("done", job_id, rows=len(rows), started=started, code="ok")
        return JobOutcome(
            result_code="ok",
            result_summary={
                "artifact_id": str(artifact_id),
                "row_count": len(rows),
                "verified": True,
                "read_started_at": read_started_at.isoformat(),
                "read_finished_at": read_finished_at.isoformat(),
            },
        )


def build_worker_oa_pending_report_kind(
    settings: OAReportWorkerSettings, sessions: async_sessionmaker[AsyncSession]
) -> JobKindSpec:
    """The report kind with its worker handler over the minimal OA read Gateway."""

    return oa_pending_report_kind(
        settings.oa_read.source_tenant_id,
        handler=OAPendingReportHandler(
            build_worker_pending_gateway(settings, sessions),
            PostgreSQLPrincipalRoleReader(session_factory=sessions),
            session_probe=build_worker_session_probe(settings, sessions),
        ),
    )


def build_worker_session_probe(
    settings: OAReportWorkerSettings, sessions: async_sessionmaker[AsyncSession]
) -> WorkerSessionProbe:
    """Read-only liveness check of the requester's own OA Session: never logs in or renews."""

    credential_store = PostgreSQLCredentialStore(
        session_factory=sessions, encryption_key=settings.credential_encryption_key
    )
    heartbeat = OASessionHeartbeat(
        base_url=settings.oa_read.oa_base_url,
        heartbeat_path=settings.oa_heartbeat_path,
        timeout_seconds=settings.oa_read.oa_timeout_seconds,
    )

    async def probe(owner: BackgroundJobRunOwner) -> OASessionHeartbeatResult | None:
        session = None
        try:
            session = await credential_store.load(owner.ai_user_id, "oa", tenant_id=owner.tenant_id)
            result = None if session is None else await heartbeat(session)
        except Exception as error:
            # An unreadable credential or unreachable OA proves nothing about expiry.
            _LOGGER.info("oa_heartbeat_unavailable exception_type=%s", type(error).__name__)
            return None
        finally:
            session = None
        _LOGGER.info("oa_heartbeat result=%s", result)
        return result

    return probe


def build_worker_pending_gateway(
    settings: OAReportWorkerSettings, sessions: async_sessionmaker[AsyncSession]
) -> CapabilityGateway:
    """Minimal read-only Gateway over ``oa.list_pending_workflows`` only (no renewal)."""

    oa_read = settings.oa_read
    credential_store = PostgreSQLCredentialStore(
        session_factory=sessions, encryption_key=settings.credential_encryption_key
    )
    gateway = CapabilityGateway(
        capability_registry=_PendingOnlyRegistry(PostgreSQLCapabilityRegistry(sessions)),
        identity_mapping=PostgreSQLOAIdentityMapping(session_factory=sessions),
        policy_guard=MinimalPolicyGuard(),
        trace_port=PostgreSQLTraceWriter(sessions),
        # No session_renewal: an expired OA session fails the job, it is never renewed here.
        adapters={"oa": build_oa_read_provider_adapter(oa_read, credential_store=credential_store)},
        human_gate_port=PostgreSQLHumanGate(sessions),
        unbound_task_capability_ids=frozenset({OA_LIST_PENDING_WORKFLOWS}),
        tenant_id=oa_read.source_tenant_id,
    )
    gateway.assert_production_wiring()
    return gateway


__all__ = (
    "OAPendingReportHandler",
    "build_worker_oa_pending_report_kind",
    "build_worker_pending_gateway",
    "build_worker_session_probe",
    "read_pending_workflows",
)
