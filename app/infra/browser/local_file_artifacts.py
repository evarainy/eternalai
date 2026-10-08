"""Private downloaded-file spool; PG authority owns metadata and access decisions.

The caller supplies a trusted Run identity and a Playwright-owned temporary path.
Neither page-provided filename nor response Content-Type is used as a storage key
or proof of type. Nothing is readable until the authority records its metadata.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import os
import re
import secrets
import stat
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Protocol

FileMediaType = Literal[
    "application/pdf", "text/plain", "text/csv", "image/png",
]
_MAX_FILE_BYTES = 16 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024
_REFERENCE = re.compile(r"[A-Za-z0-9_-]{40,64}\Z")


class LocalFileArtifactError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("browser file artifact refused")


@dataclass(frozen=True, slots=True, repr=False)
class FileArtifactOwner:
    tenant_id: str
    ai_user_id: str
    session_id: str
    target_system: str
    binding_id: str
    binding_revision: int
    task_id: str
    run_id: str

    def __post_init__(self) -> None:
        if (any(type(value) is not str or not value or len(value) > 256
                for value in (self.tenant_id, self.ai_user_id, self.session_id,
                              self.target_system, self.binding_id, self.task_id,
                              self.run_id))
                or type(self.binding_revision) is not int
                or self.binding_revision < 0):
            raise LocalFileArtifactError("browser_file_owner_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class StoredFileArtifact:
    reference: str
    owner: FileArtifactOwner = field(repr=False)
    media_type: FileMediaType
    size_bytes: int
    sha256: bytes = field(repr=False)
    expires_at: datetime

    def __post_init__(self) -> None:
        if (type(self.reference) is not str or _REFERENCE.fullmatch(self.reference) is None
                or not isinstance(self.owner, FileArtifactOwner)
                or self.media_type not in {"application/pdf", "text/plain", "text/csv", "image/png"}
                or type(self.size_bytes) is not int or not 0 < self.size_bytes <= _MAX_FILE_BYTES
                or type(self.sha256) is not bytes or len(self.sha256) != 32
                or not isinstance(self.expires_at, datetime)
                or self.expires_at.tzinfo is None
                or self.expires_at.utcoffset() is None):
            raise LocalFileArtifactError("browser_file_metadata_invalid")


class FileArtifactAuthority(Protocol):
    """Implemented by the PG service, including fresh caller/binding checks."""

    async def check_create(self, owner: FileArtifactOwner) -> None: ...
    async def record(self, artifact: StoredFileArtifact) -> None: ...
    async def authorize_read(
        self, reference: str, owner: FileArtifactOwner,
    ) -> StoredFileArtifact: ...
    async def list_authorized(
        self, owner: FileArtifactOwner, limit: int,
    ) -> tuple[StoredFileArtifact, ...]: ...
    async def expired(self, before: datetime, limit: int) -> tuple[StoredFileArtifact, ...]: ...
    async def confirm_removed(self, artifact: StoredFileArtifact) -> None: ...


def _open_regular(path: Path) -> int:
    try:
        if path.is_symlink():
            raise LocalFileArtifactError("browser_file_source_invalid")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise LocalFileArtifactError("browser_file_source_invalid")
        return fd
    except OSError:
        raise LocalFileArtifactError("browser_file_source_invalid") from None


def _type_matches(payload: bytes, media_type: FileMediaType) -> bool:
    if media_type == "application/pdf":
        return bool(
            re.match(rb"%PDF-(?:1\.[0-7]|2\.0)(?:\r|\n)", payload[:12])
            and b"startxref" in payload[-2048:]
            and payload.rstrip().endswith(b"%%EOF")
        )
    if media_type == "image/png":
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            return False
        offset = 8
        first = True
        while offset + 12 <= len(payload):
            length = int.from_bytes(payload[offset:offset + 4], "big")
            end = offset + 12 + length
            if end > len(payload):
                return False
            kind = payload[offset + 4:offset + 8]
            data = payload[offset + 8:offset + 8 + length]
            checksum = int.from_bytes(payload[offset + 8 + length:end], "big")
            if zlib.crc32(data, zlib.crc32(kind)) != checksum:
                return False
            if first and (kind != b"IHDR" or length != 13
                          or int.from_bytes(data[:4], "big") == 0
                          or int.from_bytes(data[4:8], "big") == 0):
                return False
            first = False
            offset = end
            if kind == b"IEND":
                return length == 0 and offset == len(payload)
        return False
    if any(byte < 32 and byte not in (9, 10, 13) for byte in payload):
        return False
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    leading = text.lstrip().lower()
    if leading.startswith("<"):
        return False
    if media_type != "text/csv":
        return True
    try:
        dialect = csv.excel_tab if "\t" in text and "," not in text else csv.excel
        rows = csv.reader(io.StringIO(text), dialect=dialect, strict=True)
        return len(next(rows, ())) >= 2 and all(bool(row) for row in rows)
    except csv.Error:
        return False


class LocalFileArtifacts:
    """Bounded, private spool. A supplied path or reference never grants access."""

    def __init__(self, root: Path, authority: FileArtifactAuthority) -> None:
        if not isinstance(root, Path) or not root.is_absolute() or authority is None:
            raise LocalFileArtifactError("browser_file_configuration_invalid")
        self._root = root
        self._authority = authority

    def _root_path(self) -> Path:
        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            root = self._root.resolve(strict=True)
            mode = root.stat().st_mode
            if (self._root.is_symlink() or not root.is_dir()
                    or (os.name == "posix" and (
                        mode & 0o077 or root.stat().st_uid != getattr(os, "getuid")()
                    ))):
                raise LocalFileArtifactError("browser_file_root_invalid")
            return root
        except OSError:
            raise LocalFileArtifactError("browser_file_root_invalid") from None

    async def seal_download(
        self, source: Path, owner: FileArtifactOwner, *,
        media_type: FileMediaType, ttl_seconds: int,
        max_bytes: int = _MAX_FILE_BYTES,
    ) -> StoredFileArtifact:
        if (not isinstance(source, Path) or not source.is_absolute()
                or not isinstance(owner, FileArtifactOwner)
                or media_type not in {"application/pdf", "text/plain", "text/csv", "image/png"}
                or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86_400
                or type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_FILE_BYTES):
            raise LocalFileArtifactError("browser_file_request_invalid")
        await self._authority.check_create(owner)
        root = self._root_path()
        reference = secrets.token_urlsafe(32)
        destination = root / reference
        source_fd = _open_regular(source)
        created = False
        try:
            output_fd = os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            created = True
            digest = hashlib.sha256()
            size = 0
            with os.fdopen(source_fd, "rb") as input_stream, os.fdopen(output_fd, "wb") as output:
                source_fd = -1
                while chunk := input_stream.read(_CHUNK_BYTES):
                    size += len(chunk)
                    if size > max_bytes:
                        raise LocalFileArtifactError("browser_file_too_large")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if not size or not _type_matches(destination.read_bytes(), media_type):
                raise LocalFileArtifactError("browser_file_type_invalid")
            artifact = StoredFileArtifact(
                reference, owner, media_type, size, digest.digest(),
                datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
            )
            await self._authority.record(artifact)
            return artifact
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise
        finally:
            if source_fd >= 0:
                os.close(source_fd)

    async def read(self, reference: str, owner: FileArtifactOwner) -> bytes:
        if type(reference) is not str or _REFERENCE.fullmatch(reference) is None:
            raise LocalFileArtifactError("browser_file_reference_invalid")
        artifact = await self._authority.authorize_read(reference, owner)
        if (artifact.reference != reference or artifact.owner != owner
                or artifact.expires_at <= datetime.now(timezone.utc)):
            raise LocalFileArtifactError("browser_file_access_denied")
        path = self._root_path() / reference
        fd = _open_regular(path)
        with os.fdopen(fd, "rb") as stream:
            payload = stream.read(artifact.size_bytes + 1)
        if (len(payload) != artifact.size_bytes
                or not secrets.compare_digest(hashlib.sha256(payload).digest(), artifact.sha256)
                or not _type_matches(payload, artifact.media_type)):
            raise LocalFileArtifactError("browser_file_integrity_invalid")
        return payload

    async def list_artifacts(
        self, owner: FileArtifactOwner, *, limit: int = 100,
    ) -> tuple[StoredFileArtifact, ...]:
        """Metadata-only listing after fresh PG authority for the exact Run."""
        if (not isinstance(owner, FileArtifactOwner)
                or type(limit) is not int or not 1 <= limit <= 1000):
            raise LocalFileArtifactError("browser_file_request_invalid")
        records = await self._authority.list_authorized(owner, limit)
        now = datetime.now(timezone.utc)
        if (len(records) > limit or any(
            not isinstance(record, StoredFileArtifact) or record.owner != owner
            for record in records
        )):
            raise LocalFileArtifactError("browser_file_access_denied")
        return tuple(record for record in records if record.expires_at > now)

    async def cleanup_expired(self, *, limit: int = 100) -> int:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise LocalFileArtifactError("browser_file_cleanup_invalid")
        now = datetime.now(timezone.utc)
        records = await self._authority.expired(now, limit)
        removed = 0
        for artifact in records:
            if artifact.expires_at > now:
                continue
            path = self._root_path() / artifact.reference
            try:
                path.unlink(missing_ok=True)
                await self._authority.confirm_removed(artifact)
            except OSError:
                # Keep the PG record on deletion failure for later retry.
                continue
            removed += 1
            await asyncio.sleep(0)
        return removed
