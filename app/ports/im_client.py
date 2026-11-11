"""Current-user HTTP client boundary; transport and credentials stay in infra."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import AsyncContextManager, Literal, Protocol

from app.ports.auth import Principal
from app.ports.communication import IMAccountBinding, IMReadIdentity


@dataclass(frozen=True, slots=True)
class IMClientBindingSnapshot:
    binding: IMAccountBinding = field(repr=False)
    binding_id: str
    binding_version: str


class IMClientBindingStorePort(Protocol):
    async def current_client(
        self, principal: Principal, *, provider: str, im_instance_id: str, realm_id: str,
    ) -> IMClientBindingSnapshot | None: ...


@dataclass(frozen=True, slots=True)
class IMClientRequest:
    kind: Literal["api", "file"]
    method: str
    path: str = field(repr=False)
    query: bytes = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    body: AsyncIterator[bytes] = field(repr=False)
    session_scope: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class IMClientResponse:
    status: int
    headers: Mapping[str, str] = field(repr=False)
    body: AsyncIterator[bytes] = field(repr=False)


class IMClientError(RuntimeError):
    def __init__(
        self, code: str, http_status: int, message: str, *,
        invalidate_credentials: bool = False, headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.code, self.http_status, self.message = code, http_status, message
        self.invalidate_credentials = invalidate_credentials
        self.headers = headers or {}


class CommunicationClientPort(Protocol):
    async def client_identity(self, binding: IMAccountBinding) -> IMReadIdentity: ...

    def request(
        self, identity: IMReadIdentity, request: IMClientRequest, *,
        revalidate: Callable[[], Awaitable[None]],
    ) -> AsyncContextManager[IMClientResponse]: ...

    def release_scope(self, identity: IMReadIdentity, session_scope: str) -> None: ...

    async def aclose(self) -> None: ...
