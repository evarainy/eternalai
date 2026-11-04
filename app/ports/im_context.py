"""Trusted, read-only IM sources issued by the application service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.ports.auth import Principal
from app.ports.communication import IMAccountBinding, IMMessage, IMSelection


class IMContextError(RuntimeError):
    def __init__(self, code: str, http_status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


class IMAccountBindingStorePort(Protocol):
    async def current(
        self,
        principal: Principal,
        *,
        provider: str,
        im_instance_id: str,
        realm_id: str,
    ) -> IMAccountBinding | None: ...


@dataclass(frozen=True, slots=True)
class IMSourceReference:
    ref_id: str = field(repr=False)
    expires_at: datetime
    messages: tuple[IMMessage, ...] = field(repr=False)
    deep_link: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AuthorizedIMContext:
    """Immutable turn capture; contains no authentication credential."""

    ref_ids: tuple[str, ...] = field(repr=False)
    messages: tuple[IMMessage, ...] = field(repr=False)
    authority: IMContextPort = field(repr=False)
    purpose: str = "self_read"


class IMContextPort(Protocol):
    async def select(
        self,
        principal: Principal,
        bound_session: str,
        selection: IMSelection,
    ) -> IMSourceReference: ...

    async def revalidate(
        self,
        principal: Principal,
        bound_session: str,
        ref_ids: tuple[str, ...],
    ) -> AuthorizedIMContext: ...
