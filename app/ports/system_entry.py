"""Open a bound business system in the user's own browser, already signed in."""

from __future__ import annotations

from typing import Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.ports.credential_binding import CredentialTargetSystem, PasswordBindingCredential

SystemEntryTarget: TypeAlias = Literal["oa", "u8"]
SystemEntryFailureCode: TypeAlias = Literal[
    "entry_not_configured",
    "entry_not_bound",
    "entry_credentials_rejected",
    "entry_captcha_required",
    "entry_captcha_invalid",
    "entry_unavailable",
]


class SystemEntryView(BaseModel):
    """One-shot navigation target; never cached, logged or traced."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(repr=False)


class U8CaptchaAnswer(BaseModel):
    """Transient user-entered challenge; never saved with the password binding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    guid: str = Field(min_length=1, repr=False)
    code: SecretStr = Field(min_length=1)


class U8CaptchaView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    guid: str = Field(repr=False)
    image: str = Field(repr=False)


class SystemEntryError(RuntimeError):
    """Fixed-code failure without upstream text or credential values."""

    def __init__(self, code: SystemEntryFailureCode) -> None:
        super().__init__("system entry failed")
        self.code = code


class EntryPasswordReaderPort(Protocol):
    async def load_password_for_entry(
        self, ai_user_id: str, target_system: CredentialTargetSystem, *, tenant_id: str
    ) -> PasswordBindingCredential:
        """The caller's own saved password for an unrevoked binding.

        Read-only: claims no poll write stamp. Raises ``SystemEntryError("entry_not_bound")``
        when nothing usable is saved or the binding is marked invalid / captcha_required.
        """
        ...


class U8LoginPort(Protocol):
    async def get_captcha(self) -> U8CaptchaView:
        """Load U8's native image and challenge GUID without solving it."""
        ...

    async def login(
        self, credential: PasswordBindingCredential, *, captcha: U8CaptchaAnswer | None = None,
    ) -> SecretStr:
        """Sign in to U8 and return its bearer token.

        Raises ``SystemEntryError("entry_credentials_rejected")`` when U8 refuses the
        account, ``entry_captcha_required`` / ``entry_captcha_invalid`` for a missing or
        rejected challenge, and ``entry_unavailable`` for other upstream failures.
        """
        ...


class OAEntryTicketPort(Protocol):
    async def issue(self, cookies: dict[str, SecretStr]) -> str:
        """Hold one browser OA session for a short time; return a one-shot ticket."""
        ...


__all__ = (
    "EntryPasswordReaderPort",
    "OAEntryTicketPort",
    "SystemEntryError",
    "SystemEntryFailureCode",
    "SystemEntryTarget",
    "SystemEntryView",
    "U8CaptchaAnswer",
    "U8CaptchaView",
    "U8LoginPort",
)
