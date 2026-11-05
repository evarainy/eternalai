"""Private on-disk layout for file artifacts (FOUNDATION_PLAN v1.6 §2.2).

``<root>/.file-artifact-storage-scope-v1`` identifies the physical root,
``objects/<reference>`` holds published files and ``staging/<reference>.part`` files in
progress. Root initialization is an explicit command that only writes into an empty
directory. Windows notes: every ``os.open`` passes ``O_BINARY`` because CRT descriptors
default to text mode, and a publishing rename uses ``MOVEFILE_WRITE_THROUGH`` because a
directory cannot be fsynced there.

Usage: ``uv run python -m app.infra.files.local_store init --kind standard <root>``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from app.ports.file_artifacts import FileArtifactError

StorageKind = Literal["standard", "acceptance_sample"]
STORAGE_KINDS: tuple[StorageKind, ...] = ("standard", "acceptance_sample")
_MARKER = ".file-artifact-storage-scope-v1"
_MAX_MARKER_BYTES = 256
_SCOPE = re.compile(r"[0-9a-f]{64}\Z")
_REFERENCE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_CHUNK_BYTES = 1024 * 1024
_BINARY = getattr(os, "O_BINARY", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_OBJECTS = "objects"
_STAGING = "staging"


def _is_link(path: Path) -> bool:
    return path.is_symlink() or os.path.isjunction(path)


def _private_root(path: Path, *, create: bool) -> Path:
    if not path.is_absolute():
        raise FileArtifactError("file_artifact_root_invalid")
    try:
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        elif not path.exists():
            raise FileArtifactError("file_artifact_root_uninitialized")
        if _is_link(path):
            raise FileArtifactError("file_artifact_root_invalid")
        root = path.resolve(strict=True)
        metadata = root.stat()
        if not root.is_dir() or (
            os.name == "posix"
            and (metadata.st_mode & 0o077 or metadata.st_uid != getattr(os, "getuid")())
        ):
            raise FileArtifactError("file_artifact_root_invalid")
        return root
    except OSError:
        raise FileArtifactError("file_artifact_root_invalid") from None


def open_regular(path: Path) -> int:
    """Open an existing regular file read-only, refusing to follow a link."""

    if _is_link(path):
        raise FileArtifactError("not_regular_file")
    fd = os.open(path, os.O_RDONLY | _BINARY | _NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise FileArtifactError("not_regular_file")
    except BaseException:
        os.close(fd)
        raise
    return fd


def create_exclusive(path: Path) -> int:
    """Create a new private file for writing; never reuses an existing entry."""

    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY | _NOFOLLOW, 0o600)


def write_all(fd: int, chunk: bytes) -> None:
    view = memoryview(chunk)
    while view:
        view = view[os.write(fd, view) :]


def sync_and_close(fd: int) -> None:
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def remove_regular(path: Path) -> None:
    """Remove one regular file without following links; a missing file is success."""

    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            raise FileArtifactError("not_regular_file")
        os.unlink(path)
    except FileNotFoundError:
        return


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _MOVEFILE_REPLACE_EXISTING = 0x1
    _MOVEFILE_WRITE_THROUGH = 0x8
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.MoveFileExW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    _kernel32.MoveFileExW.restype = wintypes.BOOL

    def durable_replace(source: Path, target: Path) -> None:
        """Returns only once the move has reached the disk (Win32 ``MoveFileExW``)."""

        if not _kernel32.MoveFileExW(
            str(source), str(target), _MOVEFILE_REPLACE_EXISTING | _MOVEFILE_WRITE_THROUGH
        ):
            raise ctypes.WinError(ctypes.get_last_error())
else:

    def durable_replace(source: Path, target: Path) -> None:
        os.replace(source, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def open_verified(path: Path, size_bytes: int, sha256: bytes) -> tuple[int, os.stat_result]:
    """Open a published file and keep it open once its size and digest match."""

    fd = open_regular(path)
    try:
        metadata = os.fstat(fd)
        if metadata.st_size != size_bytes:
            raise FileArtifactError("file_artifact_content_mismatch")
        digest = hashlib.sha256()
        while chunk := os.read(fd, _CHUNK_BYTES):
            digest.update(chunk)
        if digest.digest() != sha256:
            raise FileArtifactError("file_artifact_content_mismatch")
    except BaseException:
        os.close(fd)
        raise
    return fd, metadata


@dataclass(frozen=True)
class LocalFileStorage:
    root: Path
    scope: str
    kind: StorageKind

    def object_path(self, reference: str) -> Path:
        return self.root / _OBJECTS / _checked_reference(reference)

    def staging_path(self, reference: str) -> Path:
        return self.root / _STAGING / f"{_checked_reference(reference)}.part"


def _checked_reference(reference: str) -> str:
    if _REFERENCE.fullmatch(reference) is None:
        raise FileArtifactError("metadata_invalid")
    return reference


def _read_marker(root: Path) -> tuple[str, StorageKind]:
    try:
        fd = open_regular(root / _MARKER)
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if os.name == "posix" and (
                metadata.st_mode & 0o077 or metadata.st_uid != getattr(os, "getuid")()
            ):
                raise FileArtifactError("file_artifact_root_invalid")
            payload = json.loads(stream.read(_MAX_MARKER_BYTES + 1).decode("ascii"))
        scope, kind = payload["scope"], payload["kind"]
        if (
            type(scope) is not str
            or _SCOPE.fullmatch(scope) is None
            or kind not in STORAGE_KINDS
        ):
            raise FileArtifactError("file_artifact_root_invalid")
    except (FileArtifactError, OSError, ValueError, KeyError, TypeError):
        raise FileArtifactError("file_artifact_root_invalid") from None
    return scope, cast(StorageKind, kind)


def _is_real_directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode) and not _is_link(path)
    except OSError:
        return False


def _ensure_layout(root: Path) -> None:
    for name in (_OBJECTS, _STAGING):
        directory = root / name
        if not os.path.lexists(directory):
            directory.mkdir(mode=0o700)
        elif not _is_real_directory(directory):
            raise FileArtifactError("file_artifact_root_invalid")


def open_storage(root: Path) -> LocalFileStorage:
    """Read an initialized root; never creates anything."""

    location = _private_root(root, create=False)
    if not os.path.lexists(location / _MARKER):
        raise FileArtifactError("file_artifact_root_uninitialized")
    scope, kind = _read_marker(location)
    if not all(_is_real_directory(location / name) for name in (_OBJECTS, _STAGING)):
        raise FileArtifactError("file_artifact_root_invalid")
    return LocalFileStorage(root=location, scope=scope, kind=kind)


def initialize_storage(root: Path, kind: StorageKind) -> tuple[str, str]:
    """Initialize an empty root; returns ``(status, scope)``. Never adopts unknown content."""

    location = _private_root(root, create=True)
    marker = location / _MARKER
    if not os.path.lexists(marker):
        if any(location.iterdir()):
            raise FileArtifactError("file_artifact_root_unrecognized")
        scope = secrets.token_hex(32)
        document = json.dumps({"scope": scope, "kind": kind}, separators=(",", ":"))
        fd = create_exclusive(marker)
        with os.fdopen(fd, "wb") as stream:
            stream.write(document.encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        _ensure_layout(location)
        return "initialized", scope
    scope, existing = _read_marker(location)
    if existing != kind:
        raise FileArtifactError("file_artifact_root_kind_mismatch")
    _ensure_layout(location)
    return "already_initialized", scope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.infra.files.local_store")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--kind", choices=STORAGE_KINDS, required=True)
    init.add_argument("root", type=Path)
    arguments = parser.parse_args(argv)
    try:
        result, scope = initialize_storage(arguments.root, arguments.kind)
    except FileArtifactError as error:
        print(f"file_artifact_storage_error code={error.code}", file=sys.stderr)
        return 1
    except OSError:
        print("file_artifact_storage_error code=file_artifact_root_invalid", file=sys.stderr)
        return 1
    print(f"{result} kind={arguments.kind} scope={scope[:8]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = (
    "LocalFileStorage",
    "STORAGE_KINDS",
    "StorageKind",
    "create_exclusive",
    "durable_replace",
    "initialize_storage",
    "open_regular",
    "open_storage",
    "open_verified",
    "remove_regular",
    "sync_and_close",
    "write_all",
)
