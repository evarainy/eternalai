"""Contract for the single document selected by server configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, get_args

FixedDocumentErrorCode = Literal[
    "document_unavailable",
    "document_encoding_invalid",
    "document_too_large",
    "document_empty",
    "document_configuration_invalid",
]


@dataclass(frozen=True)
class FixedDocumentSnapshot:
    """Normalized text and its UTF-8 SHA256 hex digest."""

    text: str = field(repr=False)
    version: str


class FixedDocumentReadError(Exception):
    """Expose only a bounded error code, never a filename or source exception."""

    def __init__(self, code: FixedDocumentErrorCode) -> None:
        self.code: FixedDocumentErrorCode = (
            code if code in get_args(FixedDocumentErrorCode) else "document_unavailable"
        )
        super().__init__(self.code)


class FixedDocumentPort(Protocol):
    async def read(self) -> FixedDocumentSnapshot: ...
