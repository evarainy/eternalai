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
_STORAGE_SCOPE = re.compile(r"[0-9a-f]{64}\Z")
_STORAGE_MARKER = ".browser-file-storage-scope-v1"


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
    storage_scope: str = field(repr=False)

    def __post_init__(self) -> None:
        if (type(self.reference) is not str or _REFERENCE.fullmatch(self.reference) is None
                or not isinstance(self.owner, FileArtifactOwner)
                or self.media_type not in {"application/pdf", "text/plain", "text/csv", "image/png"}
                or type(self.size_bytes) is not int or not 0 < self.size_bytes <= _MAX_FILE_BYTES
                or type(self.sha256) is not bytes or len(self.sha256) != 32
                or not isinstance(self.expires_at, datetime)
                or self.expires_at.tzinfo is None
                or self.expires_at.utcoffset() is None
                or type(self.storage_scope) is not str
                or _STORAGE_SCOPE.fullmatch(self.storage_scope) is None):
            raise LocalFileArtifactError("browser_file_metadata_invalid")


class FileArtifactAuthority(Protocol):
    """Implemented by the PG service, including fresh caller/binding checks."""

    @property
    def storage_scope(self) -> str: ...

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


def _private_root(path: Path, *, create: bool) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise LocalFileArtifactError("browser_file_root_invalid")
    try:
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        root = path.resolve(strict=True)
        metadata = root.stat()
        if (path.is_symlink() or not root.is_dir()
                or (os.name == "posix" and (
                    metadata.st_mode & 0o077
                    or metadata.st_uid != getattr(os, "getuid")()
                ))):
            raise LocalFileArtifactError("browser_file_root_invalid")
        return root
    except OSError:
        raise LocalFileArtifactError("browser_file_root_invalid") from None


def _read_storage_scope(root: Path) -> str:
    """Read the physical spool's private identity; never synthesize a replacement."""
    try:
        fd = _open_regular(root / _STORAGE_MARKER)
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if os.name == "posix" and (
                metadata.st_mode & 0o077
                or metadata.st_uid != getattr(os, "getuid")()
            ):
                raise LocalFileArtifactError("browser_file_storage_scope_invalid")
            value = stream.read(65).decode("ascii")
        if _STORAGE_SCOPE.fullmatch(value) is None:
            raise LocalFileArtifactError("browser_file_storage_scope_invalid")
        return value
    except (OSError, UnicodeError, LocalFileArtifactError):
        raise LocalFileArtifactError("browser_file_storage_scope_invalid") from None


def initialize_file_artifact_storage_scope(root: Path, *, create: bool = True) -> str:
    """Initialize only an empty worker spool; API mounts read an existing marker."""
    if type(create) is not bool:
        raise LocalFileArtifactError("browser_file_configuration_invalid")
    location = _private_root(root, create=create)
    marker = location / _STORAGE_MARKER
    if marker.exists() or marker.is_symlink() or not create:
        return _read_storage_scope(location)
    # A pre-existing unmarked spool has unknown ownership. Do not take it over.
    if any(location.iterdir()):
        raise LocalFileArtifactError("browser_file_storage_scope_unknown")
    try:
        fd = os.open(
            marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(secrets.token_hex(32).encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        # Another worker owns initialization; an incomplete marker fails closed.
        pass
    except OSError:
        raise LocalFileArtifactError("browser_file_storage_scope_invalid") from None
    return _read_storage_scope(location)


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
        self._storage_scope = authority.storage_scope
        if (type(self._storage_scope) is not str
                or _STORAGE_SCOPE.fullmatch(self._storage_scope) is None):
            raise LocalFileArtifactError("browser_file_storage_scope_invalid")
        self._root_path()
        self.last_cleanup: dict[str, int | str] = {
            "scanned": 0, "removed": 0, "failed": 0, "result": "not_run",
        }

    def _root_path(self) -> Path:
        root = _private_root(self._root, create=False)
        if not secrets.compare_digest(_read_storage_scope(root), self._storage_scope):
            raise LocalFileArtifactError("browser_file_storage_scope_mismatch")
        return root

    def _assert_storage(self, artifact: StoredFileArtifact) -> None:
        if (not isinstance(artifact, StoredFileArtifact)
                or artifact.storage_scope != self._storage_scope):
            raise LocalFileArtifactError("browser_file_storage_scope_mismatch")

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
                self._storage_scope,
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
        self._assert_storage(artifact)
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
        self._root_path()
        for record in records:
            self._assert_storage(record)
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
        try:
            root = self._root_path()
            records = await self._authority.expired(now, limit)
        except Exception:
            self.last_cleanup = {
                "scanned": 0, "removed": 0, "failed": 1, "result": "authority_failed",
            }
            raise
        if len(records) > limit:
            self.last_cleanup = {
                "scanned": len(records), "removed": 0, "failed": 1,
                "result": "authority_failed",
            }
            raise LocalFileArtifactError("browser_file_cleanup_invalid")
        removed = 0
        failed = 0
        for artifact in records:
            if not isinstance(artifact, StoredFileArtifact) or artifact.expires_at > now:
                failed += 1
                continue
            try:
                self._assert_storage(artifact)
                # Prove the marker still belongs to this authority after the
                # awaited scan, before treating an absent path as removed.
                if self._root_path() != root:
                    raise LocalFileArtifactError("browser_file_storage_scope_mismatch")
                path = root / artifact.reference
                if path.is_symlink() or (path.exists() and not path.is_file()):
                    raise LocalFileArtifactError("browser_file_cleanup_invalid")
                path.unlink(missing_ok=True)
                await self._authority.confirm_removed(artifact)
            except Exception:
                # Keep the durable record on deletion or confirmation failure.
                failed += 1
                continue
            removed += 1
            await asyncio.sleep(0)
        self.last_cleanup = {
            "scanned": len(records), "removed": removed, "failed": failed,
            "result": "incomplete" if failed else "ok",
        }
        if failed:
            raise LocalFileArtifactError("browser_file_cleanup_incomplete")
        return removed


class FileArtifactCleanupScheduler:
    """Independent bounded spool sweeps, including the first post-start pass."""

    def __init__(
        self, artifacts: LocalFileArtifacts, *, interval_seconds: int = 60,
        batch_limit: int = 100,
    ) -> None:
        if (not isinstance(artifacts, LocalFileArtifacts)
                or type(interval_seconds) is not int or not 10 <= interval_seconds <= 3600
                or type(batch_limit) is not int or not 1 <= batch_limit <= 1000):
            raise LocalFileArtifactError("browser_file_cleanup_invalid")
        self._artifacts = artifacts
        self._interval = interval_seconds
        self._limit = batch_limit
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.last_result = "not_run"

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._run(), name="browser-file-expiry-cleanup",
        )

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._artifacts.cleanup_expired(limit=self._limit)
            except asyncio.CancelledError:
                raise
            except Exception:
                # The PG event stays available for retry; never log a path or owner.
                self.last_result = "failed"
            else:
                self.last_result = "ok"
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                continue
