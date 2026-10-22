"""Small private-file contract; no public blob URL or application dependency."""

from __future__ import annotations

import hashlib
import io
from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Protocol
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from pydantic import Field, model_validator

from app.ports.work_object_dispatch import DispatchModel, WorkDispatchError
from app.ports.work_object_scope import AuthorizedWorkObjectScope

AttachmentPurpose = Literal["task_material", "result"]
AttachmentVisibility = Literal["shared", "department_internal"]
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_TASK_ATTACHMENT_BYTES = 100 * 1024 * 1024
MAX_ATTACHMENTS_PER_REQUEST = 10


class AttachmentView(DispatchModel):
    attachment_id: UUID
    work_object_id: str | None
    handling_relation_id: UUID | None
    purpose: AttachmentPurpose
    visibility: AttachmentVisibility
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    version: int
    created_at: datetime
    linked_at: datetime | None


class AttachmentRecord(AttachmentView):
    tenant_id: str
    owner_ai_user_id: str
    detached_at: datetime | None
    idempotency_key: UUID
    request_fingerprint: str

    def view(self) -> AttachmentView:
        return AttachmentView.model_validate(
            self.model_dump(include=set(AttachmentView.model_fields))
        )


class AttachmentLinkRequest(DispatchModel):
    attachment_ids: list[UUID] = Field(min_length=1, max_length=MAX_ATTACHMENTS_PER_REQUEST)
    purpose: AttachmentPurpose
    visibility: AttachmentVisibility
    handling_relation_id: UUID | None = None

    @model_validator(mode="after")
    def canonicalize(self) -> AttachmentLinkRequest:
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("attachment references must be unique")
        if self.visibility == "department_internal" and self.handling_relation_id is None:
            raise ValueError("internal attachment requires a relation")
        object.__setattr__(self, "attachment_ids", sorted(self.attachment_ids, key=str))
        return self


class AttachmentMutationResult(DispatchModel):
    items: list[AttachmentView]
    etag: str
    replayed: bool


class AttachmentDownload(DispatchModel):
    attachment: AttachmentView
    payload: bytes = Field(repr=False)


def validate_attachment(filename: str, media_type: str, payload: bytes) -> str:
    """Basic declared-type/actual-format boundary; never an antivirus claim."""
    if not payload or len(payload) > MAX_ATTACHMENT_BYTES:
        raise WorkDispatchError(
            "work_object_attachment_too_large" if payload else "work_object_attachment_invalid"
        )
    if (
        not filename
        or len(filename) > 255
        or filename in {".", ".."}
        or PurePosixPath(filename).name != filename
        or PureWindowsPath(filename).name != filename
        or any(ord(c) < 32 or c in '<>:"|?*' for c in filename)
    ):
        raise WorkDispatchError("work_object_attachment_invalid")
    extension = PurePosixPath(filename).suffix.lower()
    media = {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }
    valid = media.get(extension) == media_type
    if extension == ".pdf":
        valid = valid and payload.startswith(b"%PDF-") and b"%%EOF" in payload[-1024:]
    elif extension in {".jpg", ".jpeg"}:
        valid = valid and payload.startswith(b"\xff\xd8\xff") and payload.endswith(b"\xff\xd9")
    elif extension == ".png":
        valid = (
            valid
            and payload.startswith(b"\x89PNG\r\n\x1a\n")
            and payload[12:16] == b"IHDR"
            and payload[-8:-4] == b"IEND"
        )
    elif extension in {".docx", ".xlsx", ".pptx"}:
        required = {
            ".docx": "word/document.xml",
            ".xlsx": "xl/workbook.xml",
            ".pptx": "ppt/presentation.xml",
        }[extension]
        try:
            with ZipFile(io.BytesIO(payload)) as archive:
                names = set(archive.namelist())
                valid = valid and {"[Content_Types].xml", "_rels/.rels", required}.issubset(names)
        except BadZipFile:
            valid = False
    if not valid:
        raise WorkDispatchError("work_object_attachment_invalid")
    return hashlib.sha256(payload).hexdigest()


class WorkObjectAttachmentStorePort(Protocol):
    async def upload(
        self,
        scope: AuthorizedWorkObjectScope,
        *,
        filename: str,
        media_type: str,
        purpose: AttachmentPurpose,
        payload: bytes,
        sha256: str,
        idempotency_key: UUID,
        now: datetime,
    ) -> AttachmentView: ...
    async def list_for_task(
        self, scope: AuthorizedWorkObjectScope, work_object_id: str
    ) -> list[AttachmentView]: ...
    async def link(
        self,
        scope: AuthorizedWorkObjectScope,
        work_object_id: str,
        *,
        request: AttachmentLinkRequest,
        if_match: str,
        idempotency_key: UUID,
        now: datetime,
    ) -> AttachmentMutationResult: ...
    async def detach(
        self,
        scope: AuthorizedWorkObjectScope,
        work_object_id: str,
        attachment_id: UUID,
        *,
        if_match: str,
        idempotency_key: UUID,
        now: datetime,
    ) -> AttachmentMutationResult: ...
    async def download(
        self, scope: AuthorizedWorkObjectScope, work_object_id: str, attachment_id: UUID
    ) -> AttachmentDownload: ...
