"""API route assembly and contract-backed registration metadata."""

from dataclasses import dataclass

from fastapi import APIRouter

from services.api.app.routes.system import build_system_router
from services.api.app.routes.v1 import build_v1_router


@dataclass(frozen=True)
class RouterRegistration:
    name: str
    prefix: str
    router: APIRouter


@dataclass(frozen=True)
class ContractRoute:
    operation_id: str
    method: str
    path: str


def get_router_registrations(*, environment: str) -> tuple[RouterRegistration, ...]:
    return (
        RouterRegistration(
            name="system",
            prefix="",
            router=build_system_router(environment=environment),
        ),
        RouterRegistration(
            name="public_v1",
            prefix="/v1",
            router=build_v1_router(),
        ),
    )


def get_contract_routes() -> tuple[ContractRoute, ...]:
    return (
        ContractRoute(
            operation_id="getApiHealth",
            method="GET",
            path="/health",
        ),
        ContractRoute(
            operation_id="createSession",
            method="POST",
            path="/v1/sessions",
        ),
        ContractRoute(
            operation_id="getSession",
            method="GET",
            path="/v1/sessions/{session_id}",
        ),
        ContractRoute(
            operation_id="submitMessage",
            method="POST",
            path="/v1/sessions/{session_id}/messages",
        ),
        ContractRoute(
            operation_id="getTask",
            method="GET",
            path="/v1/tasks/{task_id}",
        ),
    )
