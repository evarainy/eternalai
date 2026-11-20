"""Read and manage personal memories for the authenticated owner."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from app.api.v1.csrf import PrincipalDependency
from app.ports.auth import Principal
from app.ports.user_memories import (
    MEMORY_CONTENT_LIMIT,
    MemoryOwner,
    UserMemory,
    UserMemoryStore,
    UserMemoryStoreError,
)
from app.user_memories import MemoryContentError, MemoryInputReason, validated_content

_NO_STORE = {"Cache-Control": "no-store"}


class MemoryInputErrorDetail(BaseModel):
    code: Literal["memory_input_invalid"]
    reason: MemoryInputReason


class MemoryInputErrorResponse(BaseModel):
    detail: MemoryInputErrorDetail


def _input_reason(error: RequestValidationError) -> MemoryInputReason:
    for issue in error.errors():
        if issue["type"] == "string_too_long":
            return "too_long"
        rejection = issue.get("ctx", {}).get("error")
        if isinstance(rejection, MemoryContentError):
            return rejection.reason
    return "empty"


class _PrivateMemoryRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError as error:
                detail = {"code": "memory_input_invalid"}
                if request.method == "PATCH":
                    detail["reason"] = _input_reason(error)
                raise HTTPException(422, detail, headers=_NO_STORE) from None
            except UserMemoryStoreError:
                raise _unavailable() from None
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **_NO_STORE}
                raise
            response.headers.update(_NO_STORE)
            return response

        return handle


class MemoryListResponse(BaseModel):
    items: list[UserMemory]


class EditMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MEMORY_CONTENT_LIMIT),
        AfterValidator(validated_content),
    ]


def _not_found() -> HTTPException:
    return HTTPException(404, {"code": "memory_not_found"})


def _unavailable() -> HTTPException:
    return HTTPException(503, {"code": "memory_store_unavailable"}, headers=_NO_STORE)


def make_router(
    store: UserMemoryStore | None, require_principal: PrincipalDependency,
) -> APIRouter:
    router = APIRouter(
        route_class=_PrivateMemoryRoute,
        responses={503: {"description": "Personal memories unavailable"}},
    )
    mutation_errors: dict[int | str, dict[str, Any]] = {
        404: {"description": "Memory not found"},
        422: {"description": "Invalid memory input"},
    }

    def configured() -> UserMemoryStore:
        if store is None:
            raise _unavailable()
        return store

    @router.get("")
    async def list_memories(
        principal: Principal = Depends(require_principal),
    ) -> MemoryListResponse:
        owner = MemoryOwner(principal.org_ctx.tenant_id, principal.ai_user_id)
        items = await configured().list_active(owner)
        return MemoryListResponse(items=items)

    @router.patch(
        "/{id}",
        response_model=UserMemory,
        responses={
            **mutation_errors,
            422: {"description": "Invalid memory input", "model": MemoryInputErrorResponse},
        },
    )
    async def edit_memory(
        id: UUID,
        body: EditMemoryRequest,
        principal: Principal = Depends(require_principal),
    ) -> UserMemory:
        owner = MemoryOwner(principal.org_ctx.tenant_id, principal.ai_user_id)
        row = await configured().update(owner, id, body.content)
        if row is None:
            raise _not_found()
        return row

    @router.delete("/{id}", status_code=204, response_class=Response, responses=mutation_errors)
    async def delete_memory(
        id: UUID,
        principal: Principal = Depends(require_principal),
    ) -> Response:
        owner = MemoryOwner(principal.org_ctx.tenant_id, principal.ai_user_id)
        if not await configured().delete(owner, id):
            raise _not_found()
        return Response(status_code=204)

    return router
