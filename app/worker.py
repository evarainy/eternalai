"""Background worker process: ``python -m app.worker``.

A separate process from the 18005 API with no HTTP listener. It loads only
``WorkerSettings`` (database, Redis, health timeout, directory age, concurrency, self-check
hold limit, file artifact root and MAC key), plus optional IM Bot and reader configuration.
When personal agents are enabled it also loads the tool-free published-material model seam, without
the production composition, ordinary chat, OA or browser stacks. When an OA read mode is
configured it registers the OA pending-workflow report kind over a minimal read-only OA
Gateway (requester's own saved session; no login, renewal or heartbeat). With file
artifacts it registers the sheet fill kind (OA pending and/or fixed-document Q&A sources,
whichever are configured; the workbook is parsed only in an isolated child). The loop is
psycopg + Selector.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from functools import partial

from sqlalchemy.engine import make_url

from app.background_jobs import JobExecution, build_job_kinds
from app.config import WorkerSettings
from app.db.session import make_async_engine, make_async_session_factory
from app.dependency_checks import build_readonly_checks
from app.event_loop import make_event_loop
from app.infra.communication.composition import build_im_delivery, build_personal_agents
from app.infra.jobs.execution import BackgroundJobRunner
from app.infra.jobs.ledger import PostgreSQLBackgroundJobLedger
from app.infra.jobs.procrastinate_app import (
    FILE_ARTIFACTS_CONTEXT_KEY,
    IM_DELIVERY_CONTEXT_KEY,
    PERSONAL_AGENT_CONTEXT_KEY,
    RUNNER_CONTEXT_KEY,
    SESSIONS_CONTEXT_KEY,
    build_procrastinate_app,
    psycopg_conninfo,
)
from app.infra.jobs.queue_sql import GENERAL_QUEUE, MAINTENANCE_QUEUE
from app.infra.organization_directory.postgresql import PostgreSQLOrganizationDirectory
from app.infra.organization_directory.sync_state import PostgreSQLOrganizationDirectorySync
from app.infra.persistence.file_artifacts import build_file_artifact_service
from app.organization_directory_sync import OrganizationDirectorySyncService
from app.ports.organization_directory import (
    OrganizationDirectorySnapshot,
    OrganizationDirectorySourcePort,
)

_LOGGER = logging.getLogger("uvicorn.error.background_jobs")
SHUTDOWN_GRACEFUL_TIMEOUT_SECONDS = 30.0


def psycopg_database_url(database_url: str) -> str:
    """The worker always uses psycopg (Selector loop), whatever the API driver is."""

    return make_url(database_url).set(drivername="postgresql+psycopg").render_as_string(
        hide_password=False
    )


async def _no_source_reader(
    _source: OrganizationDirectorySourcePort, _fetched_at: datetime
) -> OrganizationDirectorySnapshot:
    raise RuntimeError("organization_directory_source_not_installed")


async def run_worker(settings: WorkerSettings) -> None:
    database_url = psycopg_database_url(settings.database_url)
    engine = make_async_engine(database_url)
    sessions = make_async_session_factory(engine)
    delivery_client = None
    try:
        file_artifacts = build_file_artifact_service(settings.file_artifacts, sessions)
        delivery_service, delivery_client = build_im_delivery(
            settings.im_delivery, sessions, settings.personal_agents, files=file_artifacts,
        )
        personal_runtime = None
        if settings.personal_agents is not None:
            from app.infra.llm.agentscope_chat import build_chat_model
            from app.infra.orchestration.agentscope_runtime.personal_agent import (
                AgentScopePersonalAgentRuntime,
            )

            personal_runtime = AgentScopePersonalAgentRuntime(partial(
                build_chat_model, base_url=settings.personal_agents.llm_base_url,
                model=settings.personal_agents.llm_model,
            ))
        personal_service = build_personal_agents(
            settings.personal_agents, sessions, delivery_service.sender, personal_runtime,
        ) if delivery_service is not None else None
        if delivery_service is not None:
            delivery_service.set_agent_replies(personal_service)
        checks = build_readonly_checks(
            database_url=database_url,
            redis_url=settings.redis_url,
            timeout_seconds=settings.health_timeout_seconds,
            directory_max_age_s=settings.organization_directory_max_age_s,
            organization_directory=PostgreSQLOrganizationDirectory(sessions),
            directory_sync=OrganizationDirectorySyncService(
                store=PostgreSQLOrganizationDirectorySync(sessions),
                reader=_no_source_reader,
                source_opener=None,
                max_age_s=settings.organization_directory_max_age_s,
            ),
        )
        kinds = build_job_kinds(settings.self_check_max_hold_seconds)
        if settings.oa_report is not None:
            from app.infra.jobs.oa_pending_report import build_worker_oa_pending_report_kind

            report = build_worker_oa_pending_report_kind(settings.oa_report, sessions)
            kinds[report.name] = report
        if file_artifacts is not None:
            from app.infra.jobs.sheet_fill import build_worker_sheet_fill_kind

            sheet_fill = build_worker_sheet_fill_kind(
                file_artifacts, sessions,
                oa_report=settings.oa_report, document_qa=settings.document_qa,
            )
            kinds[sheet_fill.name] = sheet_fill
        runner = BackgroundJobRunner(
            PostgreSQLBackgroundJobLedger(sessions),
            kinds,
            JobExecution(
                checks={**checks.health, **checks.diagnostics},
                check_timeout_seconds=settings.health_timeout_seconds,
            ),
            job_outputs=None if file_artifacts is None else file_artifacts.job_outputs,
        )
        app = build_procrastinate_app(psycopg_conninfo(database_url))
        async with app.open_async():
            _LOGGER.info(
                "background_worker_started queues=%s,%s concurrency=%s",
                GENERAL_QUEUE,
                MAINTENANCE_QUEUE,
                settings.concurrency,
            )
            await app.run_worker_async(
                queues=[GENERAL_QUEUE, MAINTENANCE_QUEUE],
                concurrency=settings.concurrency,
                shutdown_graceful_timeout=SHUTDOWN_GRACEFUL_TIMEOUT_SECONDS,
                delete_jobs="never",
                # Windows: asyncio.Runner turns Ctrl+C into cancelling this task, which
                # the official worker handles as a graceful stop.
                install_signal_handlers=sys.platform != "win32",
                additional_context={
                    RUNNER_CONTEXT_KEY: runner,
                    SESSIONS_CONTEXT_KEY: sessions,
                    FILE_ARTIFACTS_CONTEXT_KEY: file_artifacts,
                    IM_DELIVERY_CONTEXT_KEY: delivery_service,
                    PERSONAL_AGENT_CONTEXT_KEY: personal_service,
                },
            )
    finally:
        try:
            if delivery_client is not None:
                await delivery_client.aclose()
        finally:
            await engine.dispose()
        _LOGGER.info("background_worker_stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    settings = WorkerSettings.from_env()
    database_url = psycopg_database_url(settings.database_url)
    with asyncio.Runner(loop_factory=lambda: make_event_loop(database_url=database_url)) as runner:
        try:
            runner.run(run_worker(settings))
        except (KeyboardInterrupt, asyncio.CancelledError):
            _LOGGER.info("background_worker_interrupted")


if __name__ == "__main__":
    main()
