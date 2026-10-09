"""Read six mounted secrets, then forward the explicit API or worker role."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

_SECRET_FILES = {
    "DATABASE_URL": "database-url",
    "REDIS_URL": "redis-url",
    "ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64": "credential-encryption-key-b64",
    "ETERNALAI_IDENTITY_HMAC_KEY_B64": "identity-hmac-key-b64",
    "ETERNALAI_SESSION_SIGNING_KEY_B64": "session-signing-key-b64",
    "ETERNALAI_SESSION_BINDING_KEY_B64": "session-binding-key-b64",
}
_SECRET_ROOT = Path("/run/secrets")
_MAX_SECRET_BYTES = 8192


def _read_secret(path: Path) -> str:
    if path.is_symlink():
        raise ValueError
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode)
                or not 1 <= metadata.st_size <= _MAX_SECRET_BYTES):
            raise ValueError
        raw = os.read(descriptor, _MAX_SECRET_BYTES + 1)
        if len(raw) != metadata.st_size:
            raise ValueError
    finally:
        os.close(descriptor)
    value = raw.decode("utf-8")
    if value.endswith("\n"):
        value = value[:-1]
    if not value or any(character in value for character in "\r\n\x00"):
        raise ValueError
    return value


def main(argv: list[str] | None = None) -> int:
    try:
        if any(key in os.environ for key in _SECRET_FILES):
            raise ValueError
        values = {
            key: _read_secret(_SECRET_ROOT / name)
            for key, name in _SECRET_FILES.items()
        }
        os.environ.update(values)
        try:
            from scripts.run_browser_registered_server import main as serve

            return serve(argv)
        finally:
            for key in _SECRET_FILES:
                os.environ.pop(key, None)
    except BaseException:
        # Secret values, file paths and exception messages never reach stderr.
        print("browser_production_unavailable", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
