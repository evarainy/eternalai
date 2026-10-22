"""Periodic rule orchestration; persistent subjects never become fake sessions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.ports.auth import Principal
from app.ports.work_object_dispatch import (
    DispatchRequest,
    DispatchSubject,
    PreparedDispatch,
    WorkDispatchError,
)
from app.ports.work_object_recurrence import (
    DispatchPreparationPort,
    GenerationSummary,
    SeriesHistoryPage,
    SeriesMutationResult,
    SeriesOccurrencePage,
    SeriesPage,
    SeriesRequest,
    SeriesView,
    WorkObjectSeriesStorePort,
    next_scheduled,
    occurrence_request,
)
from app.ports.work_object_scope import compute_visibility_scope

_LOGGER = logging.getLogger(__name__)


def _subject(principal: Principal) -> DispatchSubject:
    return DispatchSubject(
        principal.org_ctx.tenant_id, principal.ai_user_id, principal.org_ctx.directory_user_id
    )


class WorkObjectSeriesService:
    def __init__(
        self,
        store: WorkObjectSeriesStorePort,
        dispatch: DispatchPreparationPort,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._dispatch = dispatch
        self._clock = clock

    async def _validate_template(self, subject: DispatchSubject, request: SeriesRequest) -> None:
        scheduled = next_scheduled(request, self._clock(), inclusive=True)
        await self._dispatch.prepare_dispatch(
            subject,
            request=occurrence_request(request, scheduled),
            idempotency_key=uuid4(),
            scheduled_at=scheduled,
        )

    async def create(
        self, principal: Principal, *, request: SeriesRequest, idempotency_key: UUID
    ) -> SeriesMutationResult:
        subject = _subject(principal)
        await self._validate_template(subject, request)
        result = await self._store.create(subject, request, idempotency_key, self._clock())
        _LOGGER.info(
            "work_series stage=create series_id=%s replayed=%s",
            result.series.series_id,
            result.replayed,
        )
        return result

    async def get(self, principal: Principal, series_id: UUID) -> SeriesView:
        return await self._store.get(_subject(principal), series_id)

    async def series_id_for_task(self, principal: Principal, work_object_id: str) -> UUID | None:
        scope = await self._dispatch.build_scope(principal)
        try:
            return await self._store.series_id_for_task(scope, work_object_id)
        except WorkDispatchError as exc:
            if exc.code not in {"organization_directory_missing", "organization_directory_stale"}:
                raise
            _LOGGER.info(
                "work_series stage=read_scope_expired resource=task_series code=%s", exc.code
            )
        return await self._store.series_id_for_task(
            compute_visibility_scope(
                principal_tenant_id=scope.principal_tenant_id,
                principal_ai_user_id=scope.principal_ai_user_id,
                principal_directory_user_id=scope.principal_directory_user_id,
                principal_department_id=None,
            ),
            work_object_id,
        )

    async def list(
        self, principal: Principal, *, cursor: str | None = None, limit: int = 50
    ) -> SeriesPage:
        return await self._store.list(_subject(principal), cursor=cursor, limit=limit)

    async def update(
        self,
        principal: Principal,
        series_id: UUID,
        *,
        request: SeriesRequest,
        if_match: str,
        idempotency_key: UUID,
    ) -> SeriesMutationResult:
        subject = _subject(principal)
        await self._store.get(subject, series_id)
        await self._validate_template(subject, request)
        result = await self._store.update(
            subject, series_id, request, if_match, idempotency_key, self._clock()
        )
        _LOGGER.info(
            "work_series stage=update series_id=%s version=%d replayed=%s",
            series_id,
            result.series.version,
            result.replayed,
        )
        return result

    async def set_enabled(
        self,
        principal: Principal,
        series_id: UUID,
        *,
        enabled: bool,
        if_match: str,
        idempotency_key: UUID,
    ) -> SeriesMutationResult:
        subject = _subject(principal)
        view = await self._store.get(subject, series_id)
        if enabled:
            await self._validate_template(
                subject,
                SeriesRequest.model_validate(
                    view.model_dump(include=set(SeriesRequest.model_fields))
                ),
            )
        result = await self._store.set_enabled(
            subject, series_id, enabled, if_match, idempotency_key, self._clock()
        )
        _LOGGER.info(
            "work_series stage=%s series_id=%s version=%d replayed=%s",
            "enable" if enabled else "disable",
            series_id,
            result.series.version,
            result.replayed,
        )
        return result

    async def history(
        self, principal: Principal, series_id: UUID, *, cursor: str | None = None, limit: int = 50
    ) -> SeriesHistoryPage:
        return await self._store.history(_subject(principal), series_id, cursor=cursor, limit=limit)

    async def occurrences(
        self, principal: Principal, series_id: UUID, *, cursor: str | None = None, limit: int = 50
    ) -> SeriesOccurrencePage:
        scope = await self._dispatch.build_scope(principal)
        try:
            return await self._store.occurrences(
                _subject(principal),
                scope,
                series_id,
                cursor=cursor,
                limit=limit,
            )
        except WorkDispatchError as exc:
            if exc.code not in {"organization_directory_missing", "organization_directory_stale"}:
                raise
            _LOGGER.info(
                "work_series stage=read_scope_expired resource=occurrences code=%s", exc.code
            )
        return await self._store.occurrences(
            _subject(principal),
            compute_visibility_scope(
                principal_tenant_id=scope.principal_tenant_id,
                principal_ai_user_id=scope.principal_ai_user_id,
                principal_directory_user_id=scope.principal_directory_user_id,
                principal_department_id=None,
            ),
            series_id,
            cursor=cursor,
            limit=limit,
        )

    async def _prepare(
        self, subject: DispatchSubject, request: DispatchRequest, key: UUID, scheduled_at: datetime
    ) -> PreparedDispatch:
        return await self._dispatch.prepare_dispatch(
            subject, request=request, idempotency_key=key, scheduled_at=scheduled_at
        )

    async def generate_due(self, now: datetime) -> GenerationSummary:
        result = await self._store.generate_due(now, self._prepare)
        if result.generated or result.blocked:
            _LOGGER.info(
                "work_series stage=generation generated=%d blocked=%d",
                result.generated,
                result.blocked,
            )
        return result


class WorkObjectSeriesScheduler:
    """The host owns start/stop; this task runs only after explicit lifecycle mounting."""

    def __init__(
        self,
        service: WorkObjectSeriesService,
        *,
        interval_seconds: float = 30,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._service = service
        self._interval = interval_seconds
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="work-object-series")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            finally:
                self._task = None

    async def _run(self) -> None:
        _LOGGER.info("work_series stage=scheduler_started")
        while not self._stop.is_set():
            try:
                await self._service.generate_due(self._clock())
            except asyncio.CancelledError:
                raise
            except WorkDispatchError as exc:
                _LOGGER.error("work_series stage=tick_failed code=%s", exc.code)
            except Exception as exc:
                _LOGGER.error(
                    "work_series stage=tick_failed code=work_object_series_generation_failed "
                    "error_type=%s",
                    type(exc).__name__,
                )
            # Every failed tick waits the ordinary interval; its rolled-back work is not success.
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                pass
        _LOGGER.info("work_series stage=scheduler_stopped")
