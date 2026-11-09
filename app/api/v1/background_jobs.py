"""Background job HTTP routes: accept, read and cancel the caller's own jobs.

"Accepted" means the ledger row and its queue row were committed together; it does not
mean started or finished. Reads only use the ledger and only return the caller's own
rows; another user's job is indistinguishable from a missing one (404).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.csrf import PrincipalDependency
from app.api.v1.runtime import _bind_session
from app.background_jobs import BackgroundJobService
from app.ports.auth import Principal
from app.ports.background_jobs import BackgroundJobError, BackgroundJobStatus, BackgroundJobView

_NO_STORE = {"Cache-Control": "no-store"}
_ERRORS: dict[str, tuple[int, str]] = {
    "idempotency_key_invalid": (422, "需要提供一个有效的幂等键。"),
    "background_job_request_invalid": (422, "后台任务请求格式不正确。"),
    "background_job_kind_invalid": (422, "不支持的后台任务类型。"),
    "background_job_params_invalid": (422, "后台任务参数不正确。"),
    "background_job_idempotency_conflict": (409, "该请求编号已用于另一项后台任务。"),
    "background_job_limit_reached": (429, "进行中的后台任务已达上限，请稍后再试。"),
    "background_job_not_found": (404, "没有找到该后台任务。"),
    "background_job_accept_failed": (503, "后台任务暂时无法接收，请稍后再试。"),
    "background_job_accept_outcome_unknown": (
        503,
        "后台任务接收结果未确认，请用同一请求重试核对。",
    ),
    "background_job_unavailable": (503, "后台任务服务未启用。"),
    # Admission refusals of the OA pending report kind (shared with the chat tool).
    "oa_unbound": (409, "需要先在软件中心授权 OA 后才能导出。"),
    "report_session_required": (422, "OA 待办报表需要在 AI 助手对话中发起。"),
    "oa_unavailable": (503, "OA 暂不可用，请稍后重试。"),
    # Admission refusals of the sheet fill kind (shared with the chat proposal tool).
    "sheet_fill_source_changed": (
        409,
        "上传的表格已变化或不属于本次对话，请重新上传后再填表。",
    ),
    "sheet_fill_document_forbidden": (403, "当前账号不能使用固定文档问答填表。"),
    "sheet_fill_sheet_size_unknown": (
        422,
        "表格的行列数无法确定，请另存后重新上传。",
    ),
    "sheet_fill_fidelity_unsupported": (
        422,
        "表格含图表、图片等对象，首版暂不支持，请另存为仅含数据的表格后重试",
    ),
    "sheet_fill_too_many_rows": (
        422,
        "填写的数据行超过上限（OA 待办每表 2000 行，文档问答合计 30 个问题）。",
    ),
}


class BackgroundJobCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    params: dict[str, Any]
    # Client chat session id; bound to the caller by the session binder before use.
    session_id: str | None = Field(default=None, min_length=1, max_length=512)


class BackgroundJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    kind: str
    status: BackgroundJobStatus
    result_code: str | None
    result_summary: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    cancel_requested_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime


class BackgroundJobAcceptResponse(BackgroundJobResponse):
    deduplicated: bool


class BackgroundJobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BackgroundJobResponse]


class BackgroundJobErrorDetail(BaseModel):
    code: str
    message: str


class BackgroundJobErrorResponse(BaseModel):
    detail: BackgroundJobErrorDetail


def _view(job: BackgroundJobView) -> BackgroundJobResponse:
    return BackgroundJobResponse(**job.model_dump())


def _fail(code: str) -> NoReturn:
    http_status, message = _ERRORS[code]
    raise HTTPException(
        status_code=http_status,
        detail={"code": code, "message": message},
        headers=_NO_STORE,
    )


def _idempotency_key(request: Request) -> UUID:
    values = request.headers.getlist("idempotency-key")
    try:
        if len(values) != 1:
            raise ValueError
        return UUID(values[0])
    except ValueError:
        _fail("idempotency_key_invalid")


class _BackgroundJobRoute(APIRoute):
    """Framework validation errors use the route's error envelope and never echo input."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError:
                _fail("background_job_request_invalid")

        return handle


def make_router(
    service: BackgroundJobService | None,
    require_principal: PrincipalDependency,
    session_binder: Callable[[Principal, str], str] | None = None,
) -> APIRouter:
    router = APIRouter(route_class=_BackgroundJobRoute)
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": BackgroundJobErrorResponse}
        for code in (403, 404, 409, 422, 429, 503)
    }

    def configured() -> BackgroundJobService:
        if service is None:
            _fail("background_job_unavailable")
        return service

    @router.post(
        "",
        response_model=BackgroundJobAcceptResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={**errors, 200: {"model": BackgroundJobAcceptResponse}},
        openapi_extra={
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                }
            ]
        },
    )
    async def submit_background_job(
        body: BackgroundJobCreateRequest,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> BackgroundJobAcceptResponse:
        key = _idempotency_key(request)
        bound_session_id = (
            None
            if body.session_id is None
            else _bind_session(
                principal=principal,
                requested_session_id=body.session_id,
                session_binder=session_binder,
            )
        )
        try:
            accepted = await configured().submit(
                principal,
                kind=body.kind,
                params=body.params,
                idempotency_key=key,
                session_id=bound_session_id,
            )
        except BackgroundJobError as error:
            _fail(error.code)
        response.headers.update(_NO_STORE)
        if accepted.deduplicated:
            response.status_code = status.HTTP_200_OK
        return BackgroundJobAcceptResponse(
            **accepted.job.model_dump(), deduplicated=accepted.deduplicated
        )

    @router.get("", response_model=BackgroundJobListResponse, responses=errors)
    async def list_background_jobs(
        response: Response,
        limit: int = Query(default=10, ge=1, le=50),
        principal: Principal = Depends(require_principal),
    ) -> BackgroundJobListResponse:
        jobs = await configured().list(principal, limit)
        response.headers.update(_NO_STORE)
        return BackgroundJobListResponse(items=[_view(job) for job in jobs])

    @router.get("/{job_id}", response_model=BackgroundJobResponse, responses=errors)
    async def get_background_job(
        job_id: UUID,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> BackgroundJobResponse:
        try:
            job = await configured().get(principal, job_id)
        except BackgroundJobError as error:
            _fail(error.code)
        response.headers.update(_NO_STORE)
        return _view(job)

    @router.post(
        "/{job_id}/cancel",
        response_model=BackgroundJobResponse,
        responses={**errors, 202: {"model": BackgroundJobResponse}},
    )
    async def cancel_background_job(
        job_id: UUID,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> BackgroundJobResponse:
        try:
            cancelled = await configured().cancel(principal, job_id)
        except BackgroundJobError as error:
            _fail(error.code)
        response.headers.update(_NO_STORE)
        if cancelled.pending:
            response.status_code = status.HTTP_202_ACCEPTED
        return _view(cancelled.job)

    return router


__all__ = (
    "BackgroundJobAcceptResponse",
    "BackgroundJobCreateRequest",
    "BackgroundJobListResponse",
    "BackgroundJobResponse",
    "make_router",
)
