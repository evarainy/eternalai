"""Worker side of the OA pending-workflow report kind.

The worker reads only ``oa.list_pending_workflows`` with the requester's own saved OA
session, through a minimal Gateway (registry limited to that descriptor, OA identity
mapping, MinimalPolicyGuard, PostgreSQL trace and HumanGate). One keeper shared by the
worker's OA readers recovers eligible active bindings only after a dead heartbeat.
No directory, MCP or browser component is built.
Generation, read-back check and publication run in that order; a mismatch never
publishes. Logs carry stage, job, counts, elapsed time and closed codes only: never
titles, states, user names, cookies or content.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.background_jobs import (
    JobExecution,
    JobKindSpec,
    JobOutcome,
    OASessionProbe,
    build_oa_session_probe,
    oa_pending_report_kind,
)
from app.config import OAReportWorkerSettings
from app.credential_polling import (
    CredentialPollingPolicy,
    CredentialRecoveryPolicy,
    CredentialSessionRecovery,
    OASessionKeeper,
    prepare_active_oa_read,
)
from app.infra.adapters.oa.adapter import OA_LIST_PENDING_WORKFLOWS
from app.infra.adapters.oa.contracts import OAPendingWorkflowCollection
from app.infra.adapters.oa.factory import build_oa_read_provider_adapter
from app.infra.adapters.oa.provider import OASessionHeartbeat
from app.infra.auth.background import OAPasswordCredentialAcquirer
from app.infra.auth.oa import OACredentialVerifier, make_urllib_session_factory
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
from app.ports.auth import OASessionCredential
from app.ports.background_jobs import BackgroundJobError, BackgroundJobRunOwner
from app.ports.capability_gateway import CapabilityGatewayPort, ExecutionResult
from app.ports.capability_registry import CapabilitySpec
from app.ports.credential_binding import OAKeepaliveStatus
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
WorkerSessionProbe = OASessionProbe


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
    session_keeper: OASessionKeeper | None = None,
) -> OAPendingWorkflowCollection:
    """One full read of the requester's own OA to-dos; failures are closed job codes."""

    session_id = owner.session_id
    assert session_id is not None
    roles = await role_reader.list_roles(owner.ai_user_id, tenant_id=owner.tenant_id)
    request_context = RequestOrgContext(
        request_id=str(uuid4()), tenant_id=owner.tenant_id, roles=list(roles), channel="web"
    )
    prepared: OAKeepaliveStatus | None = None
    if session_keeper is not None:
        observed = await session_keeper.observe(
            tenant_id=owner.tenant_id, ai_user_id=owner.ai_user_id
        )
        if (
            observed is not None
            and observed.binding_state == "active"
            and observed.session_expires_at is not None
            and observed.session_expires_at <= datetime.now(UTC)
        ):
            prepared = await prepare_active_oa_read(
                session_keeper, tenant_id=owner.tenant_id, ai_user_id=owner.ai_user_id,
                trigger="local_expiry",
            )

    async def read() -> ExecutionResult:
        return await gateway.execute_capability(
            task_id=task_id, session_id=session_id, ai_user_id=owner.ai_user_id,
            capability_id=OA_LIST_PENDING_WORKFLOWS, arguments={}, request_context=request_context,
        )

    result = await read()
    if result.status != "completed" or result.data is None:
        code = _FAILURES.get(result.error_code or "")
        if (
            session_keeper is not None
            and prepared in {None, "alive", "binding_unavailable"}
            and (result.error_code == "identity_expired" or code is None)
        ):
            prepared = await prepare_active_oa_read(
                session_keeper, tenant_id=owner.tenant_id, ai_user_id=owner.ai_user_id,
                trigger="read_identity_expired" if result.error_code == "identity_expired"
                else "oa_002",
            )
            if prepared in {"recovered", "refreshed_by_other"} or (
                prepared == "alive" and result.error_code == "identity_expired"
            ):
                result = await read()
                if result.status != "completed" or result.data is None:
                    raise BackgroundJobError(
                        _FAILURES.get(result.error_code or "", "oa_unavailable")
                    )
            elif prepared not in {"alive", "unavailable", "binding_unavailable"}:
                code = "oa_session_expired"
        if result.status != "completed" or result.data is None:
            if code is None:
                # Ineligible bindings retain their pure probe; unavailable never means dead.
                dead = (
                    prepared in {None, "binding_unavailable"}
                    and session_probe is not None and await session_probe(owner) == "dead"
                )
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
        session_keeper: OASessionKeeper | None = None,
    ) -> None:
        self._gateway = gateway
        self._roles = role_reader
        self._clock = clock
        self._session_probe = session_probe
        self._session_keeper = session_keeper

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
            session_keeper=self._session_keeper,
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
    settings: OAReportWorkerSettings, sessions: async_sessionmaker[AsyncSession],
    *, session_keeper: OASessionKeeper | None,
) -> JobKindSpec:
    """The report kind with its worker handler over the minimal OA read Gateway."""

    return oa_pending_report_kind(
        settings.oa_read.source_tenant_id,
        handler=OAPendingReportHandler(
            build_worker_pending_gateway(settings, sessions),
            PostgreSQLPrincipalRoleReader(session_factory=sessions),
            session_probe=build_worker_session_probe(settings, sessions),
            session_keeper=session_keeper,
        ),
    )


def build_worker_session_probe(
    settings: OAReportWorkerSettings, sessions: async_sessionmaker[AsyncSession]
) -> WorkerSessionProbe:
    """Read-only liveness check of the requester's own OA Session: never logs in or renews."""

    return build_oa_session_probe(
        PostgreSQLCredentialStore(
            session_factory=sessions, encryption_key=settings.credential_encryption_key
        ),
        OASessionHeartbeat(
            base_url=settings.oa_read.oa_base_url,
            heartbeat_path=settings.oa_heartbeat_path,
            timeout_seconds=settings.oa_read.oa_timeout_seconds,
        ),
    )


def build_worker_oa_session_keeper(
    settings: OAReportWorkerSettings, sessions: async_sessionmaker[AsyncSession]
) -> OASessionKeeper | None:
    """One worker-local heartbeat/recovery instance shared by both OA job handlers."""

    oa_read = settings.oa_read
    if oa_read.oa_read_adapter_mode != "live":
        return None
    tenant_id = oa_read.source_tenant_id
    store = PostgreSQLCredentialStore(
        session_factory=sessions, encryption_key=settings.credential_encryption_key
    )
    session_factory = make_urllib_session_factory(
        base_url=oa_read.oa_base_url, timeout_seconds=oa_read.oa_timeout_seconds
    )
    authentication = OACredentialVerifier(
        session_factory=session_factory,
        credential_store=store,
        role_reader=PostgreSQLPrincipalRoleReader(session_factory=sessions),
        identity_hmac_key=settings.identity_hmac_key,
        credential_ttl_seconds=settings.oa_credential_ttl_seconds,
        tenant_id=tenant_id,
    )
    policy = CredentialPollingPolicy(
        interval_seconds=settings.credential_poll_interval_seconds,
        maximum_backoff_seconds=settings.credential_poll_maximum_backoff_seconds,
        work_start_hour=settings.credential_poll_work_start_hour,
        work_end_hour=settings.credential_poll_work_end_hour,
        timezone_name=settings.credential_poll_timezone,
        global_concurrency=settings.credential_poll_global_concurrency,
        scheduler_tick_seconds=settings.credential_poll_scheduler_tick_seconds,
    )
    recovery = CredentialSessionRecovery(
        binding_store=store, tenant_id=tenant_id,
        acquirer=OAPasswordCredentialAcquirer(
            session_factory=session_factory, authentication=authentication,
            binding_store=store, tenant_id=tenant_id,
        ),
        policy=policy,
        recovery_policy=CredentialRecoveryPolicy(
            cooldown_seconds=settings.oa_recovery_cooldown_seconds,
            wait_seconds=settings.oa_recovery_wait_seconds,
            kick_window_seconds=settings.oa_recovery_kick_window_seconds,
        ),
    )

    async def load_session(ai_user_id: str) -> OASessionCredential | None:
        return await store.load(ai_user_id, "oa", tenant_id=tenant_id)

    return OASessionKeeper(
        binding_store=store, tenant_id=tenant_id, load_session=load_session,
        heartbeat=OASessionHeartbeat(
            base_url=oa_read.oa_base_url, heartbeat_path=settings.oa_heartbeat_path,
            timeout_seconds=oa_read.oa_timeout_seconds,
        ),
        recovery=recovery, credential_ttl_seconds=settings.oa_credential_ttl_seconds,
        heartbeat_interval_seconds=settings.oa_heartbeat_interval_seconds,
    )


def build_worker_pending_gateway(
    settings: OAReportWorkerSettings, sessions: async_sessionmaker[AsyncSession]
) -> CapabilityGateway:
    """Minimal Gateway; the shared worker read preparation owns session recovery."""

    oa_read = settings.oa_read
    credential_store = PostgreSQLCredentialStore(
        session_factory=sessions, encryption_key=settings.credential_encryption_key
    )
    gateway = CapabilityGateway(
        capability_registry=_PendingOnlyRegistry(PostgreSQLCapabilityRegistry(sessions)),
        identity_mapping=PostgreSQLOAIdentityMapping(session_factory=sessions),
        policy_guard=MinimalPolicyGuard(),
        trace_port=PostgreSQLTraceWriter(sessions),
        # The ledger owner prepares recovery outside the adapter's HTTP-only owner gate.
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
    "build_worker_oa_session_keeper",
    "build_worker_pending_gateway",
    "build_worker_session_probe",
    "read_pending_workflows",
)
