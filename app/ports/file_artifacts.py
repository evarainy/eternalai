"""File artifact port: owner-scoped downloads and worker-side output publication.

``public.file_artifacts`` is the authority for ownership and lifecycle; physical files
are only reachable through a verified row. Another user's artifact is indistinguishable
from a missing one.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID


class FileArtifactError(Exception):
    """Contract failure carrying a stable error code and no input echo."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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
class FileArtifactCleanupResult:
    scanned: int
    removed: int
    failed: int
    abandoned: int


ChunkWriter = Callable[[bytes], Awaitable[None]]
OutputWriter = Callable[[ChunkWriter], Coroutine[Any, Any, None]]


class FileArtifactStorePort(Protocol):
    async def open_download(
        self, owner: FileArtifactOwner, artifact_id: UUID
    ) -> FileArtifactDownload: ...

    async def publish_output(
        self, owner: JobOutputOwner, writer: OutputWriter, filename: str, media_type: str
    ) -> UUID: ...

    async def cleanup_once(self) -> FileArtifactCleanupResult: ...


class JobOutputPublisher(Protocol):
    """Publishes files for exactly one claimed execution of a background job."""

    async def publish(self, writer: OutputWriter, filename: str, media_type: str) -> UUID: ...


__all__ = (
    "ChunkWriter",
    "FileArtifactCleanupResult",
    "FileArtifactDownload",
    "FileArtifactError",
    "FileArtifactOwner",
    "FileArtifactStorePort",
    "JobOutputOwner",
    "JobOutputPublisher",
    "OutputWriter",
)
