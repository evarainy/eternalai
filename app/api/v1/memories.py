"""Read and manage personal memories for the authenticated owner."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

from app.api.v1.csrf import PrincipalDependency
from app.ports.auth import Principal
from app.ports.user_memories import (
    MEMORY_CONTENT_LIMIT,
    UserMemory,
    UserMemoryStore,
    UserMemoryStoreError,
)
from app.user_memories import memory_owner, validated_content

_NO_STORE = {"Cache-Control": "no-store"}


class _PrivateMemoryRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                raise HTTPException(
                    422, {"code": "memory_input_invalid"}, headers=_NO_STORE
                ) from None
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
        owner = memory_owner(principal)
        items = [] if owner is None else await configured().list_active(owner)
        return MemoryListResponse(items=items)

    @router.patch("/{id}", response_model=UserMemory, responses=mutation_errors)
    async def edit_memory(
        id: UUID,
        body: EditMemoryRequest,
        principal: Principal = Depends(require_principal),
    ) -> UserMemory:
        owner = memory_owner(principal)
        if owner is None:
            raise _not_found()
        row = await configured().update(owner, id, body.content)
        if row is None:
            raise _not_found()
        return row

    @router.delete("/{id}", status_code=204, response_class=Response, responses=mutation_errors)
    async def delete_memory(
        id: UUID,
        principal: Principal = Depends(require_principal),
    ) -> Response:
        owner = memory_owner(principal)
        if owner is None or not await configured().delete(owner, id):
            raise _not_found()
        return Response(status_code=204)

    return router
