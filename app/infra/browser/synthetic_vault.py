"""Explicit, private-console storage for this task's synthetic credentials.

No path is discovered from environment or process startup. The caller selects a
single code-owned file in the task's controlled Linux secret volume.
"""

from __future__ import annotations

import getpass
import json
import os
import stat
import sys
import warnings
from pathlib import Path
from typing import Any, Final, Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

TASK_ID = "P2-BROWSER-RUNTIME-V42-001"
VAULT_DIRECTORY = Path("/run/browser-synthetic-secrets") / TASK_ID
OPERATOR_FILE: Final[Literal["operator.bundle.enc"]] = "operator.bundle.enc"
BUSINESS_FILE: Final[Literal["business.token.enc"]] = "business.token.enc"
DEACTIVATION_FILE: Final[Literal["deactivation.bundle.enc"]] = "deactivation.bundle.enc"
_FILES = frozenset((OPERATOR_FILE, BUSINESS_FILE, DEACTIVATION_FILE))
_VERSION = "browser.synthetic.vault.v1"
_MAX_PLAINTEXT = 131072
_MAX_CIPHERTEXT = 262144


def _private_console() -> None:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError("browser_vault_private_console_required")


def _current_uid() -> int:
    if sys.platform != "linux":
        raise ValueError("browser_vault_linux_required")
    return os.geteuid()


def prompt_passphrase(*, confirm: bool = False) -> str:
    """Read only from the user's own terminal; never use argv, env or stdin pipe."""
    _private_console()
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            value = getpass.getpass("Synthetic task vault passphrase (hidden): ")
            if not 12 <= len(value) <= 4096:
                raise ValueError
            if confirm and value != getpass.getpass("Repeat vault passphrase (hidden): "):
                raise ValueError
            return value
        except Exception:
            raise ValueError("browser_vault_passphrase_invalid") from None


def _file_path(name: str) -> Path:
    if name not in _FILES:
        raise ValueError("browser_vault_path_invalid")
    return VAULT_DIRECTORY / name


def _sync_directory(path: Path) -> None:
    """Persist directory entries through a verified Linux directory descriptor."""
    uid = _current_uid()
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    cloexec_flag = getattr(os, "O_CLOEXEC", 0)
    if not all((directory_flag, nofollow_flag, cloexec_flag)):
        raise ValueError("browser_vault_directory_invalid")
    flags = os.O_RDONLY | directory_flag | nofollow_flag | cloexec_flag
    try:
        fd = os.open(path, flags)
    except OSError:
        raise ValueError("browser_vault_directory_invalid") from None
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid
                or stat.S_IMODE(metadata.st_mode) != 0o700):
            raise ValueError
        os.fsync(fd)
    except (OSError, ValueError):
        raise ValueError("browser_vault_directory_invalid") from None
    finally:
        os.close(fd)


def _check_directory(*, create: bool) -> None:
    if sys.platform != "linux":
        raise ValueError("browser_vault_linux_required")
    parent = VAULT_DIRECTORY.parent
    try:
        parent_stat = parent.lstat()
        if not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != _current_uid():
            raise ValueError
        if parent_stat.st_mode & 0o077:
            raise ValueError
        created = create and not VAULT_DIRECTORY.exists() and not VAULT_DIRECTORY.is_symlink()
        if created:
            VAULT_DIRECTORY.mkdir(mode=0o700)
        target = VAULT_DIRECTORY.lstat()
        if (not stat.S_ISDIR(target.st_mode) or target.st_uid != _current_uid()
                or stat.S_IMODE(target.st_mode) != 0o700):
            raise ValueError
        if created:
            _sync_directory(parent)
    except (OSError, ValueError):
        raise ValueError("browser_vault_directory_invalid") from None


def assert_vault_uninitialized() -> None:
    """Read-only path preflight before any bootstrap key is generated."""
    if sys.platform != "linux":
        raise ValueError("browser_vault_linux_required")
    parent = VAULT_DIRECTORY.parent
    try:
        parent_stat = parent.lstat()
        if (not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_uid != _current_uid()
                or parent_stat.st_mode & 0o077):
            raise ValueError
        if VAULT_DIRECTORY.exists() or VAULT_DIRECTORY.is_symlink():
            _check_directory(create=False)
            if any(VAULT_DIRECTORY.iterdir()):
                raise ValueError("browser_vault_existing_ciphertext")
    except ValueError as error:
        if str(error) == "browser_vault_existing_ciphertext":
            raise
        raise ValueError("browser_vault_directory_invalid") from None
    except OSError:
        raise ValueError("browser_vault_directory_invalid") from None


def _aad(name: str) -> bytes:
    return json.dumps(
        [_VERSION, TASK_ID, "browser_fixture_tenant", name], separators=(",", ":")
    ).encode("ascii")


def _derive(passphrase: str, salt: bytes) -> bytes:
    if not 12 <= len(passphrase) <= 4096 or len(salt) != 16:
        raise ValueError("browser_vault_passphrase_invalid")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def encrypt_document(name: str, document: dict[str, Any], passphrase: str) -> bytes:
    """Pure codec; runtime persistence additionally enforces the private path."""
    _file_path(name)
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > _MAX_PLAINTEXT:
        raise ValueError("browser_vault_document_invalid")
    salt, nonce = os.urandom(16), os.urandom(12)
    ciphertext = AESGCM(_derive(passphrase, salt)).encrypt(nonce, payload, _aad(name))
    return b"BSV1" + salt + nonce + ciphertext


def decrypt_document(name: str, ciphertext: bytes, passphrase: str) -> dict[str, Any]:
    _file_path(name)
    try:
        if (not isinstance(ciphertext, bytes) or not 48 <= len(ciphertext) <= _MAX_CIPHERTEXT
                or ciphertext[:4] != b"BSV1"):
            raise ValueError
        payload = AESGCM(_derive(passphrase, ciphertext[4:20])).decrypt(
            ciphertext[20:32], ciphertext[32:], _aad(name)
        )
        result = json.loads(payload)
        if type(result) is not dict:
            raise ValueError
        return result
    except (ValueError, InvalidTag, UnicodeError, TypeError):
        raise ValueError("browser_vault_unlock_failed") from None


def _checked_file(path: Path) -> None:
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != _current_uid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > _MAX_CIPHERTEXT):
        raise ValueError("browser_vault_file_invalid")


def read_encrypted(path: Path, *, expected_name: Literal[
    "operator.bundle.enc", "business.token.enc", "deactivation.bundle.enc"
]) -> dict[str, Any]:
    """Explicit path only. Opening a symlink or an unexpected filename fails."""
    _private_console()
    if path != _file_path(expected_name):
        raise ValueError("browser_vault_path_invalid")
    _check_directory(create=False)
    try:
        _checked_file(path)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != _current_uid()
                    or opened.st_nlink != 1
                    or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_size > _MAX_CIPHERTEXT):
                raise ValueError
            contents = stream.read(_MAX_CIPHERTEXT + 1)
        return decrypt_document(expected_name, contents, prompt_passphrase())
    except (OSError, ValueError):
        raise ValueError("browser_vault_unlock_failed") from None


def write_encrypted_files(documents: dict[str, dict[str, Any]]) -> None:
    """Create only new ciphertext, never replace a previous vault or host ACL."""
    _private_console()
    if set(documents) != _FILES:
        raise ValueError("browser_vault_document_invalid")
    _check_directory(create=True)
    if any(VAULT_DIRECTORY.iterdir()):
        raise ValueError("browser_vault_existing_ciphertext")
    for name in _FILES:
        if _file_path(name).exists() or _file_path(name).is_symlink():
            raise ValueError("browser_vault_existing_ciphertext")
    passphrase = prompt_passphrase(confirm=True)
    encrypted = {name: encrypt_document(name, documents[name], passphrase) for name in _FILES}
    for name in (OPERATOR_FILE, BUSINESS_FILE, DEACTIVATION_FILE):
        path = _file_path(name)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encrypted[name])
            stream.flush()
            os.fsync(stream.fileno())
        _checked_file(path)
    _sync_directory(VAULT_DIRECTORY)
