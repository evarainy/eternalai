"""Input rules shared by personal memory extraction and editing."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from app.credential_markers import has_credential_marker
from app.ports.user_memories import MEMORY_CONTENT_LIMIT

MemoryInputReason = Literal["empty", "too_long", "sensitive"]

_SENSITIVE = (
    ("identity", re.compile(r"(?<!\d)(?:\d{17}[\dXx]|\d{15})(?!\d)")),
    ("bank_account", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    (
        "phone",
        re.compile(
            r"(?<!\d)(?:(?:\+?86[ -]?)?1[3-9](?:[ -]?\d){9}"
            r"|(?:\(0\d{2,3}\)|0\d{2,3})[ -]?\d{7,8})(?!\d)"
        ),
    ),
    (
        "address",
        re.compile(
            r"住址|家庭地址|居住地址|户籍地址|收货地址|住在|居住在|home address|"
            r"(?:路|街|巷|弄|胡同)\s*\d+\s*号|(?<!\d)\d+\s*(?:栋|幢|单元|室)",
            re.IGNORECASE,
        ),
    ),
    (
        "credential",
        re.compile(
            # ASCII boundaries: Chinese written right next to "PIN" still counts as a boundary.
            r"密码|口令|验证码|密钥|令牌|(?<![A-Za-z0-9_])(?:password|passcode|otp|pin|token|cookie|"
            r"sessionid|(?:api|secret)[ _-]?key|(?:access|refresh)[ _-]?token)(?![A-Za-z0-9_])",
            re.IGNORECASE,
        ),
    ),
)


class MemoryContentError(ValueError):
    """A safe rejection code for a memory candidate or manual edit."""

    def __init__(self, reason: MemoryInputReason, kind: str | None = None) -> None:
        self.reason = reason
        super().__init__(kind or reason)


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def sensitive_kind(value: str) -> str | None:
    comparable = unicodedata.normalize("NFKC", value)
    if has_credential_marker(comparable):
        return "credential"
    return next((kind for kind, pattern in _SENSITIVE if pattern.search(comparable)), None)


def validated_content(value: str) -> str:
    content = value.strip()
    if not content:
        raise MemoryContentError("empty")
    if len(content) > MEMORY_CONTENT_LIMIT:
        raise MemoryContentError("too_long")
    if re.search(r"[\x00\ud800-\udfff]", content):
        raise MemoryContentError("empty", "invalid_content")
    kind = sensitive_kind(content)
    if kind is not None:
        raise MemoryContentError("sensitive", kind)
    return content
