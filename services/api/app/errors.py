from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from services.api.app.schemas.common import BadRequestErrorResponse, NotFoundErrorResponse


class ResourceNotFoundError(Exception):
    def __init__(self, *, resource: str, resource_id: str, message: str) -> None:
        self.resource = resource
        self.resource_id = resource_id
        self.message = message
        super().__init__(message)


async def handle_request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors: list[dict[str, Any]] = []
    for error in exc.errors():
        sanitized = dict(error)
        ctx = sanitized.get("ctx")
        if isinstance(ctx, dict):
            sanitized["ctx"] = {key: str(value) for key, value in ctx.items()}
        errors.append(sanitized)

    payload = BadRequestErrorResponse.model_validate(
        {
            "error": {
                "code": "invalid_request",
                "message": "Invalid request payload",
                "details": {"errors": errors},
            }
        }
    )
    return JSONResponse(status_code=400, content=payload.model_dump(mode="json"))


async def handle_not_found(_: Request, exc: ResourceNotFoundError) -> JSONResponse:
    payload = NotFoundErrorResponse.model_validate(
        {
            "error": {
                "code": "not_found",
                "message": exc.message,
                "resource": exc.resource,
                "resource_id": exc.resource_id,
            }
        }
    )
    return JSONResponse(status_code=404, content=payload.model_dump(mode="json"))


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(ResourceNotFoundError, handle_not_found)
