"""Ownership and input rules shared by personal memory extraction and editing."""

from __future__ import annotations

import re
import unicodedata

from app.ports.auth import Principal
from app.ports.user_memories import MEMORY_CONTENT_LIMIT, MemoryOwner

_SENSITIVE = (
    ("identity", re.compile(r"(?<!\d)(?:\d{17}[\dXx]|\d{15})(?!\d)")),
    ("bank_account", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("phone", re.compile(
        r"(?<!\d)(?:(?:\+?86[ -]?)?1[3-9](?:[ -]?\d){9}"
        r"|(?:\(0\d{2,3}\)|0\d{2,3})[ -]?\d{7,8})(?!\d)"
    )),
    ("address", re.compile(
        r"住址|家庭地址|居住地址|户籍地址|收货地址|住在|居住在|home address|"
        r"(?:路|街|巷|弄|胡同)\s*\d+\s*号|(?<!\d)\d+\s*(?:栋|幢|单元|室)",
        re.IGNORECASE,
    )),
    ("credential", re.compile(
        r"密码|口令|验证码|密钥|令牌|\b(?:password|passcode|otp|pin|token|cookie|"
        r"sessionid|(?:api|secret)[ _-]?key|(?:access|refresh)[ _-]?token)\b",
        re.IGNORECASE,
    )),
)


class MemoryContentError(ValueError):
    """A safe rejection code for a memory candidate or manual edit."""


def memory_owner(principal: Principal) -> MemoryOwner | None:
    org = principal.org_ctx
    if not org.department_id:
        return None
    return MemoryOwner(org.tenant_id, org.department_id, principal.ai_user_id)


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def sensitive_kind(value: str) -> str | None:
    comparable = unicodedata.normalize("NFKC", value)
    return next((kind for kind, pattern in _SENSITIVE if pattern.search(comparable)), None)


def validated_content(value: str) -> str:
    content = value.strip()
    if not 1 <= len(content) <= MEMORY_CONTENT_LIMIT or re.search(r"[\x00\ud800-\udfff]", content):
        raise MemoryContentError("invalid_content")
    kind = sensitive_kind(content)
    if kind is not None:
        raise MemoryContentError(kind)
    return content
