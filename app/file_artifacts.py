"""File artifact service: Principal-to-owner mapping over the store port.

Chat inputs may also be read back for sheet filling: row 1 headers through the injected
isolated reader (never parsed in this process) and the verified bytes for the worker.
Every read re-resolves the attachment for its owner and bound chat session first.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.ports.auth import Principal
from app.ports.file_artifacts import (
    ChatAttachmentDescriptor,
    ChatAttachmentError,
    ChatInputInspection,
    ChatUploadRequest,
    ChatUploadResult,
    ChunkSource,
    FileArtifactCleanupResult,
    FileArtifactDownload,
    FileArtifactError,
    FileArtifactOwner,
    FileArtifactStorePort,
    FileShareMetadata,
    JobOutputOwner,
    JobOutputPublisher,
    OutputWriter,
    WorkbookHeaders,
)

_LOGGER = logging.getLogger("uvicorn.error.file_artifacts")
_READ_CHUNK = 1024 * 1024
HeaderReader = Callable[[Path], WorkbookHeaders]


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


def _read_all(fd: int, size: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0 and (chunk := os.read(fd, min(_READ_CHUNK, remaining))):
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _with_headers(
    descriptor: ChatAttachmentDescriptor, headers: WorkbookHeaders
) -> ChatAttachmentDescriptor:
    return dataclasses.replace(
        descriptor,
        sheets=tuple(
            dataclasses.replace(sheet, header=headers.headers.get(sheet.name))
            for sheet in descriptor.sheets
        ),
    )


class FileArtifactService:
    def __init__(
        self, store: FileArtifactStorePort, header_reader: HeaderReader | None = None
    ) -> None:
        self._store = store
        self._header_reader = header_reader

    async def share_metadata(
        self, principal: Principal, artifact_id: UUID
    ) -> FileShareMetadata:
        return await self._store.share_metadata(_owner(principal), artifact_id)

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
        self,
        principal: Principal,
        bound_session_id: str,
        artifact_ids: Sequence[UUID],
        *,
        with_headers: bool = False,
    ) -> tuple[ChatAttachmentDescriptor, ...]:
        """``with_headers`` adds row 1 of each sheet; a failed read leaves it unknown."""

        owner = _owner(principal)
        descriptors = await self._store.resolve_chat_attachments(
            owner, bound_session_id, tuple(artifact_ids)
        )
        if not with_headers or self._header_reader is None:
            return descriptors
        enriched: list[ChatAttachmentDescriptor] = []
        for descriptor in descriptors:
            try:
                headers = await self._read_headers(owner, descriptor.artifact_id)
            except FileArtifactError as error:
                _LOGGER.info("chat_attachment_headers result=%s", error.code)
                enriched.append(descriptor)
                continue
            enriched.append(_with_headers(descriptor, headers))
        return tuple(enriched)

    async def _read_headers(self, owner: FileArtifactOwner, artifact_id: UUID) -> WorkbookHeaders:
        assert self._header_reader is not None
        download = await self._store.open_download(owner, artifact_id)
        try:
            return await asyncio.to_thread(self._header_reader, download.path)
        finally:
            os.close(download.pin_fd)

    async def _resolve_one(
        self, owner: FileArtifactOwner, bound_session_id: str, artifact_id: UUID
    ) -> ChatAttachmentDescriptor:
        descriptors = await self._store.resolve_chat_attachments(
            owner, bound_session_id, (artifact_id,)
        )
        if len(descriptors) != 1:
            raise ChatAttachmentError("chat_attachment_invalid")
        return descriptors[0]

    async def inspect_chat_input(
        self, owner: FileArtifactOwner, bound_session_id: str, artifact_id: UUID
    ) -> ChatInputInspection:
        """Owner, session, purpose and expiry rechecked; headers read by the isolated child."""

        if self._header_reader is None:
            raise FileArtifactError("chat_upload_unreadable")
        descriptor = await self._resolve_one(owner, bound_session_id, artifact_id)
        metadata = await self._store.share_metadata(owner, artifact_id)
        headers = await self._read_headers(owner, artifact_id)
        return ChatInputInspection(
            descriptor=_with_headers(descriptor, headers),
            sha256=metadata.sha256.hex(),
            unsupported_objects=headers.unsupported_objects,
        )

    async def read_chat_input(
        self, owner: FileArtifactOwner, bound_session_id: str, artifact_id: UUID
    ) -> bytes:
        """The verified bytes of the owner's chat input in that session (worker side)."""

        await self._resolve_one(owner, bound_session_id, artifact_id)
        download = await self._store.open_download(owner, artifact_id)
        try:
            return await asyncio.to_thread(
                _read_all, download.pin_fd, download.stat_result.st_size
            )
        finally:
            os.close(download.pin_fd)

    async def publish_output(
        self, owner: JobOutputOwner, writer: OutputWriter, filename: str, media_type: str
    ) -> UUID:
        return await self._store.publish_output(owner, writer, filename, media_type)

    def job_outputs(self, owner: JobOutputOwner) -> JobOutputPublisher:
        return _BoundJobOutputs(self, owner)

    async def cleanup(self) -> FileArtifactCleanupResult:
        return await self._store.cleanup_once()


__all__ = ("FileArtifactService",)
