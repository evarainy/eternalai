"""Thin authenticated HTTP adapter for periodic Work Object dispatch."""

from __future__ import annotations

import json
from typing import Annotated, Any, TypeAlias
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BeforeValidator

from app.api.v1.auth import PrincipalDependency
from app.api.v1.work_objects import (
    ProtectedWorkRoute,
    WorkObjectError,
    _lifecycle_error,
    mutation_schema,
    request_etag,
    request_key,
)
from app.ports.auth import Principal
from app.ports.work_object_recurrence import (
    SeriesHistoryPage,
    SeriesMutationResult,
    SeriesOccurrencePage,
    SeriesPage,
    SeriesRequest,
    SeriesView,
)
from app.work_dispatch.recurrence import WorkObjectSeriesService


def _series_body(value: Any) -> Any:
    return SeriesRequest.model_validate_json(json.dumps(value))


SeriesBody: TypeAlias = Annotated[SeriesRequest, BeforeValidator(_series_body)]


def make_router(
    service: WorkObjectSeriesService | None, require_principal: PrincipalDependency
) -> APIRouter:
    router = APIRouter(route_class=ProtectedWorkRoute)

    def configured() -> WorkObjectSeriesService:
        if service is None:
            _lifecycle_error("work_object_unavailable")
        return service

    errors: dict[int | str, dict[str, Any]] = {
        code: {"model": WorkObjectError} for code in (401, 403, 404, 409, 412, 422, 428, 503)
    }

    @router.post(
        "",
        response_model=SeriesMutationResult,
        status_code=201,
        responses={
            **errors,
            200: {
                "model": SeriesMutationResult,
                "headers": {"ETag": {"schema": {"type": "string"}}},
            },
        },
        openapi_extra=mutation_schema(versioned=False),
    )
    async def create_series(
        body: SeriesBody,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> SeriesMutationResult:
        result = await configured().create(
            principal, request=body, idempotency_key=request_key(request)
        )
        response.status_code = 200 if result.replayed else 201
        response.headers["ETag"] = result.etag
        return result

    @router.get("", response_model=SeriesPage, responses=errors)
    async def list_series(
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        principal: Principal = Depends(require_principal),
    ) -> SeriesPage:
        return await configured().list(principal, cursor=cursor, limit=limit)

    @router.get(
        "/{series_id}",
        response_model=SeriesView,
        responses={
            **errors,
            200: {"model": SeriesView, "headers": {"ETag": {"schema": {"type": "string"}}}},
        },
    )
    async def get_series(
        series_id: UUID,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> SeriesView:
        result = await configured().get(principal, series_id)
        response.headers["ETag"] = result.etag
        return result

    @router.put(
        "/{series_id}",
        response_model=SeriesMutationResult,
        responses={
            **errors,
            200: {
                "model": SeriesMutationResult,
                "headers": {"ETag": {"schema": {"type": "string"}}},
            },
        },
        openapi_extra=mutation_schema(prefix="wosr"),
    )
    async def update_series(
        series_id: UUID,
        body: SeriesBody,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> SeriesMutationResult:
        result = await configured().update(
            principal,
            series_id,
            request=body,
            if_match=request_etag(request),
            idempotency_key=request_key(request),
        )
        response.headers["ETag"] = result.etag
        return result

    @router.post(
        "/{series_id}/enable",
        response_model=SeriesMutationResult,
        responses={
            **errors,
            200: {
                "model": SeriesMutationResult,
                "headers": {"ETag": {"schema": {"type": "string"}}},
            },
        },
        openapi_extra=mutation_schema(prefix="wosr"),
    )
    async def enable_series(
        series_id: UUID,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> SeriesMutationResult:
        result = await configured().set_enabled(
            principal,
            series_id,
            enabled=True,
            if_match=request_etag(request),
            idempotency_key=request_key(request),
        )
        response.headers["ETag"] = result.etag
        return result

    @router.post(
        "/{series_id}/disable",
        response_model=SeriesMutationResult,
        responses={
            **errors,
            200: {
                "model": SeriesMutationResult,
                "headers": {"ETag": {"schema": {"type": "string"}}},
            },
        },
        openapi_extra=mutation_schema(prefix="wosr"),
    )
    async def disable_series(
        series_id: UUID,
        request: Request,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> SeriesMutationResult:
        result = await configured().set_enabled(
            principal,
            series_id,
            enabled=False,
            if_match=request_etag(request),
            idempotency_key=request_key(request),
        )
        response.headers["ETag"] = result.etag
        return result

    @router.get("/{series_id}/history", response_model=SeriesHistoryPage, responses=errors)
    async def series_history(
        series_id: UUID,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        principal: Principal = Depends(require_principal),
    ) -> SeriesHistoryPage:
        return await configured().history(principal, series_id, cursor=cursor, limit=limit)

    @router.get("/{series_id}/occurrences", response_model=SeriesOccurrencePage, responses=errors)
    async def series_occurrences(
        series_id: UUID,
        cursor: Annotated[str | None, Query(max_length=2048)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        principal: Principal = Depends(require_principal),
    ) -> SeriesOccurrencePage:
        return await configured().occurrences(principal, series_id, cursor=cursor, limit=limit)

    return router
