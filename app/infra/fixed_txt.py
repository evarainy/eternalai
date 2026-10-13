"""Bounded UTF-8 reader for one server-configured TXT file."""

from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import sys
from pathlib import Path

from app.ports.fixed_document import FixedDocumentReadError, FixedDocumentSnapshot


def _open_nonblocking(path: str, flags: int) -> int:
    # Prevent a regular-file-to-FIFO race from blocking before fstat on POSIX.
    return os.open(path, flags | getattr(os, "O_NONBLOCK", 0))


class FixedTxtReader:
    """Read lazily; the byte cap is a context budget, not a token count.

    Ordinary symlinks are resolved at read time and must end at a regular TXT
    file. This is a trusted server path, not a sandbox for user-selected paths.
    Windows device namespaces and alternate data streams are not supported.
    """

    def __init__(self, path: str, max_bytes: int = 32768) -> None:
        self._path = path
        self._max_bytes = max_bytes

    async def read(self) -> FixedDocumentSnapshot:
        return await asyncio.to_thread(self._read_sync)

    def _read_sync(self) -> FixedDocumentSnapshot:
        if (
            not isinstance(self._path, str)
            or not self._path
            or "\x00" in self._path
            or type(self._max_bytes) is not int
            or self._max_bytes <= 0
            or self._max_bytes >= sys.maxsize
        ):
            raise FixedDocumentReadError("document_configuration_invalid")
        path = Path(self._path)
        if (
            not path.is_absolute()
            or path.suffix.lower() != ".txt"
            or path.is_reserved()
            or self._path.replace("\\", "/").startswith(("//./", "//?/"))
            or (os.name == "nt" and ":" in self._path[len(path.drive):])
        ):
            raise FixedDocumentReadError("document_configuration_invalid")
        try:
            resolved = path.resolve(strict=True)
            if resolved.suffix.lower() != ".txt" or not stat.S_ISREG(resolved.stat().st_mode):
                raise FixedDocumentReadError("document_unavailable")
            with open(resolved, "rb", opener=_open_nonblocking) as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise FixedDocumentReadError("document_unavailable")
                raw = stream.read(self._max_bytes + 1)
        except (OSError, ValueError, RuntimeError):
            raise FixedDocumentReadError("document_unavailable") from None
        if len(raw) > self._max_bytes:
            raise FixedDocumentReadError("document_too_large")
        try:
            text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            raise FixedDocumentReadError("document_encoding_invalid") from None
        if not text.strip():
            raise FixedDocumentReadError("document_empty")
        return FixedDocumentSnapshot(
            text=text,
            version=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
