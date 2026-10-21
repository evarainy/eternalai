"""Process-local browser admission at the ordinary authenticated Gateway boundary."""

from __future__ import annotations

import re
from contextlib import AbstractAsyncContextManager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeAlias, get_args
from uuid import UUID

from app.browser_skill.models import BrowserOwner
from app.ports.auth import AuthenticatedSessionContext, Principal, authenticated_session
from app.ports.error_codes import ErrorCode
from app.ports.response_envelope import ResponseEnvelope

BrowserDiagnosticStage: TypeAlias = Literal[
    "context",
    "input",
    "publication",
    "binding",
    "admission",
    "handoff",
    "llm",
]
BrowserDiagnosticReason: TypeAlias = Literal[
    "context_mismatch",
    "input_invalid",
    "input_missing",
    "capability_unavailable",
    "publication_stale",
    "identity_unbound",
    "identity_expired",
    "identity_revoked",
    "target_denied",
    "readonly_required",
    "policy_denied",
    "request_key_conflict",
    "admission_timeout",
    "admission_uncertain",
    "provider_unavailable",
    "handoff_unavailable",
    "audit_unavailable",
    "unknown_exception",
    "llm_timeout",
    "llm_unavailable",
    "model_mismatch",
]


@dataclass(frozen=True, slots=True)
class BrowserGatewayDiagnostic:
    stage: BrowserDiagnosticStage
    reason: BrowserDiagnosticReason

    def __post_init__(self) -> None:
        if self.stage not in get_args(BrowserDiagnosticStage) or self.reason not in get_args(
            BrowserDiagnosticReason
        ):
            raise ValueError("browser_diagnostic_invalid")

    def attributes(self) -> dict[str, str]:
        return {"browser_stage": self.stage, "browser_reason": self.reason}


@dataclass(slots=True, repr=False)
class BrowserIntentAttempt:
    """Request-local accounting only; durable CAS, never this flag, decides closure."""

    task_id: str | None = None
    trace_id: str | None = None
    admission: BrowserGatewayAdmission | None = None
    message: str | None = None
    channel: Literal["web", "cli", "api", "mock"] | None = None
    finalization_deadline: float | None = None


@dataclass(frozen=True, slots=True, repr=False)
class BrowserRequestScope:
    verified: AuthenticatedSessionContext
    bound_session: str
    client_request_id: str
    attempt: BrowserIntentAttempt = field(default_factory=BrowserIntentAttempt)

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_request_id", str(UUID(self.client_request_id)))
        if not self.bound_session or authenticated_session.get() != self.verified:
            raise ValueError("browser_context_invalid")

    def matches(self, principal: Principal, session_id: str) -> bool:
        return (
            authenticated_session.get() == self.verified
            and principal == self.verified.principal
            and session_id == self.bound_session
        )

    def capture_admission(self, response: ResponseEnvelope) -> BrowserGatewayAdmission:
        principal = self.verified.principal
        owner = BrowserOwner(
            tenant_id=principal.org_ctx.tenant_id,
            user_id=principal.ai_user_id,
            session_id=self.bound_session,
        )
        data = response.data
        if (
            not self.matches(principal, self.bound_session)
            or response.status != "running"
            or response.schema_version != "phase0.sdui.v1"
            or response.session_id != self.bound_session
            or type(data) is not dict
            or data.get("task_id") != response.task_id
        ):
            raise ValueError("browser_admission_invalid")
        if data.get("kind") == "accepted" and set(data) == {
            "kind",
            "task_id",
            "run_id",
            "state_revision",
        }:
            admission = BrowserGatewayAdmission(
                owner,
                response.task_id,
                response.trace_id,
                "accepted",
                data["run_id"],
                data["state_revision"],
            )
        elif data.get("kind") == "pending" and set(data) == {"kind", "task_id"}:
            admission = BrowserGatewayAdmission(
                owner, response.task_id, response.trace_id, "pending"
            )
        else:
            raise ValueError("browser_admission_invalid")
        previous = self.attempt.admission
        if previous is not None and (
            previous.task_id != admission.task_id
            or previous.trace_id != admission.trace_id
            or previous.run_id is not None
            and previous.run_id != admission.run_id
        ):
            raise ValueError("browser_admission_changed")
        self.attempt.admission = admission
        return admission


browser_request_scope: ContextVar[BrowserRequestScope | None] = ContextVar(
    "browser_gateway_request_scope",
    default=None,
)


@dataclass(frozen=True, slots=True, repr=False)
class BrowserGatewayInvocation:
    scope: BrowserRequestScope
    capability_id: str
    capability_version: str
    task_id: str
    trace_id: str

    @property
    def owner(self) -> BrowserOwner:
        principal = self.scope.verified.principal
        return BrowserOwner(
            tenant_id=principal.org_ctx.tenant_id,
            user_id=principal.ai_user_id,
            session_id=self.scope.bound_session,
        )


@dataclass(frozen=True, slots=True, repr=False)
class BrowserGatewayAdmission:
    owner: BrowserOwner
    task_id: str
    trace_id: str
    kind: Literal["accepted", "pending"]
    run_id: str | None = None
    state_revision: int | None = None

    def __post_init__(self) -> None:
        for value in (self.task_id, self.trace_id):
            if re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value) is None:
                raise ValueError("browser_admission_invalid")
        if self.kind == "accepted":
            if (
                type(self.run_id) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{1,96}", self.run_id) is None
                or type(self.state_revision) is not int
                or not 0 <= self.state_revision <= 9007199254740991
            ):
                raise ValueError("browser_admission_invalid")
        elif self.kind != "pending" or self.run_id is not None or self.state_revision is not None:
            raise ValueError("browser_admission_invalid")

    def data(self) -> dict[str, object]:
        result: dict[str, object] = {"kind": self.kind, "task_id": self.task_id}
        if self.kind == "accepted":
            result.update(run_id=self.run_id, state_revision=self.state_revision)
        return result


@dataclass(frozen=True, slots=True)
class BrowserGatewayRegistration:
    tenant_id: str
    user_id: str
    capability_id: str
    capability_version: str
    skill_id: str
    skill_version: str
    source_kind: Literal["registered_synthetic_fixture", "registered_site"] = (
        "registered_synthetic_fixture"
    )
    target_system: Literal["oa"] = "oa"
    effect: Literal["read_only", "fill_stop"] = "read_only"

    def __post_init__(self) -> None:
        if (
            (self.source_kind, self.effect)
            not in {
                ("registered_synthetic_fixture", "read_only"),
                ("registered_site", "fill_stop"),
            }
            or self.target_system != "oa"
            or any(
                not v or v != v.strip()
                for v in (
                    self.tenant_id,
                    self.user_id,
                    self.capability_id,
                    self.capability_version,
                    self.skill_id,
                    self.skill_version,
                )
            )
        ):
            raise ValueError("browser_registration_invalid")


class BrowserIntentHandoffPort(Protocol):
    def try_request_route(self, scope: BrowserRequestScope) -> AbstractAsyncContextManager[bool]:
        """Cross-process in-flight exclusion for this owner/session/request key.

        A true result holds the route lock until context exit. A false result
        permits only canonical lookup, never new intent parsing or dispatch.
        The lock is coordination, not ownership or execution authority.
        """
        ...

    async def commit(
        self,
        invocation: BrowserGatewayInvocation,
        admission: BrowserGatewayAdmission,
    ) -> None: ...

    async def fail_if_open(
        self,
        scope: BrowserRequestScope,
        error_code: ErrorCode,
    ) -> Literal["failed", "handed_off", "terminal", "absent"]: ...
