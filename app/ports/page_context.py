"""Fail-closed page-context registration and authorization contract."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from typing import Annotated, Literal, NamedTuple, Protocol, TypeAlias
from weakref import WeakKeyDictionary

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    model_validator,
)

from app.ports.llm_provider import LLMMessage
from app.ports.trace import REDACTED_TRACE_VALUE, redact_trace_attributes

PAGE_CONTEXT_FIELD_NAMES = (
    "surface_id",
    "organization_scope",
    "work_object_refs",
    "source_refs",
    "filters",
    "selected_metric",
    "allowed_capabilities",
    "freshness",
    "visibility",
)
PAGE_CONTEXT_MAX_REFERENCES = 200
PAGE_CONTEXT_MAX_FILTERS = 32
PAGE_CONTEXT_MAX_CAPABILITIES = 64
PAGE_CONTEXT_TIMESTAMP_MAX_LENGTH = 64
PAGE_CONTEXT_TIMESTAMP_PATTERN = (
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?Z$"
)
PAGE_CONTEXT_DATA_MESSAGE_PREFIX = "UNTRUSTED_PAGE_CONTEXT_DATA\n"

_SURFACE_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$", re.ASCII)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9]+(?:[._:@/-][A-Za-z0-9]+)*$", re.ASCII)
_CAPABILITY_ID_RE = re.compile(r"^[a-z0-9]+(?:[._:-][a-z0-9]+)*$", re.ASCII)
_TIMESTAMP_RE = re.compile(PAGE_CONTEXT_TIMESTAMP_PATTERN, re.ASCII)
_DOM_MARKUP_RE = re.compile(
    r"<!doctype\s+html|<\s*/?\s*(?:html|body|form|input|script|table|div|main)\b",
    re.IGNORECASE,
)


class _PageContextModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )


def _reject_credential_or_dom_text(value: str) -> str:
    if not value.isprintable():
        raise ValueError("page context text must contain only printable characters")
    redacted = redact_trace_attributes({"value": value})["value"]
    if redacted == REDACTED_TRACE_VALUE:
        raise ValueError("credential-like material is forbidden in page context")
    if _DOM_MARKUP_RE.search(value):
        raise ValueError("DOM markup is forbidden in page context")
    return value


def _validate_surface_id(value: str) -> str:
    if not _SURFACE_ID_RE.fullmatch(value):
        raise ValueError("surface_id must use lowercase slug form")
    return _reject_credential_or_dom_text(value)


def _validate_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError("identifier contains unsupported characters")
    return _reject_credential_or_dom_text(value)


def _validate_capability_id(value: str) -> str:
    if not _CAPABILITY_ID_RE.fullmatch(value):
        raise ValueError("capability id must use lowercase dotted slug form")
    return _reject_credential_or_dom_text(value)


def _validate_timestamp(value: str) -> str:
    _reject_credential_or_dom_text(value)
    if not _TIMESTAMP_RE.fullmatch(value):
        raise ValueError("freshness observed_at must be a UTC RFC3339 timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("freshness observed_at must be a valid timestamp") from exc
    return value


PageSurfaceId: TypeAlias = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=80),
    AfterValidator(_validate_surface_id),
]
PageContextIdentifier: TypeAlias = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=256),
    AfterValidator(_validate_identifier),
]
PageContextText: TypeAlias = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=500),
    AfterValidator(_reject_credential_or_dom_text),
]
PageContextReferenceText: TypeAlias = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=256),
    AfterValidator(_reject_credential_or_dom_text),
]
PageContextTimestamp: TypeAlias = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=PAGE_CONTEXT_TIMESTAMP_MAX_LENGTH),
    AfterValidator(_validate_timestamp),
]
PageCapabilityId: TypeAlias = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=120),
    AfterValidator(_validate_capability_id),
]
PageVisibility: TypeAlias = Literal["principal", "department", "organization"]
PageFilterOperator: TypeAlias = Literal[
    "equals",
    "not_equals",
    "contains",
    "greater_than",
    "less_than",
]
PageFilterValue: TypeAlias = (
    PageContextText | StrictInt | StrictFloat | StrictBool
)
PageFreshnessState: TypeAlias = Literal["unknown", "reported", "stale"]


class OrganizationScope(_PageContextModel):
    tenant_id: PageContextIdentifier
    organization_id: PageContextIdentifier | None
    department_id: PageContextIdentifier | None

    @model_validator(mode="after")
    def validate_department_parent(self) -> OrganizationScope:
        if self.department_id is not None and self.organization_id is None:
            raise ValueError("department_id requires organization_id")
        return self


class WorkObjectReference(_PageContextModel):
    work_object_id: PageContextIdentifier


class SourceReference(_PageContextModel):
    source_system: PageContextIdentifier
    source_ref: PageContextReferenceText


class PageFilter(_PageContextModel):
    """Untrusted page declaration; authority proves the exact control/value pair."""

    field: PageContextIdentifier
    operator: PageFilterOperator
    value: PageFilterValue
    source: Literal["visible_control"]

    @model_validator(mode="after")
    def validate_value(self) -> PageFilter:
        if isinstance(self.value, str):
            _reject_credential_or_dom_text(self.value)
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("filter number must be finite")
        return self


class PageFreshness(_PageContextModel):
    state: PageFreshnessState
    observed_at: PageContextTimestamp | None

    @model_validator(mode="after")
    def validate_observation(self) -> PageFreshness:
        if self.state != "unknown" and self.observed_at is None:
            raise ValueError("reported or stale freshness requires observed_at")
        return self


class _PageContextData(_PageContextModel):
    surface_id: PageSurfaceId
    organization_scope: OrganizationScope | None
    work_object_refs: Annotated[
        tuple[WorkObjectReference, ...],
        Field(max_length=PAGE_CONTEXT_MAX_REFERENCES),
    ]
    source_refs: Annotated[
        tuple[SourceReference, ...],
        Field(max_length=PAGE_CONTEXT_MAX_REFERENCES),
    ]
    filters: Annotated[
        tuple[PageFilter, ...],
        Field(max_length=PAGE_CONTEXT_MAX_FILTERS),
    ]
    selected_metric: PageContextIdentifier | None
    allowed_capabilities: Annotated[
        tuple[PageCapabilityId, ...],
        Field(max_length=PAGE_CONTEXT_MAX_CAPABILITIES),
    ]
    freshness: PageFreshness
    visibility: PageVisibility

    @model_validator(mode="after")
    def validate_unique_collections(self) -> _PageContextData:
        work_object_ids = [item.work_object_id for item in self.work_object_refs]
        source_keys = [
            (item.source_system, item.source_ref) for item in self.source_refs
        ]
        if len(work_object_ids) != len(set(work_object_ids)):
            raise ValueError("work_object_refs must not contain duplicates")
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("source_refs must not contain duplicates")
        if len(self.allowed_capabilities) != len(set(self.allowed_capabilities)):
            raise ValueError("allowed_capabilities must not contain duplicates")
        if self.visibility == "department" and (
            self.organization_scope is None
            or self.organization_scope.department_id is None
        ):
            raise ValueError("department visibility requires a department scope")
        if self.visibility == "organization" and (
            self.organization_scope is None
            or self.organization_scope.organization_id is None
        ):
            raise ValueError("organization visibility requires an organization scope")
        return self


class PageContextDeclaration(_PageContextData):
    """The only page-supplied shape; values remain untrusted declarations."""


class AuthorizedPageContext(_PageContextData):
    """Validated nine-field data; only a resolution ticket proves authorization."""


class PageContextAuthority(_PageContextModel):
    """Trusted facts built from Principal, Policy and Registry; never page input."""

    principal_id: PageContextIdentifier
    organization_scopes: frozenset[OrganizationScope]
    visibilities: frozenset[PageVisibility]
    registry_capabilities: frozenset[PageCapabilityId]
    policy_capabilities: frozenset[PageCapabilityId]
    visible_work_object_refs: frozenset[WorkObjectReference]
    visible_source_refs: frozenset[SourceReference]
    visible_filters: frozenset[PageFilter]
    visible_selected_metrics: frozenset[PageContextIdentifier]


class PageContextModelData(_PageContextModel):
    """Structured model input segment that can never become a system instruction."""

    role: Literal["user_data"] = "user_data"
    trust: Literal["untrusted_external"] = "untrusted_external"
    data: AuthorizedPageContext


class PageContextAuthorizationError(RuntimeError):
    """Explicit fail-closed authorization failure without rejected values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _PageContextResolutionPayload(NamedTuple):
    principal_id: str
    authorized_context_json: str
    rejected_capabilities: tuple[str, ...]


class PageContextResolution:
    """Opaque authorization ticket issued only by ``authorize_page_context``."""

    __slots__ = ("_payload", "__weakref__")
    _payload: _PageContextResolutionPayload

    def __new__(cls, *args: object, **kwargs: object) -> PageContextResolution:
        del args, kwargs
        raise TypeError("PageContextResolution can only be issued by authorize_page_context")

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise TypeError("PageContextResolution is immutable")

    @property
    def principal_id(self) -> str:
        return self._payload.principal_id

    @property
    def authorized_context(self) -> AuthorizedPageContext:
        return AuthorizedPageContext.model_validate_json(
            self._payload.authorized_context_json
        )

    @property
    def rejected_capabilities(self) -> tuple[str, ...]:
        return self._payload.rejected_capabilities


class _AuthorizePageContext(Protocol):
    def __call__(
        self,
        declaration: PageContextDeclaration,
        authority: PageContextAuthority,
    ) -> PageContextResolution: ...


class _AsUntrustedModelData(Protocol):
    def __call__(
        self,
        resolution: PageContextResolution,
    ) -> PageContextModelData: ...


class _BuildPageContextMessages(Protocol):
    def __call__(
        self,
        *,
        system_instruction: str,
        resolution: PageContextResolution,
    ) -> tuple[LLMMessage, LLMMessage]: ...


def _build_page_context_authorization_contract() -> tuple[
    _AuthorizePageContext,
    _AsUntrustedModelData,
    _BuildPageContextMessages,
]:
    issued_resolutions: WeakKeyDictionary[
        PageContextResolution,
        _PageContextResolutionPayload,
    ] = WeakKeyDictionary()

    def require_issued_resolution(
        candidate: object,
    ) -> _PageContextResolutionPayload:
        if not isinstance(candidate, PageContextResolution):
            raise PageContextAuthorizationError("authorization_provenance_invalid")
        try:
            return issued_resolutions[candidate]
        except KeyError as exc:
            raise PageContextAuthorizationError(
                "authorization_provenance_invalid"
            ) from exc

    def authorize_page_context(
        declaration: PageContextDeclaration,
        authority: PageContextAuthority,
    ) -> PageContextResolution:
        """Authorize declarations and issue an identity-bound provenance ticket."""

        if (
            declaration.organization_scope is not None
            and declaration.organization_scope not in authority.organization_scopes
        ):
            raise PageContextAuthorizationError("organization_scope_not_authorized")
        if declaration.visibility not in authority.visibilities:
            raise PageContextAuthorizationError("visibility_not_authorized")
        if not set(declaration.work_object_refs).issubset(
            authority.visible_work_object_refs
        ):
            raise PageContextAuthorizationError("work_object_refs_not_visible")
        if not set(declaration.source_refs).issubset(authority.visible_source_refs):
            raise PageContextAuthorizationError("source_refs_not_visible")
        if not set(declaration.filters).issubset(authority.visible_filters):
            raise PageContextAuthorizationError("filters_not_visible")
        if (
            declaration.selected_metric is not None
            and declaration.selected_metric not in authority.visible_selected_metrics
        ):
            raise PageContextAuthorizationError("selected_metric_not_visible")

        backend_capabilities = (
            authority.registry_capabilities & authority.policy_capabilities
        )
        effective_capabilities = tuple(
            capability_id
            for capability_id in declaration.allowed_capabilities
            if capability_id in backend_capabilities
        )
        rejected_capabilities = tuple(
            capability_id
            for capability_id in declaration.allowed_capabilities
            if capability_id not in backend_capabilities
        )
        authorized_context = AuthorizedPageContext.model_validate(
            {
                **declaration.model_dump(mode="python"),
                "allowed_capabilities": effective_capabilities,
            }
        )
        payload = _PageContextResolutionPayload(
            principal_id=authority.principal_id,
            authorized_context_json=authorized_context.model_dump_json(),
            rejected_capabilities=rejected_capabilities,
        )
        resolution = object.__new__(PageContextResolution)
        object.__setattr__(resolution, "_payload", payload)
        issued_resolutions[resolution] = payload
        return resolution

    def as_untrusted_model_data(
        resolution: PageContextResolution,
    ) -> PageContextModelData:
        """Project only data from a factory-issued authorization ticket."""

        payload = require_issued_resolution(resolution)
        context = AuthorizedPageContext.model_validate_json(
            payload.authorized_context_json
        )
        return PageContextModelData(data=context)

    def build_page_context_messages(
        *,
        system_instruction: str,
        resolution: PageContextResolution,
    ) -> tuple[LLMMessage, LLMMessage]:
        """Assemble messages only from a factory-issued authorization ticket."""

        model_data = as_untrusted_model_data(resolution)
        serialized_data = json.dumps(
            model_data.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            LLMMessage(role="system", content=system_instruction),
            LLMMessage(
                role="user",
                content=f"{PAGE_CONTEXT_DATA_MESSAGE_PREFIX}{serialized_data}",
            ),
        )

    return (
        authorize_page_context,
        as_untrusted_model_data,
        build_page_context_messages,
    )


(
    authorize_page_context,
    as_untrusted_model_data,
    build_page_context_messages,
) = _build_page_context_authorization_contract()


class PageContextPort(Protocol):
    """Register/read context bound to principal_id; reads must reauthorize."""

    async def register(
        self,
        *,
        context: PageContextDeclaration,
        authority: PageContextAuthority,
    ) -> PageContextResolution: ...

    async def read(
        self,
        *,
        surface_id: PageSurfaceId,
        authority: PageContextAuthority,
    ) -> PageContextResolution | None: ...


__all__ = (
    "AuthorizedPageContext",
    "OrganizationScope",
    "PAGE_CONTEXT_FIELD_NAMES",
    "PAGE_CONTEXT_DATA_MESSAGE_PREFIX",
    "PAGE_CONTEXT_TIMESTAMP_MAX_LENGTH",
    "PAGE_CONTEXT_TIMESTAMP_PATTERN",
    "PageContextAuthorizationError",
    "PageContextAuthority",
    "PageContextDeclaration",
    "PageContextModelData",
    "PageContextPort",
    "PageContextResolution",
    "PageFilter",
    "PageFreshness",
    "SourceReference",
    "WorkObjectReference",
    "as_untrusted_model_data",
    "authorize_page_context",
    "build_page_context_messages",
)
