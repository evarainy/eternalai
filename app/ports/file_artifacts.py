"""File artifact port: owner-scoped downloads, chat uploads and worker-side outputs.

``public.file_artifacts`` is the authority for ownership and lifecycle; physical files
are only reachable through a verified row. Another user's artifact needs an explicit confirmed
delivery grant; without it, the artifact is indistinguishable from a missing one. Only the standard
library is imported here: the worker reaches this module through the persistence layer.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from app.ports.auth import Principal

CHAT_UPLOAD_MAX_BYTES = 20 * 1024 * 1024
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class FileArtifactError(Exception):
    """Contract failure carrying a stable error code and no input echo."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ChatAttachmentError(FileArtifactError):
    """A chat message's attachments were rejected (HTTP 422)."""


@dataclass(frozen=True)
class FileArtifactOwner:
    tenant_id: str
    ai_user_id: str


@dataclass(frozen=True)
class JobOutputOwner:
    """One claimed execution of a background job; the owner is read from the ledger."""

    job_id: UUID
    claim_token: UUID


@dataclass(frozen=True)
class FileArtifactDownload:
    """A verified file; ``pin_fd`` stays open until the response has been sent."""

    artifact_id: UUID
    path: Path = field(repr=False)
    pin_fd: int = field(repr=False)
    stat_result: os.stat_result = field(repr=False)
    display_filename: str = field(repr=False)
    media_type: str


@dataclass(frozen=True)
class FileShareMetadata:
    filename: str
    size_bytes: int
    sha256: bytes
    expires_at: datetime


class FileArtifactSharePort(Protocol):
    async def share_metadata(
        self, principal: Principal, artifact_id: UUID
    ) -> FileShareMetadata: ...


@dataclass(frozen=True)
class FileArtifactCleanupResult:
    scanned: int
    removed: int
    failed: int
    abandoned: int


@dataclass(frozen=True)
class SheetSummary:
    """One worksheet as recorded by the upload inspection; sizes may be unknown."""

    name: str
    max_row: int | None
    max_column: int | None


@dataclass(frozen=True)
class ChatUploadRequest:
    """One raw-body upload; ``deadline`` is the ``time.monotonic()`` end of its budget."""

    owner: FileArtifactOwner
    bound_session_id: str
    idempotency_key: UUID
    filename: str
    deadline: float


@dataclass(frozen=True)
class ChatUploadResult:
    artifact_id: UUID
    filename: str
    size_bytes: int
    sheets: tuple[SheetSummary, ...]
    expires_at: datetime
    replayed: bool


@dataclass(frozen=True)
class ChatAttachmentDescriptor:
    """A verified chat attachment; names are untrusted user data."""

    artifact_id: UUID
    filename: str
    sheets: tuple[SheetSummary, ...]


ChunkWriter = Callable[[bytes], Awaitable[None]]
OutputWriter = Callable[[ChunkWriter], Coroutine[Any, Any, None]]
ChunkSource = AsyncIterator[bytes]


class FileArtifactStorePort(Protocol):
    async def share_metadata(
        self, owner: FileArtifactOwner, artifact_id: UUID
    ) -> FileShareMetadata: ...

    async def open_download(
        self, owner: FileArtifactOwner, artifact_id: UUID
    ) -> FileArtifactDownload: ...

    async def publish_output(
        self, owner: JobOutputOwner, writer: OutputWriter, filename: str, media_type: str
    ) -> UUID: ...

    async def upload_chat_input(
        self, request: ChatUploadRequest, body: ChunkSource
    ) -> ChatUploadResult: ...

    async def resolve_chat_attachments(
        self, owner: FileArtifactOwner, bound_session_id: str, artifact_ids: tuple[UUID, ...]
    ) -> tuple[ChatAttachmentDescriptor, ...]: ...

    async def cleanup_once(self) -> FileArtifactCleanupResult: ...


class JobOutputPublisher(Protocol):
    """Publishes files for exactly one claimed execution of a background job."""

    async def publish(self, writer: OutputWriter, filename: str, media_type: str) -> UUID: ...


__all__ = (
    "CHAT_UPLOAD_MAX_BYTES",
    "XLSX_MEDIA_TYPE",
    "ChatAttachmentDescriptor",
    "ChatAttachmentError",
    "ChatUploadRequest",
    "ChatUploadResult",
    "ChunkSource",
    "ChunkWriter",
    "FileArtifactCleanupResult",
    "FileArtifactDownload",
    "FileArtifactError",
    "FileArtifactOwner",
    "FileArtifactStorePort",
    "FileArtifactSharePort",
    "FileShareMetadata",
    "JobOutputOwner",
    "JobOutputPublisher",
    "OutputWriter",
    "SheetSummary",
)
