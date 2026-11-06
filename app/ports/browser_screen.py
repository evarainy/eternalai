"""Remote view of a Run's own server-side browser window, with single-owner takeover.

Only typed, allowlisted inputs cross this port. Raw CDP, navigation, scripts,
file choosers and clipboard reads are never part of it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from app.ports.auth import Principal

ScreenErrorCode = Literal["denied", "unavailable", "busy", "overflow", "closed"]

# Named keys a controller may press. Printable text arrives only as ScreenText.
SCREEN_KEYS = frozenset(
    {
        "Enter",
        "Tab",
        "Backspace",
        "Delete",
        "Escape",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "Home",
        "End",
        "PageUp",
        "PageDown",
        "Control+a",
        "Control+z",
        "Shift+ArrowUp",
        "Shift+ArrowDown",
        "Shift+ArrowLeft",
        "Shift+ArrowRight",
    }
)
SCREEN_TEXT_MAX = 2000
SCREEN_PROMPT_MAX = 500


class BrowserScreenError(RuntimeError):
    def __init__(self, code: ScreenErrorCode) -> None:
        super().__init__("browser screen unavailable")
        self.code = code


@dataclass(frozen=True, slots=True, repr=False)
class ScreenFrame:
    """One JPEG frame with the page CSS viewport it was captured at."""

    data: bytes
    width: int
    height: int


@dataclass(frozen=True, slots=True, repr=False)
class ScreenMessage:
    """Client-ready control message (state, dialog, notices); never a log record."""

    payload: Mapping[str, object]


@dataclass(frozen=True, slots=True, repr=False)
class ScreenMouse:
    action: Literal["move", "down", "up", "wheel"]
    x: float
    y: float
    delta_x: float = 0.0
    delta_y: float = 0.0


@dataclass(frozen=True, slots=True, repr=False)
class ScreenKey:
    key: str


@dataclass(frozen=True, slots=True, repr=False)
class ScreenText:
    text: str


ScreenInput = ScreenMouse | ScreenKey | ScreenText


class BrowserScreenSession(Protocol):
    """One viewer connection; inputs act only while this viewer holds control."""

    async def next_event(self) -> ScreenFrame | ScreenMessage:
        """Next message or newest unseen frame; BrowserScreenError("closed") at the end."""
        ...

    def request_control(self) -> None:
        """Ask for control; the outcome arrives later as a message."""
        ...

    async def release_control(self) -> None: ...

    async def send(self, item: ScreenInput) -> None:
        """Queue one input; dropped unless this viewer holds control."""
        ...

    async def answer_dialog(self, dialog_id: str, accept: bool, prompt: str | None) -> None: ...

    async def recheck(self) -> None:
        """Re-verify owner, session binding and Run state; raises when no longer allowed."""
        ...

    async def close(self) -> None: ...


class BrowserScreenPort(Protocol):
    async def open(
        self,
        principal: Principal,
        bound_session: str,
        task_id: str,
        run_id: str,
    ) -> BrowserScreenSession:
        """Owner-checked view of a live Run window in this process."""
        ...

    async def aclose(self) -> None:
        """Process shutdown: detach every viewer and observer driver."""
        ...
