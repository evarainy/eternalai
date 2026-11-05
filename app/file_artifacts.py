"""File artifact service: Principal-to-owner mapping over the store port."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.ports.auth import Principal
from app.ports.file_artifacts import (
    ChatAttachmentDescriptor,
    ChatUploadRequest,
    ChatUploadResult,
    ChunkSource,
    FileArtifactCleanupResult,
    FileArtifactDownload,
    FileArtifactOwner,
    FileArtifactStorePort,
    JobOutputOwner,
    JobOutputPublisher,
    OutputWriter,
)


def _owner(principal: Principal) -> FileArtifactOwner:
    return FileArtifactOwner(
        tenant_id=principal.org_ctx.tenant_id, ai_user_id=principal.ai_user_id
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
        return await self._store.open_download(_owner(principal), artifact_id)

    async def upload_chat_input(
        self,
        principal: Principal,
        bound_session_id: str,
        idempotency_key: UUID,
        filename: str,
        body: ChunkSource,
        *,
        deadline: float,
    ) -> ChatUploadResult:
        return await self._store.upload_chat_input(
            ChatUploadRequest(
                owner=_owner(principal),
                bound_session_id=bound_session_id,
                idempotency_key=idempotency_key,
                filename=filename,
                deadline=deadline,
            ),
            body,
        )

    async def resolve_chat_attachments(
        self, principal: Principal, bound_session_id: str, artifact_ids: Sequence[UUID]
    ) -> tuple[ChatAttachmentDescriptor, ...]:
        return await self._store.resolve_chat_attachments(
            _owner(principal), bound_session_id, tuple(artifact_ids)
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
