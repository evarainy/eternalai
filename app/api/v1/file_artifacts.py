"""File artifacts: owner-only download and raw-body chat uploads (same origin, cookies).

Another user's artifact, a missing one and a malformed id all answer the same 404. The
pinned descriptor is not what gets sent: ``FileResponse`` reopens the path, and holding
the descriptor keeps the cleanup job from deleting the file until sending has finished
(Windows opens files without delete sharing).

A chat upload is one xlsx per request (FOUNDATION_PLAN §3.2): the body is counted while it
streams, with a 60-second idle limit and a 10-minute budget from the request start that
also bounds inspection and sealing. File names and paths are never logged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.requests import ClientDisconnect
from starlette.types import Message, Receive, Scope, Send

from app.api.v1.csrf import PrincipalDependency
from app.api.v1.runtime import _bind_session
from app.file_artifacts import FileArtifactService
from app.ports.auth import Principal
from app.ports.file_artifacts import (
    CHAT_UPLOAD_MAX_BYTES,
    XLSX_MEDIA_TYPE,
    ChatUploadResult,
    FileArtifactDownload,
    FileArtifactError,
)

_LOGGER = logging.getLogger("uvicorn.error.file_artifacts")
_NO_STORE = {"Cache-Control": "no-store"}
_UPLOAD_BUDGET_SECONDS = 600.0
_UPLOAD_IDLE_SECONDS = 60.0
_QUOTA = "chat_upload_quota_exceeded"
_ERRORS: dict[str, tuple[int, str]] = {
    "file_artifact_not_found": (404, "没有找到该文件。"),
    "file_artifact_expired": (410, "该文件已过期或不可用。"),
    "file_artifact_unavailable": (503, "文件服务未启用。"),
    "file_artifact_reservation_lost": (503, "上传未完成，请重新上传。"),
    "file_artifact_seal_outcome_unknown": (
        503,
        "暂时无法确认上传结果，请稍后重新上传同一文件。",
    ),
    "chat_upload_request_invalid": (422, "上传请求不完整，请重新选择文件上传。"),
    "chat_upload_type_invalid": (
        422,
        "只支持 .xlsx 文件；.xls 或 WPS 表格请先另存为 .xlsx。",
    ),
    "chat_upload_encrypted": (422, "文件设有密码，请取消密码后再上传。"),
    "chat_upload_macro_or_external": (
        422,
        "文件含宏、外部链接或嵌入对象，请另存为普通 .xlsx 后再上传。",
    ),
    "chat_upload_zip_limit": (422, "文件内容过大或结构过于复杂，请拆分后再上传。"),
    "chat_upload_unreadable": (
        422,
        "文件无法读取，请用 Excel 或 WPS 另存为 .xlsx 后再上传。",
    ),
    "chat_upload_inspection_timeout": (422, "文件检查超时，请拆分后再上传。"),
    "chat_upload_too_large": (413, "文件超过 20 MB，请拆分或压缩后再上传。"),
    "chat_upload_timeout": (408, "上传超时，请检查网络后重新上传。"),
    "chat_upload_aborted": (408, "上传已中断，请重新上传。"),
    "chat_upload_idempotency_conflict": (
        409,
        "该文件与之前的上传不一致，请重新选择文件上传。",
    ),
    "chat_upload_in_progress": (409, "该文件正在上传，请稍候。"),
}
# Two internal quota codes share one public code with different messages.
_QUOTA_MESSAGES = {
    "chat_upload_quota_session": "本会话的附件已达 10 个上限，请新建对话后再上传。",
    "chat_upload_quota_owner": "你的附件总量已达 200 MB 上限，请等待旧附件过期后再上传。",
}


class FileArtifactErrorDetail(BaseModel):
    code: str
    message: str


class FileArtifactErrorResponse(BaseModel):
    detail: FileArtifactErrorDetail


class SheetView(BaseModel):
    name: str
    max_row: int | None
    max_column: int | None


class ChatUploadResponse(BaseModel):
    artifact_id: UUID
    filename: str
    size_bytes: int
    sheets: list[SheetView]
    expires_at: datetime


def _fail(code: str) -> NoReturn:
    if code in _QUOTA_MESSAGES:
        raise HTTPException(
            status_code=409,
            detail={"code": _QUOTA, "message": _QUOTA_MESSAGES[code]},
            headers=_NO_STORE,
        )
    http_status, message = _ERRORS[code]
    raise HTTPException(
        status_code=http_status,
        detail={"code": code, "message": message},
        headers=_NO_STORE,
    )


class PinnedFileResponse(FileResponse):
    """A ``FileResponse`` that releases the pinned descriptor once it has been sent."""

    def __init__(self, download: FileArtifactDownload) -> None:
        super().__init__(
            download.path,
            stat_result=download.stat_result,
            media_type=download.media_type,
            filename=download.display_filename,
            content_disposition_type="attachment",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )
        self._pin_fd = download.pin_fd

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_no_store(message: Message) -> None:
            # Starlette answers bad or unsatisfiable Range headers with a fresh response
            # (400/416) that does not carry this response's headers; keep every start
            # message uncacheable.
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"cache-control"
                ]
                headers.append((b"cache-control", b"private, no-store"))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await super().__call__(scope, receive, send_no_store)
        finally:
            os.close(self._pin_fd)


class _FileArtifactRoute(APIRoute):
    """A malformed id is indistinguishable from a missing artifact and is never echoed."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError:
                _fail("file_artifact_not_found")

        return handle


class _ChatUploadRoute(APIRoute):
    """Framework validation failures are a 422 without input echo; nothing is cached."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                _fail("chat_upload_request_invalid")
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **_NO_STORE}
                raise
            response.headers.update(_NO_STORE)
            return response

        return handle


def _idempotency_key(request: Request) -> UUID:
    values = request.headers.getlist("idempotency-key")
    try:
        if len(values) != 1:
            raise ValueError
        return UUID(values[0])
    except ValueError:
        _fail("chat_upload_request_invalid")


def _declared_too_large(request: Request) -> bool:
    declared = request.headers.get("content-length")
    return declared is not None and declared.isdigit() and int(declared) > CHAT_UPLOAD_MAX_BYTES


async def _bounded_stream(request: Request, deadline: float) -> AsyncIterator[bytes]:
    """Count the body as it streams; idle, budget and size limits raise contract errors."""

    stream = request.stream()
    received = 0
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FileArtifactError("chat_upload_timeout")
            try:
                async with asyncio.timeout(min(_UPLOAD_IDLE_SECONDS, remaining)):
                    chunk = await anext(stream)
            except StopAsyncIteration:
                return
            except TimeoutError:
                raise FileArtifactError("chat_upload_timeout") from None
            except ClientDisconnect:
                raise FileArtifactError("chat_upload_aborted") from None
            received += len(chunk)
            if received > CHAT_UPLOAD_MAX_BYTES:
                raise FileArtifactError("chat_upload_too_large")
            if chunk:
                yield chunk
    finally:
        await stream.aclose()


def _upload_response(result: ChatUploadResult) -> ChatUploadResponse:
    return ChatUploadResponse(
        artifact_id=result.artifact_id,
        filename=result.filename,
        size_bytes=result.size_bytes,
        sheets=[
            SheetView(name=sheet.name, max_row=sheet.max_row, max_column=sheet.max_column)
            for sheet in result.sheets
        ],
        expires_at=result.expires_at,
    )


def _make_upload_router(
    service: FileArtifactService | None,
    require_principal: PrincipalDependency,
    session_binder: Callable[[Principal, str], str] | None,
) -> APIRouter:
    router = APIRouter(route_class=_ChatUploadRoute)

    @router.post(
        "/chat-uploads",
        response_model=ChatUploadResponse,
        status_code=201,
        responses={
            200: {"model": ChatUploadResponse, "description": "Idempotent replay"},
            **{
                code: {"model": FileArtifactErrorResponse}
                for code in (403, 404, 408, 409, 410, 413, 422, 503)
            },
        },
        openapi_extra={
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                }
            ],
            "requestBody": {
                "required": True,
                "content": {
                    XLSX_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}
                },
            },
        },
    )
    async def upload_chat_file(
        request: Request,
        response: Response,
        session_id: Annotated[str, Query(min_length=1, max_length=512)],
        filename: Annotated[str, Query(min_length=1, max_length=255)],
        principal: Principal = Depends(require_principal),
    ) -> ChatUploadResponse:
        deadline = time.monotonic() + _UPLOAD_BUDGET_SECONDS
        bound_session_id = _bind_session(
            principal=principal,
            requested_session_id=session_id,
            session_binder=session_binder,
        )
        # .xls and .et get the same code; the message asks for "save as .xlsx".
        if not filename.strip().lower().endswith(".xlsx"):
            _fail("chat_upload_type_invalid")
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != XLSX_MEDIA_TYPE:
            _fail("chat_upload_type_invalid")
        key = _idempotency_key(request)
        if _declared_too_large(request):
            _LOGGER.info("chat_upload result=rejected code=chat_upload_too_large")
            _fail("chat_upload_too_large")
        if service is None:
            _fail("file_artifact_unavailable")
        try:
            result = await service.upload_chat_input(
                principal,
                bound_session_id,
                key,
                filename,
                _bounded_stream(request, deadline),
                deadline=deadline,
            )
        except FileArtifactError as error:
            _LOGGER.info("chat_upload result=rejected code=%s", error.code)
            if error.code not in _ERRORS and error.code not in _QUOTA_MESSAGES:
                raise
            _fail(error.code)
        _LOGGER.info(
            "chat_upload result=%s artifact=%s size=%s",
            "replayed" if result.replayed else "created",
            result.artifact_id,
            result.size_bytes,
        )
        if result.replayed:
            response.status_code = 200
        return _upload_response(result)

    return router


def make_router(
    service: FileArtifactService | None,
    require_principal: PrincipalDependency,
    *,
    session_binder: Callable[[Principal, str], str] | None = None,
) -> APIRouter:
    router = APIRouter(route_class=_FileArtifactRoute)
    router.include_router(_make_upload_router(service, require_principal, session_binder))

    @router.get(
        "/{artifact_id}/content",
        response_class=FileResponse,
        responses={
            code: {"model": FileArtifactErrorResponse} for code in (404, 410, 503)
        },
    )
    async def download_file_artifact(
        artifact_id: UUID,
        principal: Principal = Depends(require_principal),
    ) -> Response:
        if service is None:
            _fail("file_artifact_unavailable")
        try:
            download = await service.authorize_download(principal, artifact_id)
        except FileArtifactError as error:
            _fail(error.code)
        try:
            return PinnedFileResponse(download)
        except BaseException:
            os.close(download.pin_fd)
            raise

    return router


__all__ = ("PinnedFileResponse", "make_router")
