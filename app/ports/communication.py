"""Reader-scoped communication boundary; external identifiers are opaque strings."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncContextManager, Protocol

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True, slots=True)
class IMAccountBinding:
    binding_id: str
    tenant_id: str
    ai_user_id: str
    directory_user_id: str = field(repr=False)
    provider: str
    im_instance_id: str
    realm_id: str
    im_user_id: str
    login_subject: str = field(repr=False)
    status: str
    mapping_version: int


class IMSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=64)
    im_instance_id: str = Field(min_length=1, max_length=128)
    realm_id: str = Field(min_length=1, max_length=256)
    im_user_id: str = Field(min_length=1, max_length=256)
    message_id: str = Field(min_length=1, max_length=256)
    conversation_ref: str = Field(min_length=1, max_length=2048, repr=False)


@dataclass(frozen=True, slots=True)
class IMReadIdentity:
    provider: str
    im_instance_id: str
    realm_id: str
    im_user_id: str
    handle: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class IMMessage:
    message_id: str
    conversation_ref: str = field(repr=False)
    version: str
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class IMReadContext:
    messages: tuple[IMMessage, ...] = field(repr=False)
    found_anchor: bool
    deep_link: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class IMLoginForm:
    action_url: str
    fields: dict[str, str] = field(repr=False)
    expires_at: datetime


class CommunicationReaderPort(Protocol):
    def current_identity(
        self, binding: IMAccountBinding
    ) -> AsyncContextManager[IMReadIdentity]: ...

    async def read_anchor(self, identity: IMReadIdentity, selection: IMSelection) -> IMMessage: ...

    async def read_context(
        self,
        identity: IMReadIdentity,
        selection: IMSelection,
    ) -> IMReadContext: ...


class CommunicationLoginPort(Protocol):
    def build_login_form(self, binding: IMAccountBinding) -> IMLoginForm: ...
