"""Procrastinate 3.10.0 application for the background worker process.

Only the worker opens this App (psycopg + Selector loop). Under controller ruling O1
the API never needs it: acceptance and cancellation call the official SQL functions
inside the ledger transaction (``app.infra.jobs.queue_sql``). Tasks receive their
worker-side dependencies through the official ``additional_context`` mechanism.
"""

from __future__ import annotations

import logging

from procrastinate import App, PsycopgConnector
from procrastinate.job_context import JobContext
from sqlalchemy.engine import make_url

from app.infra.jobs.queue_sql import (
    GENERAL_QUEUE,
    MAINTENANCE_QUEUE,
    QUEUE_SCHEMA,
    RUN_BACKGROUND_JOB_TASK,
)
from app.infra.jobs.reconcile import reconcile_once

RECONCILE_TASK = "background_jobs.reconcile"
RECONCILE_CRON = "*/1 * * * *"
RUNNER_CONTEXT_KEY = "background_job_runner"
SESSIONS_CONTEXT_KEY = "background_job_sessions"
FILE_ARTIFACTS_CLEANUP_TASK = "file_artifacts.cleanup"
FILE_ARTIFACTS_CLEANUP_CRON = "*/10 * * * *"
FILE_ARTIFACTS_CONTEXT_KEY = "file_artifact_service"
_FILE_ARTIFACT_LOGGER = logging.getLogger("uvicorn.error.file_artifacts")


def psycopg_conninfo(database_url: str) -> str:
    """libpq URI for the worker pool, derived from the SQLAlchemy DATABASE_URL."""

    return make_url(database_url).set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def build_procrastinate_app(conninfo: str) -> App:
    connector = PsycopgConnector(
        conninfo=conninfo, kwargs={"options": f"-c search_path={QUEUE_SCHEMA}"}
    )
    app = App(connector=connector)

    @app.task(
        name=RUN_BACKGROUND_JOB_TASK, queue=GENERAL_QUEUE, pass_context=True, retry=False
    )
    async def run_background_job(context: JobContext, job_id: str) -> None:
        await context.additional_context[RUNNER_CONTEXT_KEY].run(context, job_id)

    @app.periodic(cron=RECONCILE_CRON, periodic_id="reconcile", queueing_lock=RECONCILE_TASK)
    @app.task(name=RECONCILE_TASK, queue=MAINTENANCE_QUEUE, pass_context=True, retry=False)
    async def reconcile(context: JobContext, timestamp: int) -> None:
        del timestamp
        await reconcile_once(context.additional_context[SESSIONS_CONTEXT_KEY])

    @app.periodic(
        cron=FILE_ARTIFACTS_CLEANUP_CRON,
        periodic_id="file_artifacts_cleanup",
        queueing_lock=FILE_ARTIFACTS_CLEANUP_TASK,
    )
    @app.task(
        name=FILE_ARTIFACTS_CLEANUP_TASK, queue=MAINTENANCE_QUEUE, pass_context=True, retry=False
    )
    async def cleanup_file_artifacts(context: JobContext, timestamp: int) -> None:
        del timestamp
        service = context.additional_context[FILE_ARTIFACTS_CONTEXT_KEY]
        if service is None:
            _FILE_ARTIFACT_LOGGER.info("file_artifact_cleanup_disabled")
            return
        await service.cleanup()

    return app


__all__ = (
    "FILE_ARTIFACTS_CLEANUP_TASK",
    "FILE_ARTIFACTS_CONTEXT_KEY",
    "RECONCILE_TASK",
    "RUNNER_CONTEXT_KEY",
    "SESSIONS_CONTEXT_KEY",
    "build_procrastinate_app",
    "psycopg_conninfo",
)
