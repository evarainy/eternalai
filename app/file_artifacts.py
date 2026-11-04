"""File artifact service: Principal-to-owner mapping over the store port."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.ports.auth import Principal
from app.ports.file_artifacts import (
    FileArtifactCleanupResult,
    FileArtifactDownload,
    FileArtifactOwner,
    FileArtifactStorePort,
    JobOutputOwner,
    JobOutputPublisher,
    OutputWriter,
)


@dataclass(frozen=True)
class _BoundJobOutputs:
    service: FileArtifactService
    owner: JobOutputOwner

    async def publish(self, writer: OutputWriter, filename: str, media_type: str) -> UUID:
        return await self.service.publish_output(self.owner, writer, filename, media_type)


class FileArtifactService:
    def __init__(self, store: FileArtifactStorePort) -> None:
        self._store = store

    async def authorize_download(
        self, principal: Principal, artifact_id: UUID
    ) -> FileArtifactDownload:
        return await self._store.open_download(
            FileArtifactOwner(
                tenant_id=principal.org_ctx.tenant_id, ai_user_id=principal.ai_user_id
            ),
            artifact_id,
        )

    async def publish_output(
        self, owner: JobOutputOwner, writer: OutputWriter, filename: str, media_type: str
    ) -> UUID:
        return await self._store.publish_output(owner, writer, filename, media_type)

    def job_outputs(self, owner: JobOutputOwner) -> JobOutputPublisher:
        return _BoundJobOutputs(self, owner)

    async def cleanup(self) -> FileArtifactCleanupResult:
        return await self._store.cleanup_once()


__all__ = ("FileArtifactService",)
