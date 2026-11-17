"""The caller's own saved read flows (功能): trial, save, list, enable, run, takeover.

Identity comes only from the signed session; every flow is looked up by its
owner, so another user's flow is indistinguishable from a missing one (404).
Trial and run execute read-only queries with the caller's own identity.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Literal, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.api.v1.csrf import PrincipalDependency
from app.api.v1.runtime import _bind_session
from app.ports.auth import Principal
from app.ports.user_skills import UserSkillError, UserSkillOwner
from app.user_skills.runner import RunContext
from app.user_skills.service import UserSkillFailure, UserSkillService

_log = logging.getLogger(__name__)
_NO_STORE = {"Cache-Control": "no-store"}
API_RUN_SECONDS = 120.0
SessionId = Field(min_length=1, max_length=512)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpdateTarget(_Strict):
    mode: Literal["new", "update"]
    skill_id: str | None = Field(default=None, max_length=64)
    expected_current_version: int | None = Field(default=None, ge=1)


class TrialRequest(_Strict):
    session_id: str = SessionId
    binding_choices: dict[str, str] = Field(default_factory=dict)
    for_each_choices: dict[str, bool] = Field(default_factory=dict)
    column_ids: list[str] = Field(min_length=1, max_length=60)
    inputs: dict[str, JsonValue] = Field(default_factory=dict)
    name: str = Field(min_length=1, max_length=40)
    purpose: str = Field(min_length=1, max_length=120)
    rules: str = Field(min_length=1, max_length=300)
    target: UpdateTarget


class TrialResponse(BaseModel):
    trial_id: str
    result: dict[str, JsonValue]
    savable: bool
    not_savable_reason: str | None = None


class SaveRequest(_Strict):
    session_id: str = SessionId
    trial_id: str = Field(min_length=1, max_length=64)


class SaveResponse(BaseModel):
    skill_id: str
    version: int
    state: Literal["usable", "paused", "disabled"]


class SkillForm(BaseModel):
    schema_: dict[str, JsonValue] = Field(alias="schema")
    labels: dict[str, str]


class SkillListItem(BaseModel):
    skill_id: str
    name: str
    purpose: str
    state: Literal["usable", "paused", "disabled"]
    pause_reason_label: str | None = None
    current_version: int
    updated_at: str | None = None
    exportable: bool
    form: SkillForm


class SkillListResponse(BaseModel):
    items: list[SkillListItem]


class StateRequest(_Strict):
    state: Literal["usable", "disabled"]
    expected_current_version: int = Field(ge=1)


class StateResponse(BaseModel):
    skill_id: str
    state: Literal["usable", "paused", "disabled"]
    current_version: int


class RunRequest(_Strict):
    session_id: str = SessionId
    version: int = Field(ge=1)
    input: dict[str, JsonValue] = Field(default_factory=dict)


class RunResponse(BaseModel):
    user_skill: dict[str, JsonValue]


class TakeoverRequest(_Strict):
    session_id: str = SessionId


def _fail(status_code: int, code: str, message: str) -> NoReturn:
    raise HTTPException(status_code, detail={"code": code, "message": message}, headers=_NO_STORE)


def _owner(principal: Principal) -> UserSkillOwner:
    return UserSkillOwner(tenant_id=principal.org_ctx.tenant_id, ai_user_id=principal.ai_user_id)


def _context(principal: Principal, session_id: str, entry: Literal["trial", "run"]) -> RunContext:
    return RunContext(
        tenant_id=principal.org_ctx.tenant_id,
        ai_user_id=principal.ai_user_id,
        session_id=session_id,
        channel="web",
        entry=entry,
        deadline=asyncio.get_running_loop().time() + API_RUN_SECONDS,
    )


def make_router(
    service: UserSkillService | None,
    require_principal: PrincipalDependency,
    *,
    session_binder: Callable[[Principal, str], str] | None = None,
) -> APIRouter:
    router = APIRouter()
    errors: dict[int | str, dict[str, Any]] = {
        404: {"description": "Not found"},
        409: {"description": "Conflict"},
        410: {"description": "Expired"},
        422: {"description": "Invalid"},
        503: {"description": "Unavailable"},
    }

    def configured() -> UserSkillService:
        if service is None:
            _fail(503, "user_skills_unavailable", "功能暂不可用")
        return service

    def bind(principal: Principal, session_id: str) -> tuple[str, str, str]:
        bound = _bind_session(
            principal=principal, requested_session_id=session_id, session_binder=session_binder
        )
        return (principal.org_ctx.tenant_id, principal.ai_user_id, bound)

    async def guarded(call: Any) -> Any:
        try:
            return await call
        except UserSkillFailure as failure:
            _fail(failure.status, failure.code, failure.label)
        except UserSkillError as error:
            _log.warning("user_skills op=store result=unavailable error=%s", error.code)
            _fail(503, "user_skills_unavailable", "功能存储暂不可用")

    @router.post("/proposals/{proposal_id}/trials", response_model=TrialResponse, responses=errors)
    async def trial(
        proposal_id: str,
        body: TrialRequest,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> TrialResponse:
        key = bind(principal, body.session_id)
        raw = body.model_dump(mode="json", exclude={"session_id"})
        outcome = await guarded(
            configured().trial(
                key, _owner(principal), _context(principal, key[2], "trial"), proposal_id, raw
            )
        )
        response.headers.update(_NO_STORE)
        return TrialResponse(**outcome)

    @router.post(
        "", response_model=SaveResponse, status_code=status.HTTP_201_CREATED, responses=errors
    )
    async def save(
        body: SaveRequest,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> SaveResponse:
        key = bind(principal, body.session_id)
        saved = await guarded(configured().save(key, _owner(principal), body.trial_id))
        response.headers.update(_NO_STORE)
        return SaveResponse(**saved)

    @router.get("", response_model=SkillListResponse, responses=errors)
    async def list_skills(
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> SkillListResponse:
        items = await guarded(configured().list(_owner(principal)))
        response.headers.update(_NO_STORE)
        return SkillListResponse(items=[SkillListItem(**item) for item in items])

    @router.patch("/{skill_id}", response_model=StateResponse, responses=errors)
    async def set_state(
        skill_id: str,
        body: StateRequest,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> StateResponse:
        changed = await guarded(
            configured().set_enabled(
                _owner(principal), skill_id, body.state, body.expected_current_version
            )
        )
        response.headers.update(_NO_STORE)
        return StateResponse(**changed)

    @router.post("/{skill_id}/runs", response_model=RunResponse, responses=errors)
    async def run(
        skill_id: str,
        body: RunRequest,
        response: Response,
        principal: Principal = Depends(require_principal),
    ) -> RunResponse:
        key = bind(principal, body.session_id)
        payload, _failed, _code = await guarded(
            configured().run_saved(
                key,
                _owner(principal),
                _context(principal, key[2], "run"),
                skill_id,
                body.version,
                body.input,
            )
        )
        response.headers.update(_NO_STORE)
        return RunResponse(user_skill=payload)

    @router.post(
        "/runs/{run_id}/takeover",
        status_code=status.HTTP_204_NO_CONTENT,
        response_class=Response,
        responses=errors,
    )
    async def takeover(
        run_id: str,
        body: TakeoverRequest,
        principal: Principal = Depends(require_principal),
    ) -> Response:
        key = bind(principal, body.session_id)
        if not configured().request_takeover(key, run_id):
            _fail(404, "run_not_found", "这次查询已过期，请重新查询")
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_NO_STORE)

    return router


__all__ = ("make_router",)
