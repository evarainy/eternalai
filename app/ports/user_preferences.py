"""The caller's own display preferences: appearance and home layout.

Both operations take only the server-authenticated principal, so reading or
writing somebody else's preferences is not expressible at the contract level.
The section contents are owned by the web client (skins, card ids) and are
validated there on read; the server stores them as opaque JSON objects.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

from app.ports.auth import Principal


class UserPreferences(BaseModel):
    """A missing section is ``None``; a missing record has both sections ``None``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    appearance: dict[str, JsonValue] | None = None
    home_layout: dict[str, JsonValue] | None = None


class UserPreferencesError(Exception):
    """Preference storage is unavailable."""


class UserPreferencesPort(Protocol):
    async def read(self, principal: Principal) -> UserPreferences: ...

    async def write(self, principal: Principal, preferences: UserPreferences) -> None: ...


__all__ = ("UserPreferences", "UserPreferencesError", "UserPreferencesPort")
