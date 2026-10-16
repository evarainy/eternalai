"""Admin-facing adapter for the installed D form management capability."""

from __future__ import annotations

from app.ports.auth import Principal
from app.ports.browser_fill_stop import (
    DFormManagementCommand,
    DFormManagementError,
    DFormManagementState,
)
from app.ports.browser_fill_stop import (
    DFormManagementPort as _DFormManagementPort,
)


class AdminDFormManagementService:
    def __init__(self, management: _DFormManagementPort) -> None:
        self._management = management

    async def check_fixed_access(self, principal: Principal) -> bool:
        checker = getattr(self._management, "check_fixed_access", None)
        if not callable(checker):
            return False
        result = await checker(principal)
        return result is True

    async def state(self, principal: Principal) -> DFormManagementState:
        return await self._management.state(principal)

    async def perform(
        self,
        principal: Principal,
        command: DFormManagementCommand,
    ) -> DFormManagementState:
        return await self._management.perform(principal, command)


__all__ = (
    "AdminDFormManagementService",
    "DFormManagementCommand",
    "DFormManagementError",
    "DFormManagementState",
)
