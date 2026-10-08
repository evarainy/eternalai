"""Immutable browser contracts. Vendor wire and live DOM references stay in infra.

Projection is an allowlist operation before values enter these models. Input values
have only a value_state; names/context must come from the approved Site policy.
These contracts do not confer authorization: executors must recheck current grants.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Annotated, Literal, Never, Self, SupportsIndex, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

OpaqueId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")]
# Preserve server-bound sid_v1 identities verbatim, including separators. This
# shape is not signature validation: current authorization verifies the identity.
BrowserSessionIdentity = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]{1,192}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Epoch = Annotated[int, Field(ge=0)]
SafeText = Annotated[str, Field(min_length=1, max_length=256)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Role = Literal["button", "link", "textbox", "combobox", "option", "row", "treeitem", "tab"]
DecisionStatus = Literal["selected", "abstained", "ambiguous", "unsupported"]
DecisionError = Literal[
    "unavailable",
    "overloaded",
    "timeout",
    "cancelled",
    "invalid_response",
    "model_mismatch",
    "input_unsupported",
]


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        hide_input_in_errors=True,
    )


class BrowserOwner(Contract):
    tenant_id: OpaqueId
    user_id: OpaqueId
    session_id: BrowserSessionIdentity = Field(repr=False, exclude=True)


class ScopeBinding(Contract):
    owner: BrowserOwner = Field(repr=False)
    binding_id: OpaqueId
    binding_revision: Epoch
    authorization_revision: Epoch | None = None
    lease_epoch: Epoch
    authorization_run_id: OpaqueId | None = None
    evidence_version: Literal["verified-session-v1"] | None = None

    @model_validator(mode="after")
    def authorization_mode_is_explicit(self) -> Self:
        revision_mode = (
            self.authorization_revision is not None
            and self.authorization_revision <= 9_007_199_254_740_991
            and self.authorization_run_id is None
            and self.evidence_version is None
        )
        evidence_mode = (
            self.authorization_revision is None
            and self.authorization_run_id is not None
            and self.evidence_version == "verified-session-v1"
        )
        if not (revision_mode or evidence_mode):
            raise ValueError("browser_authorization_mode_invalid")
        return self


class FrameHop(Contract):
    frame_id: OpaqueId
    frame_epoch: Epoch


class ScopeStamp(Contract):
    page_id: OpaqueId
    page_epoch: Epoch
    # Includes every ancestor, starting with the main frame.
    frame_path: Annotated[tuple[FrameHop, ...], Field(min_length=1, max_length=32)]
    region_id: OpaqueId
    region_digest: Digest


class ObservationRequest(Contract):
    """A registered region, optionally with a strict stale-snapshot precondition.

    Infra resolves the region to its actual frame/selector and generates the
    current scope. A supplied expected_scope is never treated as observed fact.
    Any mismatch fails stale; the caller must explicitly request a fresh view.
    """

    region_id: OpaqueId
    expected_scope: ScopeStamp | None = None

    @model_validator(mode="after")
    def same_region(self) -> Self:
        if self.expected_scope is not None and self.expected_scope.region_id != self.region_id:
            raise ValueError("observation_region_mismatch")
        return self


class TargetRef(Contract):
    target_id: OpaqueId
    candidate_epoch: Epoch
    scope: ScopeStamp


class Coverage(Contract):
    state: Literal["complete", "partial", "unsupported"]
    reason: Literal["complete", "pagination", "virtualized", "cross_origin", "unobservable"]
    trusted_empty: bool = False

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.state == "complete") != (self.reason == "complete"):
            raise ValueError("coverage_inconsistent")
        if self.trusted_empty and self.state != "complete":
            raise ValueError("empty_requires_complete_coverage")
        return self


class FrameObservation(Contract):
    frame: FrameHop
    coverage: Coverage
    children: Annotated[tuple[FrameObservation, ...], Field(max_length=128)] = ()


class ObservationPolicy(Contract):
    policy_id: OpaqueId
    digest: Digest
    allowed_names: tuple[SafeText, ...]
    allowed_context: tuple[SafeText, ...] = ()
    allowed_row_labels: tuple[SafeText, ...] = ()
    allowed_column_labels: tuple[SafeText, ...] = ()
    allowed_roles: tuple[Role, ...]
    maximum_candidates: Annotated[int, Field(ge=1, le=255)] = 128


class VisibleCandidate(Contract):
    ref: TargetRef
    role: Role
    name: SafeText | None = None
    context: tuple[SafeText, ...] = ()
    row_label: SafeText | None = None
    column_label: SafeText | None = None
    value_state: Literal["empty", "nonempty", "not_applicable"]
    visible: bool
    enabled: bool


class VisibleProjection(Contract):
    """One region in one actual frame; frames carries the recursive coverage tree.

    Observe nested regions separately, preserving their complete ancestor epochs.
    Candidates from different frames are never flattened into a root-frame scope.
    Unsupported child frames are explicit coverage entries, not empty results.
    """

    policy_id: OpaqueId
    policy_digest: Digest
    binding: ScopeBinding = Field(repr=False)
    scope: ScopeStamp
    frames: FrameObservation
    coverage: Coverage
    candidates: Annotated[tuple[VisibleCandidate, ...], Field(max_length=255)]


class ScopedSnapshot(VisibleProjection):
    """Produced by scope_snapshot only; consumers still revalidate before action."""


WebObservation = VisibleProjection


class DecisionCandidate(Contract):
    ref: TargetRef
    role: Role
    name: SafeText | None = None
    context: tuple[SafeText, ...] = ()
    row_label: SafeText | None = None
    column_label: SafeText | None = None


class DecisionRequest(Contract):
    task_kind: Literal["select_target"] = "select_target"
    request_id: OpaqueId
    scope: ScopeStamp
    # Approved selection descriptions, never a free-form user prompt/DOM dump.
    criteria: Annotated[tuple[SafeText, ...], Field(min_length=1, max_length=16)]
    candidates: Annotated[tuple[DecisionCandidate, ...], Field(min_length=1, max_length=255)]

    @model_validator(mode="after")
    def grounded(self) -> Self:
        ids = [candidate.ref.target_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_candidate")
        if any(candidate.ref.scope != self.scope for candidate in self.candidates):
            raise ValueError("candidate_scope_mismatch")
        return self


class ModelManifest(Contract):
    """Explicit request alias -> single pinned deployment, never inferred from alias."""

    request_model: SafeText
    deployment_model: SafeText
    manifest_digest: Digest

    @model_validator(mode="after")
    def pinned(self) -> Self:
        if "latest" in self.deployment_model.casefold():
            raise ValueError("deployment_must_be_pinned")
        return self


class DecisionBudget(Contract):
    max_request_bytes: Annotated[int, Field(ge=1, le=1_048_576)] = 65_536
    max_response_bytes: Annotated[int, Field(ge=1, le=1_048_576)] = 65_536
    max_candidates: Annotated[int, Field(ge=1, le=255)] = 128
    minimum_confidence: Probability = 0.9


class DecisionSource(Contract):
    """Trusted observer source identity, matched to a configured deployment registry."""

    source_id: OpaqueId
    origin: str = Field(repr=False, min_length=1, max_length=2048)
    fixture_digest: Digest


@dataclass(frozen=True, slots=True)
class DecisionCallContext:
    """Process-local context; intentionally not a serializable Pydantic model."""

    deadline_monotonic: float
    manifest: ModelManifest
    source: DecisionSource | None = field(default=None, repr=False)
    current_targets: Callable[[], tuple[TargetRef, ...]] | None = field(default=None, repr=False)
    budget: DecisionBudget = field(default_factory=DecisionBudget)
    cancellation: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


class DecisionResult(Contract):
    request_id: OpaqueId
    scope: ScopeStamp
    status: DecisionStatus | None = None
    error: DecisionError | None = None
    selected: TargetRef | None = None
    # Fixed diagnostic codes only. Never store response bodies or exception text.
    reason: (
        Literal[
            "unauthorized",
            "rate_limited",
            "capacity",
            "bad_input",
            "transport",
            "deadline",
            "cancelled",
            "malformed",
            "model",
            "budget",
            "confidence",
            "tie",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def exclusive(self) -> Self:
        if (self.status is None) == (self.error is None):
            raise ValueError("decision_requires_status_or_error")
        if (self.status == "selected") != (self.selected is not None):
            raise ValueError("decision_selection_inconsistent")
        if self.selected is not None and self.selected.scope != self.scope:
            raise ValueError("decision_scope_mismatch")
        return self

    def validate_for(self, request: DecisionRequest, current: ScopeStamp) -> None:
        if self.request_id != request.request_id or self.scope != request.scope:
            raise ValueError("decision_request_mismatch")
        if self.scope != current:
            raise ValueError("browser_target_stale")
        if self.selected is not None and self.selected not in tuple(
            candidate.ref for candidate in request.candidates
        ):
            raise ValueError("decision_unknown_target")


class ParameterRef(Contract):
    """Resolved by the executor from approved inputs; never generated by Decision."""

    name: OpaqueId


class LocatorHint(Contract):
    kind: Literal["role_name", "business_key", "test_id"]
    value: SafeText


class SkillStep(Contract):
    step_id: OpaqueId
    operation: Literal["click", "fill", "select_option", "read", "navigate"]
    locator: LocatorHint
    value_ref: ParameterRef | None = None
    url_ref: ParameterRef | None = None
    option_ref: ParameterRef | None = None
    effect: Literal["read_only", "may_write"]

    @model_validator(mode="after")
    def fixed_arguments(self) -> Self:
        if (self.operation == "fill") != (self.value_ref is not None):
            raise ValueError("skill_value_reference_mismatch")
        if (self.operation == "navigate") != (self.url_ref is not None):
            raise ValueError("skill_url_reference_mismatch")
        if (self.operation == "select_option") != (self.option_ref is not None):
            raise ValueError("skill_option_reference_mismatch")
        return self


class BrowserSkill(Contract):
    schema_version: Literal["browser_skill.v1"] = "browser_skill.v1"
    skill_id: OpaqueId
    version: OpaqueId
    digest: Digest
    site_id: OpaqueId
    site_digest: Digest
    verifier_id: OpaqueId
    verifier_digest: Digest
    parameters: tuple[OpaqueId, ...] = ()
    steps: Annotated[tuple[SkillStep, ...], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def bound_parameters(self) -> Self:
        if len(set(self.parameters)) != len(self.parameters):
            raise ValueError("duplicate_parameter")
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("duplicate_step")
        if any(
            ref.name not in self.parameters
            for step in self.steps
            for ref in (step.value_ref, step.url_ref, step.option_ref)
            if ref is not None
        ):
            raise ValueError("unbound_parameter")
        return self


class ActionCommand(Contract):
    """Skill-owned operation and references; contains no free-form script or value."""

    skill_digest: Digest
    step: SkillStep
    binding: ScopeBinding = Field(repr=False)
    target: TargetRef | None = None
    option: TargetRef | None = None

    @model_validator(mode="after")
    def shape(self) -> Self:
        if (self.step.operation != "navigate") != (self.target is not None):
            raise ValueError("action_target_mismatch")
        if (self.step.operation == "select_option") != (self.option is not None):
            raise ValueError("action_option_mismatch")
        if self.option and self.target and self.option.scope != self.target.scope:
            raise ValueError("action_option_scope_mismatch")
        return self


class ConfirmedBusinessKey(Contract):
    confirmation_ref: OpaqueId
    object_type: OpaqueId
    key_digest: Digest
    value_ref: ParameterRef


class ReadSpec(Contract):
    """Independent lookup by confirmed key and current owner, never selected row ID."""

    binding: ScopeBinding = Field(repr=False)
    business_key: ConfirmedBusinessKey
    verifier_id: OpaqueId
    verifier_digest: Digest
    fields: Annotated[tuple[OpaqueId, ...], Field(min_length=1, max_length=32)]
    mode: Literal["independent_confirmed_key_v1", "independent_query_detail_v1"] = (
        "independent_confirmed_key_v1"
    )

    @model_validator(mode="after")
    def unique_fields(self) -> Self:
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("duplicate_read_field")
        return self


class ReadFieldEvidence(Contract):
    """Legacy input comparison or query field presence; never raw values."""

    field_id: OpaqueId
    status: Literal["matched", "present", "mismatch", "missing", "unsupported"]


class ReadEvidence(Contract):
    """Independent key lookup evidence. Echoing a requested key is not a match.

    key_match/owner_match and comparisons come from actual independently read
    records, using the current authorized sealed key. Legacy expected values come
    from approved inputs; query fields require actual presence and frozen schema
    validation. Test oracle or selected DOM row cannot supply confirmation.
    Digests contain no raw sensitive values.
    """

    binding: ScopeBinding = Field(repr=False)
    business_key: ConfirmedBusinessKey
    match_count: Annotated[int, Field(ge=0)]
    key_match: bool
    owner_match: bool
    coverage: Coverage
    fields: Annotated[tuple[ReadFieldEvidence, ...], Field(min_length=1, max_length=32)]
    evidence_digest: Digest | None = None
    mode: Literal["independent_confirmed_key_v1", "independent_query_detail_v1"] = (
        "independent_confirmed_key_v1"
    )
    object_type_match: bool | None = None
    schema_validated: bool | None = None

    @model_validator(mode="after")
    def consistent_read(self) -> Self:
        ids = [item.field_id for item in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_read_evidence_field")
        if self.match_count == 0 and (self.key_match or self.owner_match):
            raise ValueError("empty_read_cannot_match")
        if self.coverage.trusted_empty and self.match_count != 0:
            raise ValueError("read_empty_evidence_conflict")
        if self.coverage.state == "unsupported" and self.match_count != 0:
            raise ValueError("unsupported_read_has_records")
        return self

    def validate_for(self, spec: ReadSpec, current_binding: ScopeBinding) -> None:
        if self.mode != spec.mode:
            raise ValueError("browser_read_mode_mismatch")
        if self.mode == "independent_confirmed_key_v1" and (
            self.object_type_match is not None or self.schema_validated is not None
            or any(item.status == "present" for item in self.fields)
        ):
            raise ValueError("browser_read_mode_mismatch")
        if self.mode == "independent_query_detail_v1" and (
            self.object_type_match is None or self.schema_validated is None
            or any(item.status == "matched" for item in self.fields)
        ):
            raise ValueError("browser_read_mode_mismatch")
        if self.binding != spec.binding or self.binding != current_binding:
            raise ValueError("browser_binding_stale")
        if self.business_key != spec.business_key:
            raise ValueError("browser_read_key_mismatch")
        if {item.field_id for item in self.fields} != set(spec.fields):
            raise ValueError("browser_read_fields_mismatch")


class VerificationResult(Contract):
    status: Literal["verified", "mismatch", "incomplete", "unsupported"]
    evidence_digest: Digest | None = None

    @model_validator(mode="after")
    def verified_requires_evidence(self) -> Self:
        if self.status == "verified" and self.evidence_digest is None:
            raise ValueError("verification_evidence_required")
        return self


class BrowserSessionRef(Contract):
    session_ref: OpaqueId
    binding: ScopeBinding = Field(repr=False)


class SessionStartResult(Contract):
    status: Literal["fresh", "subject_verified"]
    subject_digest: Digest | None = None
    evidence_digest: Digest | None = None

    @model_validator(mode="after")
    def verified_subject(self) -> Self:
        if self.status == "subject_verified" and (
            self.subject_digest is None or self.evidence_digest is None
        ):
            raise ValueError("browser_subject_evidence_required")
        if self.status == "fresh" and self.subject_digest is not None:
            raise ValueError("fresh_session_has_no_verified_subject")
        return self


class BrowserFailure(Contract):
    code: Literal[
        "unavailable",
        "overloaded",
        "invalid_request",
        "resource_not_found",
        "unsupported",
        "denied",
        "stale",
        "subject_mismatch",
        "invalid_response",
        "timeout",
        "cancelled",
        "effect_unknown",
        "quarantined",
    ]
    phase: Literal["acquire", "restore", "observe", "dispatch", "capture", "release", "terminate"]
    dispatch_state: Literal["not_sent", "possibly_sent"]
    cleanup_required: bool


class BrowserOperationError(Exception):
    """Neutral failure transport. Never attach a vendor body, URL or raw exception."""

    def __init__(self, failure: BrowserFailure) -> None:
        self.failure = failure
        super().__init__(failure.code)


class DispatchReceipt(Contract):
    """An action acknowledgment is distinct from independently verified success."""

    skill_digest: Digest
    step_id: OpaqueId
    state: Literal["not_sent", "possibly_sent", "acknowledged"]
    failure: BrowserFailure | None = None
    evidence_digest: Digest | None = None

    @model_validator(mode="after")
    def dispatch_consistency(self) -> Self:
        if self.state == "acknowledged":
            if self.failure is not None or self.evidence_digest is None:
                raise ValueError("acknowledged_requires_evidence_without_failure")
        elif self.failure is None or self.failure.dispatch_state != self.state:
            raise ValueError("dispatch_failure_state_mismatch")
        elif self.state == "possibly_sent" and self.failure.code != "effect_unknown":
            raise ValueError("possibly_sent_requires_unknown_effect")
        return self

    def validate_for(self, command: ActionCommand) -> None:
        if (self.skill_digest, self.step_id) != (command.skill_digest, command.step.step_id):
            raise ValueError("dispatch_command_mismatch")


class BrowserCapabilities(Contract):
    transport: Literal["playwright", "cdp"]
    cookies: bool
    local_storage: bool
    indexed_db: bool
    immutable_capture: bool
    confirmed_termination: bool


class ProfileRef(Contract):
    generation_ref: OpaqueId
    binding: ScopeBinding = Field(repr=False)
    profile_revision: Epoch
    subject_digest: Digest


class ResourceOutcome(Contract):
    status: Literal["released", "terminated", "quarantined"]
    evidence_digest: Digest


ParameterPurpose = Literal[
    "fill_value",
    "option_values",
    "navigation_url",
    "business_key",
    "expected_field",
]
_Consumed = TypeVar("_Consumed")


class _ProcessLocal:
    """Never serialize execution capabilities, callbacks or protected values."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("browser_context_serialization_forbidden")

    def __getstate__(self) -> object:
        raise TypeError("browser_context_serialization_forbidden")


class SealedParameter(_ProcessLocal):
    """Purpose-bound input, consumed only at the trusted adapter/reader boundary.

    The resolver is injected by trusted composition, not parsed from public input.
    No raw property, dict, JSON or printable value exists. Consumer code must not
    retain or log the value. An option parameter is a finite approved value set;
    it is never a value invented or extended by a model response.
    """

    __slots__ = ("__value", "__ref", "__purpose", "__binding", "__skill_digest", "__step_id")

    def __init__(
        self,
        value: str | tuple[str, ...],
        *,
        ref: ParameterRef,
        purpose: ParameterPurpose,
        binding: ScopeBinding,
        skill_digest: Digest,
        step_id: OpaqueId,
    ) -> None:
        TypeAdapter(ParameterPurpose).validate_python(purpose, strict=True)
        TypeAdapter(Digest).validate_python(skill_digest, strict=True)
        TypeAdapter(OpaqueId).validate_python(step_id, strict=True)
        if not isinstance(ref, ParameterRef) or not isinstance(binding, ScopeBinding):
            raise ValueError("sealed_parameter_binding_invalid")
        if purpose == "option_values":
            if (
                type(value) is not tuple
                or not 1 <= len(value) <= 255
                or any(type(item) is not str for item in value)
                or len(set(value)) != len(value)
            ):
                raise ValueError("sealed_option_values_invalid")
        elif type(value) is not str:
            raise ValueError("sealed_parameter_type_invalid")
        self.__value = value
        self.__ref = ref
        self.__purpose = purpose
        self.__binding = binding
        self.__skill_digest = skill_digest
        self.__step_id = step_id

    def consume(
        self,
        consumer: Callable[[str | tuple[str, ...]], _Consumed],
        *,
        ref: ParameterRef,
        purpose: ParameterPurpose,
        binding: ScopeBinding,
        skill_digest: Digest,
        step_id: OpaqueId,
    ) -> _Consumed:
        if (ref, purpose, binding, skill_digest, step_id) != (
            self.__ref,
            self.__purpose,
            self.__binding,
            self.__skill_digest,
            self.__step_id,
        ):
            raise ValueError("sealed_parameter_authority_mismatch")
        return consumer(self.__value)


class DispatchPermit(_ProcessLocal):
    """Single-use in-process dispatch barrier proof, not a durable send record.

    The trusted guard owns the lock, final current-auth/cancellation check, and
    exact command binding. WebAdapter.execute must revalidate live refs inside
    that guard, then call begin_send immediately before its actual send. No
    awaited work may intervene. M1 adds durable fencing before real writes.
    """

    __slots__ = ("__execution_id", "__command", "__begin_send", "__started")
    durability: Literal["memory_only"] = "memory_only"

    def __init__(
        self,
        execution_id: OpaqueId,
        command: ActionCommand,
        begin_send: Callable[[], None],
    ) -> None:
        TypeAdapter(OpaqueId).validate_python(execution_id, strict=True)
        if not isinstance(command, ActionCommand) or not callable(begin_send):
            raise ValueError("dispatch_permit_invalid")
        self.__execution_id = execution_id
        self.__command = command
        self.__begin_send = begin_send
        self.__started = False

    @property
    def execution_id(self) -> str:
        return self.__execution_id

    @property
    def command(self) -> ActionCommand:
        return self.__command

    @property
    def send_started(self) -> bool:
        return self.__started

    def begin_send(self) -> None:
        if self.__started:
            raise ValueError("browser_dispatch_already_started")
        self.__begin_send()
        self.__started = True


@dataclass(frozen=True, slots=True, repr=False)
class ExecutionContext(_ProcessLocal):
    """Trusted composition capability, never public request input or model wire.

    Every adapter entry independently checks its registered session/execution,
    frozen Skill and current authorization; calling execute directly cannot
    bypass them. Callbacks must use authoritative current state, not caller
    assertions or stale ContextVars. Parameter resolution is purpose/step-bound.
    M0 permits only fact-proven read-only effects; may_write remains denied.
    """

    # frozen+slots dataclasses otherwise synthesize a getstate returning every
    # field, overriding the inherited process-local serialization prohibition.
    __getstate__ = _ProcessLocal.__getstate__

    execution_id: OpaqueId
    skill: BrowserSkill
    expected_binding: ScopeBinding
    source: DecisionSource
    deadline_monotonic: float
    cancellation: asyncio.Event
    # Exact registered canonical origins, including each allowed redirect hop.
    # Empty means navigation is denied. Never populated from model/user URLs.
    navigation_origins: tuple[str, ...]
    current_binding: Callable[[BrowserSessionRef], Awaitable[ScopeBinding]]
    authorize: Callable[
        [BrowserSessionRef, BrowserSkill, ActionCommand | ReadSpec, ScopeBinding], Awaitable[None]
    ]
    resolve_parameter: Callable[
        [ParameterRef, ParameterPurpose, ScopeBinding, Digest, OpaqueId], Awaitable[SealedParameter]
    ]
    dispatch_barrier: Callable[
        [BrowserSessionRef, ActionCommand, ScopeBinding],
        AbstractAsyncContextManager[DispatchPermit],
    ]

    def __post_init__(self) -> None:
        TypeAdapter(OpaqueId).validate_python(self.execution_id, strict=True)
        if (
            not isinstance(self.skill, BrowserSkill)
            or not isinstance(self.expected_binding, ScopeBinding)
            or not isinstance(self.source, DecisionSource)
            or not isinstance(self.cancellation, asyncio.Event)
        ):
            raise ValueError("execution_context_invalid")
        if type(self.deadline_monotonic) not in (int, float) or not math.isfinite(
            self.deadline_monotonic
        ):
            raise ValueError("execution_deadline_invalid")
        if type(self.navigation_origins) is not tuple or len(set(self.navigation_origins)) != len(
            self.navigation_origins
        ):
            raise ValueError("execution_navigation_origins_invalid")
        for origin in self.navigation_origins:
            if (
                type(origin) is not str
                or not origin.isascii()
                or any(char.isspace() or char in "\\%*" for char in origin)
            ):
                raise ValueError("execution_navigation_origins_invalid")
            try:
                parsed = urlsplit(origin)
                port = parsed.port
            except ValueError:
                raise ValueError("execution_navigation_origins_invalid") from None
            host = parsed.hostname or ""
            host_text = f"[{host}]" if ":" in host else host
            suffix = "" if port is None else f":{port}"
            if (
                parsed.scheme not in {"http", "https"}
                or not host
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
                or origin != f"{parsed.scheme}://{host_text}{suffix}"
                or port == (443 if parsed.scheme == "https" else 80)
            ):
                raise ValueError("execution_navigation_origins_invalid")
        if not all(
            callable(callback)
            for callback in (
                self.current_binding,
                self.authorize,
                self.resolve_parameter,
                self.dispatch_barrier,
            )
        ):
            raise ValueError("execution_callbacks_required")
