"""File artifact download: same-origin, cookie authenticated, owner only.

Another user's artifact, a missing one and a malformed id all answer the same 404. The
pinned descriptor is not what gets sent: ``FileResponse`` reopens the path, and holding
the descriptor keeps the cleanup job from deleting the file until sending has finished
(Windows opens files without delete sharing).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Coroutine
from typing import Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel
from starlette.types import Receive, Scope, Send

from app.api.v1.csrf import PrincipalDependency
from app.file_artifacts import FileArtifactService
from app.ports.auth import Principal
from app.ports.file_artifacts import FileArtifactDownload, FileArtifactError

_NO_STORE = {"Cache-Control": "no-store"}
_ERRORS: dict[str, tuple[int, str]] = {
    "file_artifact_not_found": (404, "没有找到该文件。"),
    "file_artifact_expired": (410, "该文件已过期或不可用。"),
    "file_artifact_unavailable": (503, "文件服务未启用。"),
}


class FileArtifactErrorDetail(BaseModel):
    code: str
    message: str


class FileArtifactErrorResponse(BaseModel):
    detail: FileArtifactErrorDetail


def _fail(code: str) -> NoReturn:
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
        try:
            await super().__call__(scope, receive, send)
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


def make_router(
    service: FileArtifactService | None, require_principal: PrincipalDependency
) -> APIRouter:
    router = APIRouter(route_class=_FileArtifactRoute)

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
