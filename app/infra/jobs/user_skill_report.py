"""Background export of a personal flow as xlsx (``report.user_skill_xlsx``).

The API accepts the job only for the requester's own usable flow, at its current
version, made only of OA queries, from a bound chat session. The worker re-runs
the flow with the requester's own saved OA session through a Gateway that sees
only active OA queries, then builds, reads back and publishes the file. Logs
carry stage, job, counts and closed codes only.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.background_jobs import (
    JobExecution,
    JobHandler,
    JobKindSpec,
    JobOutcome,
    executed_by_worker,
    oa_binding_refusal,
)
from app.config import OAReportWorkerSettings
from app.credential_polling import OASessionKeeper, prepare_active_oa_read
from app.infra.adapters.oa.factory import build_oa_read_provider_adapter
from app.infra.auth.postgresql import PostgreSQLCredentialStore
from app.infra.gateway.capability_gateway import CapabilityGateway
from app.infra.human_gate import PostgreSQLHumanGate
from app.infra.identity.postgresql import PostgreSQLOAIdentityMapping
from app.infra.observability.postgresql_trace import PostgreSQLTraceWriter
from app.infra.orchestration.agent_adapter import AgentOrchestrationAdapter
from app.infra.persistence.capability_registry.repository import PostgreSQLCapabilityRegistry
from app.infra.persistence.task_store.postgresql import PostgreSQLTaskStore
from app.infra.persistence.user_skills import (
    PostgreSQLUserSkillSessionStore,
    PostgreSQLUserSkillStore,
)
from app.infra.policy.minimal_policy_guard import MinimalPolicyGuard
from app.infra.sdui.response_envelope_builder import ResponseEnvelopeBuilder
from app.infra.spreadsheets.user_skill_report import (
    build_user_skill_report,
    user_skill_report_filename,
    verify_user_skill_report,
)
from app.infra.user_skills.closing import UserSkillTaskCloser
from app.ports.auth import Principal
from app.ports.background_jobs import BackgroundJobError
from app.ports.capability_registry import CapabilitySpec
from app.ports.file_artifacts import XLSX_MEDIA_TYPE, ChunkWriter, FileArtifactError
from app.ports.identity_mapping import IdentityMappingPort
from app.ports.user_skills import UserSkillOwner
from app.user_skills.runner import RunContext, UserSkillRunner
from app.user_skills.service import UserSkillFailure, UserSkillService
from app.workflow.engine import WorkflowEngine

USER_SKILL_REPORT_KIND = "report.user_skill_xlsx"
WORKER_RUN_SECONDS = 300.0
_LOGGER = logging.getLogger("app.jobs.user_skill_report")
_IDENTITY_STOPS = {"identity_expired", "identity_revoked", "identity_unbound"}


class UserSkillReportParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    version: int = Field(ge=1)
    input: dict[str, JsonValue] = Field(default_factory=dict, max_length=20)


def user_skill_report_kind(
    tenant_id: str,
    *,
    service: UserSkillService,
    identity_mapping: IdentityMappingPort | None = None,
    handler: JobHandler = executed_by_worker,
) -> JobKindSpec:
    async def admit(principal: Principal, session_id: str | None, params: BaseModel) -> str | None:
        assert isinstance(params, UserSkillReportParams)
        if session_id is None:
            return "report_session_required"
        refusal = await service.export_refusal(
            UserSkillOwner(tenant_id=principal.org_ctx.tenant_id, ai_user_id=principal.ai_user_id),
            params.skill_id,
            params.version,
        )
        if refusal is not None:
            return refusal
        return await oa_binding_refusal(identity_mapping, principal)

    return JobKindSpec(
        name=USER_SKILL_REPORT_KIND,
        external_effect="read_only",
        parse_params=lambda raw: UserSkillReportParams.model_validate(dict(raw)),
        handler=handler,
        authorize=lambda principal: principal.org_ctx.tenant_id == tenant_id,
        admit=admit,
    )


class _OAQueryRegistry:
    """Read-only view of the registry: active OA user-delegated queries only."""

    def __init__(self, registry: PostgreSQLCapabilityRegistry) -> None:
        self._registry = registry

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        spec = await self._registry.get(capability_id)
        if (
            spec is None
            or spec.type != "query"
            or spec.target_system != "oa"
            or spec.execution_identity != "user_delegated"
        ):
            return None
        return spec

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,  # noqa: A002 - port signature
        status: str | None = None,
    ) -> list[CapabilitySpec]:
        raise BackgroundJobError("background_job_kind_invalid")

    async def create(self, capability: CapabilitySpec) -> CapabilitySpec:
        raise BackgroundJobError("background_job_kind_invalid")

    async def update(self, capability_id: str, patch: dict[str, Any]) -> CapabilitySpec:
        raise BackgroundJobError("background_job_kind_invalid")

    async def disable(self, capability_id: str) -> CapabilitySpec:
        raise BackgroundJobError("background_job_kind_invalid")


class UserSkillReportHandler:
    def __init__(
        self,
        service: UserSkillService,
        *,
        session_keeper: OASessionKeeper | None,
        clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self._service = service
        self._keeper = session_keeper
        self._clock = clock

    async def __call__(self, params: BaseModel, execution: JobExecution) -> JobOutcome:
        started = monotonic()
        try:
            return await self._run(params, execution, started)
        except BackgroundJobError as error:
            _log("failed", execution, started, code=error.code)
            raise
        except asyncio.CancelledError:
            _log("failed", execution, started, code="cancelled")
            raise
        except Exception:
            _log("failed", execution, started, code="handler_failed")
            raise

    async def _run(self, params: BaseModel, execution: JobExecution, started: float) -> JobOutcome:
        assert isinstance(params, UserSkillReportParams)
        owner, outputs, job_id = execution.owner, execution.outputs, execution.job_id
        if owner is None or job_id is None or owner.session_id is None:
            raise BackgroundJobError("report_session_required")
        if outputs is None:
            raise BackgroundJobError("file_artifacts_unavailable")
        skill_owner = UserSkillOwner(tenant_id=owner.tenant_id, ai_user_id=owner.ai_user_id)
        refusal = await self._service.export_refusal(skill_owner, params.skill_id, params.version)
        if refusal is not None:
            raise BackgroundJobError(refusal)
        if self._keeper is not None:
            observed = await self._keeper.observe(
                tenant_id=owner.tenant_id, ai_user_id=owner.ai_user_id
            )
            if (
                observed is not None
                and observed.binding_state == "active"
                and observed.session_expires_at is not None
                and observed.session_expires_at <= datetime.now(UTC)
            ):
                await prepare_active_oa_read(
                    self._keeper,
                    tenant_id=owner.tenant_id,
                    ai_user_id=owner.ai_user_id,
                    trigger="local_expiry",
                )
        ctx = RunContext(
            tenant_id=owner.tenant_id,
            ai_user_id=owner.ai_user_id,
            session_id=owner.session_id,
            channel="web",
            entry="worker",
            deadline=asyncio.get_running_loop().time() + WORKER_RUN_SECONDS,
            job_id=str(job_id),
        )
        try:
            payload, failed, code = await self._service.run_saved(
                (owner.tenant_id, owner.ai_user_id, owner.session_id),
                skill_owner,
                ctx,
                params.skill_id,
                params.version,
                params.input,
                max_rows=None,
            )
        except UserSkillFailure as failure:
            raise BackgroundJobError(
                "user_skill_changed" if failure.status == 409 else "user_skill_unavailable"
            ) from None
        if payload.get("kind") != "result":
            raise BackgroundJobError("background_job_params_invalid")
        if failed:
            raise BackgroundJobError(
                "oa_session_expired" if code in _IDENTITY_STOPS else "user_skill_run_failed"
            )
        tables = payload["tables"]
        generated_at = self._clock()
        rows = sum(len(table["rows"]) for table in tables)
        content = await asyncio.to_thread(
            build_user_skill_report,
            tables,
            name=payload["name"],
            inputs=payload["inputs"],
            queried_at=generated_at,
        )
        if not await asyncio.to_thread(verify_user_skill_report, content, tables):
            raise BackgroundJobError("report_verify_failed")
        _log("verify", execution, started, rows=rows)

        async def write(emit: ChunkWriter) -> None:
            await emit(content)

        try:
            artifact_id = await outputs.publish(
                write, user_skill_report_filename(payload["name"], generated_at), XLSX_MEDIA_TYPE
            )
        except FileArtifactError as error:
            _LOGGER.warning("user_skill_report publish_failed job=%s code=%s", job_id, error.code)
            raise BackgroundJobError("report_publish_failed") from None
        _log("done", execution, started, rows=rows, code="ok")
        return JobOutcome(
            result_code="ok",
            result_summary={
                "artifact_id": str(artifact_id),
                "row_count": rows,
                "verified": True,
                "status": payload["status"],
            },
        )


def _log(
    stage: str, execution: JobExecution, started: float, *, rows: int | None = None, code: str = "-"
) -> None:
    _LOGGER.info(
        "user_skill_report stage=%s job=%s rows=%s elapsed_ms=%d code=%s",
        stage,
        execution.job_id,
        "-" if rows is None else rows,
        int((monotonic() - started) * 1000),
        code,
    )


def build_worker_user_skill_report_kind(
    settings: OAReportWorkerSettings,
    sessions: async_sessionmaker[AsyncSession],
    *,
    session_keeper: OASessionKeeper | None,
) -> JobKindSpec:
    """The export kind with its worker handler over an OA-query-only Gateway."""

    oa_read = settings.oa_read
    registry = PostgreSQLCapabilityRegistry(sessions)
    view = _OAQueryRegistry(registry)
    policy = MinimalPolicyGuard()
    trace = PostgreSQLTraceWriter(sessions)
    tasks = PostgreSQLTaskStore(sessions)
    human_gate = PostgreSQLHumanGate(sessions)
    credential_store = PostgreSQLCredentialStore(
        session_factory=sessions, encryption_key=settings.credential_encryption_key
    )
    gateway = CapabilityGateway(
        capability_registry=view,
        identity_mapping=PostgreSQLOAIdentityMapping(session_factory=sessions),
        policy_guard=policy,
        trace_port=trace,
        adapters={"oa": build_oa_read_provider_adapter(oa_read, credential_store=credential_store)},
        human_gate_port=human_gate,
        tenant_id=oa_read.source_tenant_id,
    )
    gateway.assert_production_wiring()
    engine = WorkflowEngine(
        definitions={},
        capability_registry=view,
        gateway=gateway,
        task_store=tasks,
        trace_port=trace,
        human_gate_port=human_gate,
    )
    projector = AgentOrchestrationAdapter(
        capability_registry=view,
        gateway=gateway,
        workflow_engine=None,
        response_builder=ResponseEnvelopeBuilder(),
        confirmation_barrier=True,
    )
    service = UserSkillService(
        store=PostgreSQLUserSkillStore(sessions),
        sessions=PostgreSQLUserSkillSessionStore(sessions),
        runner=UserSkillRunner(
            registry=view,
            policy=policy,
            task_store=tasks,
            trace_port=trace,
            human_gate=human_gate,
            engine=engine,
            project_output=projector.project_leaf_data,
            closer=UserSkillTaskCloser(tasks, trace),
        ),
        registry=view,
        read_workflow_leaves={},
    )
    return user_skill_report_kind(
        oa_read.source_tenant_id,
        service=service,
        handler=UserSkillReportHandler(service, session_keeper=session_keeper),
    )


__all__ = (
    "USER_SKILL_REPORT_KIND",
    "UserSkillReportHandler",
    "build_worker_user_skill_report_kind",
    "user_skill_report_kind",
)
