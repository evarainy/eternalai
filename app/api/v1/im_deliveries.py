"""Authenticated text preview, explicit confirmation and recipient verification routes."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.csrf import PrincipalDependency
from app.ports.auth import Principal
from app.ports.im_deliveries import (
    DeliveryPrepare,
    DeliveryRecipient,
    DeliveryVerification,
    DeliveryView,
    IMDeliveryError,
)

if TYPE_CHECKING:
    from app.im_deliveries import IMDeliveryService

_NO_STORE = {"Cache-Control": "no-store"}


class IMDeliveryConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class IMDeliveryList(BaseModel):
    items: list[DeliveryView]


class IMDeliveryRecipients(BaseModel):
    items: list[DeliveryRecipient]


class IMDeliveryErrorDetail(BaseModel):
    code: str
    message: str


class IMDeliveryErrorResponse(BaseModel):
    detail: IMDeliveryErrorDetail


def _fail(code: str, status_code: int) -> NoReturn:
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": "送核请求未完成，请核对原送核记录和当前状态。"},
        headers=_NO_STORE,
    )


class _IMDeliveryRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                _fail("im_delivery_request_invalid", 422)
            except IMDeliveryError as error:
                _fail(error.code, error.status_code)
            response.headers.update(_NO_STORE)
            return response

        return handle


def make_router(
    service: IMDeliveryService | None, require_principal: PrincipalDependency,
) -> APIRouter:
    """The application supplies its existing Cookie/CSRF protected principal dependency."""
    router = APIRouter(route_class=_IMDeliveryRoute)
    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": IMDeliveryErrorResponse} for code in (404, 409, 422, 503)
    }

    def configured() -> IMDeliveryService:
        if service is None:
            raise IMDeliveryError("im_delivery_unavailable", 503)
        return service

    @router.post(
        "", response_model=DeliveryView, responses=errors, operation_id="prepareIMDelivery",
    )
    async def prepare_delivery(
        body: DeliveryPrepare, principal: Principal = Depends(require_principal),
    ) -> DeliveryView:
        return await configured().prepare(principal, body)

    @router.get(
        "", response_model=IMDeliveryList, responses=errors, operation_id="listIMDeliveries",
    )
    async def list_deliveries(
        principal: Principal = Depends(require_principal),
    ) -> IMDeliveryList:
        return IMDeliveryList(items=await configured().list(principal))

    @router.get(
        "/recipients", response_model=IMDeliveryRecipients, responses=errors,
        operation_id="listIMDeliveryRecipients",
    )
    async def list_recipients(
        principal: Principal = Depends(require_principal),
    ) -> IMDeliveryRecipients:
        return IMDeliveryRecipients(items=await configured().recipients(principal))

    @router.get(
        "/{delivery_id}", response_model=DeliveryView, responses=errors,
        operation_id="getIMDelivery",
    )
    async def get_delivery(
        delivery_id: UUID, principal: Principal = Depends(require_principal),
    ) -> DeliveryView:
        return await configured().get(principal, delivery_id)

    @router.post(
        "/{delivery_id}/confirm", response_model=DeliveryView, responses=errors,
        operation_id="confirmIMDelivery",
    )
    async def confirm_delivery(
        delivery_id: UUID, body: IMDeliveryConfirm,
        principal: Principal = Depends(require_principal),
    ) -> DeliveryView:
        return await configured().confirm(principal, delivery_id, body.confirmation_sha256)

    @router.post(
        "/{delivery_id}/cancel", response_model=DeliveryView, responses=errors,
        operation_id="cancelIMDelivery",
    )
    async def cancel_delivery(
        delivery_id: UUID, principal: Principal = Depends(require_principal),
    ) -> DeliveryView:
        return await configured().cancel(principal, delivery_id)

    @router.post(
        "/{delivery_id}/reconcile", response_model=DeliveryView, responses=errors,
        operation_id="reconcileIMDelivery",
    )
    async def reconcile_delivery(
        delivery_id: UUID, principal: Principal = Depends(require_principal),
    ) -> DeliveryView:
        return await configured().reconcile(principal, delivery_id)

    @router.post(
        "/{delivery_id}/verify", response_model=DeliveryView, responses=errors,
        operation_id="verifyIMDelivery",
    )
    async def verify_delivery(
        delivery_id: UUID, body: DeliveryVerification,
        principal: Principal = Depends(require_principal),
    ) -> DeliveryView:
        return await configured().verify(principal, delivery_id, body)

    return router
