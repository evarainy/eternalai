"""One explicitly approved account and business_context_get profile.

Successful fields follow the approved supplier guide, SHA256
735c67a0b1090f98f8f294b7fb979a361ee5b0ae769d090d9231b4e8eca181ea.
Identity binding requires either an approved expected subject or an explicit
OAuth-owner policy; neither mode infers approval evidence.
"""

from __future__ import annotations

import json
import ssl
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.infra.adapters.business_mcp.catalog import catalog
from app.infra.mcp.driver import McpDriver
from app.infra.sdui.response_projection import schema_has_credential_property
from app.mcp.contracts import (
    OutputContract,
    input_digest,
    project_public_result_schema,
    safety_digest,
)
from app.mcp.diagnostics import record_mcp_event
from app.mcp.models import McpFailure, ServiceConfig, ToolBinding, digest
from app.ports.auth import (
    AuthenticatedSessionContext,
    SessionRevocationStorePort,
    authenticated_session,
)
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.ports.mcp_store import AuthorizationTransaction, IdentityAssertionPort, McpStorePort
from app.ports.request_context import RequestOrgContext

TOOL = "business_context_get"
RESOURCE = "https://34.64.8.101/mcp"
SERVICE_CONFIG_ID = "lesscode-business-context-read"


def fixed_read_display_fields() -> dict[str, str]:
    return {"timezone": "业务时区", "capabilities": "平台可用工具（仅展示）"}


def business_context_success_schema() -> dict[str, Any]:
    """Actual supplier types; additional private fields never become public."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "userId": {"type": "string", "pattern": "^[0-9]+$"},
            "displayName": {"type": "string"},
            "clientId": {"type": "string", "minLength": 1},
            "adapterClientId": {"type": "string", "const": "controlled-agent"},
            "resource": {"type": "string", "const": RESOURCE},
            "timezone": {"type": "string", "const": "Asia/Shanghai"},
            "capabilities": {
                "type": "array",
                "items": {"type": "string"},
                "contains": {"const": TOOL},
            },
            "organization": {
                "type": "object",
                "properties": {
                    "departmentId": {"type": ["number", "null"]},
                    "departmentName": {"type": ["string", "null"]},
                    "departmentType": {"type": ["string", "null"]},
                    "allOrganizations": {"type": "boolean"},
                    "jyId": {"type": ["string", "null"]},
                    "jqPrefix": {"type": ["string", "null"]},
                },
                "required": [
                    "departmentId", "departmentName", "departmentType",
                    "allOrganizations", "jyId", "jqPrefix",
                ],
            },
        },
        "required": [
            "userId", "displayName", "clientId", "adapterClientId",
            "resource", "timezone", "capabilities", "organization",
        ],
    }


class FixedReadApproval(BaseModel):
    """Non-secret, owner-confirmed contract; never built from a model response."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    service_config_id: str
    service_config_version: int = Field(ge=1)
    tenant_id: str
    user_id: str | None = Field(default=None, min_length=1, repr=False)
    local_owner_source: Literal["fixed", "authenticated_session"] = "fixed"
    version: str = Field(min_length=1, max_length=100)
    approval_evidence: str = Field(min_length=1, repr=False)
    issuer_parameter_supported: bool
    subject_path: list[str] = Field(default_factory=lambda: ["userId"], repr=False)
    identity_binding_mode: Literal["expected_subject", "oauth_owner"] = "expected_subject"
    expected_subject: str | None = Field(default=None, pattern="^[0-9]+$", repr=False)
    success_schema: dict[str, Any] = Field(
        default_factory=business_context_success_schema, repr=False
    )
    # Labels and field names are approved configuration, not upstream captions.
    display_fields: dict[str, str] = Field(
        default_factory=fixed_read_display_fields
    )

    @model_validator(mode="after")
    def binding_mode_has_one_authority(self) -> FixedReadApproval:
        if (self.identity_binding_mode == "expected_subject") != (
            self.expected_subject is not None
        ) or (self.local_owner_source == "fixed") != (self.user_id is not None):
            raise ValueError("mcp_fixed_profile_invalid")
        if self.local_owner_source == "authenticated_session" and (
            self.identity_binding_mode != "oauth_owner"
            or self.service_config_id != SERVICE_CONFIG_ID
            or self.tenant_id != "default"
        ):
            raise ValueError("mcp_fixed_profile_invalid")
        return self


@dataclass(frozen=True)
class PreparedFixedReadDefinition:
    """Code-owned disabled metadata; carries no identity or approval evidence."""

    service: ServiceConfig
    success_schema: dict[str, Any]
    display_fields: dict[str, str]
    capability: CapabilitySpec
    binding: ToolBinding


@dataclass(frozen=True, repr=False)
class FixedReadLocalOwner:
    """Local identity obtained only from the verified HTTP session context."""

    tenant_id: str
    user_id: str
    fingerprint: bytes = dataclass_field(repr=False)
    expires_at: datetime

    @classmethod
    def from_session(cls, session: AuthenticatedSessionContext) -> FixedReadLocalOwner:
        if session.expires_at <= datetime.now(UTC) or len(session.fingerprint) != 32:
            raise McpFailure("mcp_service_unavailable")
        return cls(
            tenant_id=session.principal.org_ctx.tenant_id,
            user_id=session.principal.ai_user_id,
            fingerprint=session.fingerprint,
            expires_at=session.expires_at,
        )


def prepare_fixed_read_definition(service: ServiceConfig) -> PreparedFixedReadDefinition:
    if (
        service.protocol_version != "2025-11-25"
        or service.endpoint != RESOURCE
        or service.resource != RESOURCE
    ):
        raise McpFailure("mcp_fixed_profile_invalid")
    schema = business_context_success_schema()
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise McpFailure("mcp_output_contract_invalid") from None
    display_fields = fixed_read_display_fields()
    public_schema = project_public_result_schema(schema, set(display_fields))
    if schema_has_credential_property(public_schema):
        raise McpFailure("mcp_output_contract_invalid")
    capabilities, bindings = catalog(service, {})
    capability_id = f"business.{service.service_config_id}.{TOOL}"
    capability = next(c for c in capabilities if c.capability_id == capability_id)
    binding = next(b for b in bindings if b.capability_id == capability_id)
    capability = CapabilitySpec.model_validate(
        {
            **capability.model_dump(),
            "name": "查询当前业务信息",
            "short_description": "读取本人已授权业务平台的当前业务信息",
            "intent_tags": ["business_context", "current_business", "business_context_get"],
            "output_schema": public_schema,
            "output_schema_digest": digest(public_schema),
            "status": "disabled",
        }
    )
    return PreparedFixedReadDefinition(service, schema, display_fields, capability, binding)


@dataclass
class FixedReadProfile(IdentityAssertionPort):
    service: ServiceConfig
    approval: FixedReadApproval
    output_contract: OutputContract
    capability: CapabilitySpec
    binding: ToolBinding
    # The existing port declares writable policy attributes, not properties.
    # Keep them out of constructor/config input and initialize from approval.
    version: str = dataclass_field(init=False)
    approved: bool = dataclass_field(init=False, default=True)
    tls_context: ssl.SSLContext | None = dataclass_field(init=False, default=None, repr=False)
    _bound_owner: FixedReadLocalOwner | None = dataclass_field(
        init=False, default=None, repr=False, compare=False
    )
    _owner_ready: bool = dataclass_field(init=False, default=False, repr=False, compare=False)
    _owner_lock: Lock = dataclass_field(
        init=False, default_factory=Lock, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self.version = self.approval.version

    def permits_authorization(
        self, service: ServiceConfig, session: AuthenticatedSessionContext
    ) -> bool:
        """Check the approved or once-bound owner before OAuth config or network work."""
        try:
            owner = FixedReadLocalOwner.from_session(session)
        except McpFailure:
            return False
        return service == self.service and self.permits(
            tenant_id=owner.tenant_id,
            user_id=owner.user_id,
            service_config_id=service.service_config_id,
            require_ready=False,
        )

    async def bind_authorization_owner(
        self,
        service_config_id: str,
        session: AuthenticatedSessionContext,
        revocations: SessionRevocationStorePort,
    ) -> None:
        """A single explicit Apps authorization may select the local owner once."""
        if self.approval.local_owner_source != "authenticated_session":
            return
        if (
            not self.service.enabled
            or service_config_id != self.service.service_config_id
        ):
            raise McpFailure("mcp_service_unavailable")
        owner = FixedReadLocalOwner.from_session(session)
        if (
            owner.tenant_id != self.approval.tenant_id
            or not owner.user_id
            or await revocations.is_revoked(owner.fingerprint)
        ):
            raise McpFailure("mcp_service_unavailable")
        with self._owner_lock:
            if self._bound_owner is None:
                self._bound_owner = owner
                record_mcp_event(
                    "fixed_owner_bound", "success", profile=self.service, tool=TOOL
                )
            elif self._bound_owner != owner:
                raise McpFailure("mcp_service_unavailable")
            self._owner_ready = False

    async def complete_authorization_owner(
        self, session: AuthenticatedSessionContext, store: McpStorePort
    ) -> None:
        """Release the local read only after the matching grant is ACTIVE."""
        if self.approval.local_owner_source != "authenticated_session":
            return
        owner = FixedReadLocalOwner.from_session(session)
        if self._bound_owner != owner:
            raise McpFailure("mcp_authorization_invalid")
        connection = await store.connection(
            tenant_id=owner.tenant_id,
            user_id=owner.user_id,
            service_config_id=self.service.service_config_id,
        )
        if (
            connection is None
            or connection.state != "ACTIVE"
            or connection.service_config_version != self.service.service_config_version
            or connection.login_session_fingerprint != owner.fingerprint.hex()
            or connection.identity_policy_version != self.version
            or not connection.identity_evidence
            or connection.expires_at is None
            or connection.expires_at <= datetime.now(UTC)
        ):
            raise McpFailure("mcp_authorization_invalid")
        with self._owner_lock:
            if self._bound_owner != owner:
                raise McpFailure("mcp_authorization_invalid")
            self._owner_ready = True

    def permits(
        self,
        *,
        tenant_id: str,
        user_id: str,
        service_config_id: str,
        tool: str = TOOL,
        require_ready: bool = True,
    ) -> bool:
        if (
            not self.service.enabled
            or tenant_id != self.approval.tenant_id
            or service_config_id != self.service.service_config_id
            or tool != TOOL
        ):
            return False
        if self.approval.local_owner_source == "fixed":
            return user_id == self.approval.user_id
        owner = self._bound_owner
        session = authenticated_session.get()
        now = datetime.now(UTC)
        return (
            owner is not None
            and session is not None
            and (not require_ready or self._owner_ready)
            and owner.tenant_id == tenant_id
            and owner.user_id == user_id == session.principal.ai_user_id
            and session.principal.org_ctx.tenant_id == tenant_id
            and owner.fingerprint == session.fingerprint
            and owner.expires_at > now
            and session.expires_at > now
        )

    def permits_candidate(
        self,
        *,
        ai_user_id: str,
        capability_id: str,
        request_context: RequestOrgContext,
    ) -> bool:
        return capability_id == self.capability.capability_id and self.permits(
            tenant_id=request_context.tenant_id,
            user_id=ai_user_id,
            service_config_id=self.service.service_config_id,
            require_ready=True,
        )

    async def verify(
        self,
        *,
        tenant_id: str,
        user_id: str,
        service_config_id: str,
        token_metadata: dict[str, Any],
    ) -> str | None:
        subject = token_metadata.get("verified_subject")
        connection_id = token_metadata.get("probe_connection_id")
        binding_epoch = token_metadata.get("probe_binding_epoch")
        fingerprint = token_metadata.get("probe_login_session_fingerprint")
        registration_id = token_metadata.get("registration_id")
        client_id = token_metadata.get("probe_client_id")
        if (
            not self.permits(
                tenant_id=tenant_id,
                user_id=user_id,
                service_config_id=service_config_id,
                require_ready=False,
            )
            or type(subject) is not str
            or not subject
            or not subject.isascii()
            or not subject.isdigit()
            or (
                self.approval.identity_binding_mode == "expected_subject"
                and subject != self.approval.expected_subject
            )
            or token_metadata.get("scope") != "mcp:business"
            or token_metadata.get("issuer") != self.service.issuer
            or token_metadata.get("identity_probe_version") != self.version
            or token_metadata.get("service_config_version") != self.service.service_config_version
            or type(connection_id) is not str or not connection_id
            or type(binding_epoch) is not int or binding_epoch < 1
            or type(fingerprint) is not str or len(fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in fingerprint)
            or type(registration_id) is not str or not registration_id
            or registration_id != token_metadata.get("probe_registration_id")
            or type(client_id) is not str or not client_id
        ):
            return None
        return digest(
            [
                self.version,
                self.approval.approval_evidence,
                self.approval.identity_binding_mode,
                tenant_id,
                user_id,
                service_config_id,
                self.service.service_config_version,
                connection_id,
                binding_epoch,
                fingerprint,
                registration_id,
                client_id,
                subject,
            ]
        )

    async def probe_authorization(
        self,
        service: ServiceConfig,
        transaction: AuthorizationTransaction,
        token: str,
        revalidate: Callable[[], Awaitable[None]],
    ) -> dict[str, Any]:
        """Fixed pre-grant read inside the formal OAuth completion flow only."""
        if (
            service != self.service
            or transaction.service_config_version != service.service_config_version
            or not self.permits(
                tenant_id=transaction.owner.tenant_id,
                user_id=transaction.owner.user_id,
                service_config_id=transaction.owner.service_config_id,
                require_ready=False,
            )
        ):
            raise McpFailure("mcp_identity_probe_denied")

        async def guarded_token() -> str:
            await revalidate()  # Every POST, including initialize and tools/list.
            return token

        record_mcp_event(
            "identity_probe",
            "started",
            profile=service,
            tool=TOOL,
            connection_id=transaction.owner.connection_id,
        )
        result = await McpDriver._exchange(
            service,
            guarded_token,
            TOOL,
            {},
            input_digest=input_digest(TOOL),
            safety_digest=safety_digest(TOOL),
            connection_id=transaction.owner.connection_id,
            tls_context=self.tls_context,
        )
        validated = self.output_contract.validate(result)
        if validated["clientId"] != transaction.client_id:
            raise McpFailure("mcp_identity_probe_denied")
        subject = validated["userId"]
        # Subject mismatch keeps the encrypted grant pending; it never activates
        # a connection and never uses this response as a later Chat answer.
        await revalidate()
        record_mcp_event(
            "identity_probe",
            "success",
            profile=service,
            tool=TOOL,
            connection_id=transaction.owner.connection_id,
            outbound_count=1,
        )
        return {
            "verified_subject": subject,
            "identity_probe_version": self.version,
            "service_config_version": service.service_config_version,
            "probe_connection_id": transaction.owner.connection_id,
            "probe_binding_epoch": transaction.binding_epoch,
            "probe_login_session_fingerprint": transaction.login_session_fingerprint,
            "probe_registration_id": transaction.registration_id,
            "probe_client_id": transaction.client_id,
        }

    def format_result(self, data: dict[str, Any]) -> str:
        # Called only after the approved recursive projection. No provider text
        # or hidden subject fields are consumed by the formatter.
        values = []
        for field, label in self.approval.display_fields.items():
            if field not in data:
                continue
            value = data[field]
            if isinstance(value, (dict, list)):
                text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            else:
                text = (
                    "未提供"
                    if value is None
                    else "是"
                    if value is True
                    else "否"
                    if value is False
                    else str(value)
                )
            values.append(f"{label}：{text}")
        return "业务信息：" + "；".join(values) if values else "业务读取完成，未返回可展示字段。"


def build_fixed_read_profile(
    service: ServiceConfig,
    approval: FixedReadApproval,
    *,
    prepared: PreparedFixedReadDefinition | None = None,
) -> FixedReadProfile:
    definition = prepared or prepare_fixed_read_definition(service)
    if (
        definition.service != service
        or (
            service.service_config_id,
            service.service_config_version,
            service.tenant_id,
        )
        != (
            approval.service_config_id,
            approval.service_config_version,
            approval.tenant_id,
        )
        or not approval.approval_evidence.strip()
        or not approval.display_fields
        or approval.subject_path != ["userId"]
        or not set(approval.display_fields) <= set(definition.display_fields)
        or approval.success_schema != definition.success_schema
    ):
        raise McpFailure("mcp_fixed_profile_invalid")
    # Root-field approval must never make the private identity branch public.
    # Displaying siblings inside that branch requires a separate, explicitly
    # approved nested projection and is outside this minimal profile.
    if approval.subject_path[0] in approval.display_fields:
        raise McpFailure("mcp_output_contract_invalid")
    schema = deepcopy(approval.success_schema)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise McpFailure("mcp_output_contract_invalid") from None
    fields = tuple(approval.display_fields)
    output = OutputContract(
        version=approval.version,
        schema=schema,
        model_fields=fields,
        ui_fields=fields,
        persistence_fields=fields,
        approval_evidence=approval.approval_evidence,
        synthetic=False,
    )
    output.require_production()
    public_schema = output.public_result_schema()
    if schema_has_credential_property(public_schema):
        raise McpFailure("mcp_output_contract_invalid")
    capability = CapabilitySpec.model_validate(
        {
            **definition.capability.model_dump(),
            "output_schema": public_schema,
            "output_schema_digest": digest(public_schema),
            "policy_digest": digest(
                [approval.version, approval.approval_evidence, service.service_config_id, TOOL]
            ),
            "status": "disabled",
        }
    )
    binding = ToolBinding.model_validate(
        {**definition.binding.model_dump(), "output_contract_version": output.version}
    )
    record_mcp_event("fixed_profile_loaded", "success", profile=service, tool=TOOL)
    return FixedReadProfile(service, approval, output, capability, binding)


def load_fixed_read_profile(
    services: dict[str, ServiceConfig],
    raw_approval: str | None,
    *,
    tls_contexts: Mapping[tuple[str, int, str], ssl.SSLContext] | None = None,
    prepared: PreparedFixedReadDefinition | None = None,
) -> FixedReadProfile | None:
    """Compose from non-secret approved config; absence leaves the profile off."""
    if raw_approval is None or not raw_approval.strip():
        return None
    try:
        approval = FixedReadApproval.model_validate_json(raw_approval)
        service = services[approval.service_config_id]
        matching_preparation = (
            prepared if prepared is not None and prepared.service == service else None
        )
        profile = build_fixed_read_profile(service, approval, prepared=matching_preparation)
        if tls_contexts is not None:
            profile.tls_context = tls_contexts.get(
                (service.service_config_id, service.service_config_version, service.endpoint)
            )
        return profile
    except Exception:
        # Pydantic diagnostics can include supplied values; expose only a code.
        raise McpFailure("mcp_fixed_profile_invalid") from None


class FixedReadCapabilityRegistry:
    """Prepare the one mapping behind existing AdminRegistryService commands.

    Compose this delegate only into the admin service, whose existing session
    and role checks run before create/enable/disable. This preserves the Admin
    API, its draft-first workflow and its audit records. Construction does no
    database work and never publishes anything on application startup.
    """

    def __init__(
        self,
        delegate: CapabilityRegistryPort,
        store: McpStorePort,
        profile: FixedReadProfile,
    ) -> None:
        self._delegate, self._store, self._profile = delegate, store, profile

    def _validate(self, capability: CapabilitySpec) -> None:
        if (
            capability.status == "deprecated"
            or capability.model_copy(update={"status": "disabled"}) != self._profile.capability
        ):
            raise McpFailure("mcp_capability_drift")

    async def create(self, capability: CapabilitySpec) -> CapabilitySpec:
        if capability.capability_id == self._profile.capability.capability_id:
            self._validate(capability)
            if capability.status not in {"draft", "disabled"}:
                raise McpFailure("mcp_capability_unprepared")
            await self._store.configure(self._profile.service)
            await self._store.bind_capability(self._profile.binding)
            record_mcp_event(
                "read_capability_prepared", "success", profile=self._profile.service, tool=TOOL
            )
        return await self._delegate.create(capability)

    async def get(self, capability_id: str) -> CapabilitySpec | None:
        return await self._delegate.get(capability_id)

    async def list(
        self,
        target_system: str | None = None,
        type: str | None = None,
        status: str | None = None,
    ) -> list[CapabilitySpec]:
        return await self._delegate.list(target_system, type, status)

    async def update(self, capability_id: str, patch: dict[str, Any]) -> CapabilitySpec:
        if capability_id == self._profile.capability.capability_id:
            current = await self._delegate.get(capability_id)
            if current is None:
                raise McpFailure("mcp_capability_unprepared")
            self._validate(CapabilitySpec.model_validate({**current.model_dump(), **patch}))
            if patch.get("status") == "active":
                if not self._profile.service.enabled:
                    raise McpFailure("mcp_service_unavailable")
                await self._store.configure(self._profile.service)
                if (
                    await self._store.mapping(capability_id, current.version)
                    != self._profile.binding
                ):
                    raise McpFailure("mcp_capability_unprepared")
        return await self._delegate.update(capability_id, patch)

    async def disable(self, capability_id: str) -> CapabilitySpec:
        return await self._delegate.disable(capability_id)
