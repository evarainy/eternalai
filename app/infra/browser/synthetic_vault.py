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
from pydantic import SecretStr

TASK_ID = "P2-BROWSER-RUNTIME-V42-001"
ORIGINAL_TRIAL = "single-run"
DIAGNOSTIC_TRIAL = "diagnostic-2"
VISIBLE_TRIAL = "visible-complete"
OBSERVE_TRIAL = "observe-only"
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


def approved_trial_id(trial_id: str) -> str:
    if type(trial_id) is not str or trial_id not in {
        ORIGINAL_TRIAL, DIAGNOSTIC_TRIAL, VISIBLE_TRIAL, OBSERVE_TRIAL,
    }:
        raise ValueError("browser_trial_id_invalid")
    return trial_id


def trial_directory(trial_id: str = ORIGINAL_TRIAL) -> Path:
    trial_id = approved_trial_id(trial_id)
    return VAULT_DIRECTORY if trial_id == ORIGINAL_TRIAL else VAULT_DIRECTORY / trial_id


def _check_trial_directory(trial_id: str = ORIGINAL_TRIAL) -> None:
    """Each approved child requires its own committed renewal success receipt."""
    _check_directory(create=False)
    if approved_trial_id(trial_id) == ORIGINAL_TRIAL:
        return
    directory = trial_directory(trial_id)
    try:
        for blocked in (VAULT_DIRECTORY / f"{trial_id}.refresh-failure.json",
                         VAULT_DIRECTORY / f"{trial_id}.refresh-stage"):
            try:
                blocked.lstat()
            except FileNotFoundError:
                continue
            raise ValueError
        metadata = directory.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != _current_uid()
                or stat.S_IMODE(metadata.st_mode) != 0o700):
            raise ValueError
        # The fixed success receipt is published with the three ciphertexts.
        receipt = directory / "refresh-result.json"
        _checked_file(receipt)
        with os.fdopen(
            os.open(receipt, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC), "rb"
        ) as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != _current_uid()
                    or opened.st_nlink != 1 or stat.S_IMODE(opened.st_mode) != 0o600
                    or opened.st_size > 4096):
                raise ValueError
            result = json.loads(stream.read(4097))
        if (
            type(result) is not dict
            or result.get("status") != "success"
            or result.get("task_id") != TASK_ID
            or result.get("trial_id") != trial_id
            or result.get("ttl_seconds") != 3600
            or result.get("identities_refreshed") != 3
            or result.get("runtime_key_material_changed") is not False
            or result.get("db_write_count") != 0
            or result.get("burn_once_records_reset") is not False
        ):
            raise ValueError
    except (OSError, ValueError, TypeError):
        raise ValueError("browser_vault_directory_invalid") from None


def _file_path(name: str, *, trial_id: str = ORIGINAL_TRIAL) -> Path:
    if name not in _FILES:
        raise ValueError("browser_vault_path_invalid")
    return trial_directory(trial_id) / name


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


def _aad(name: str, *, trial_id: str = ORIGINAL_TRIAL) -> bytes:
    approved_trial_id(trial_id)
    identity = [_VERSION, TASK_ID, "browser_fixture_tenant", name]
    if trial_id != ORIGINAL_TRIAL:
        identity.append(trial_id)
    return json.dumps(
        identity, separators=(",", ":")
    ).encode("ascii")


def _derive(passphrase: str, salt: bytes) -> bytes:
    if not 12 <= len(passphrase) <= 4096 or len(salt) != 16:
        raise ValueError("browser_vault_passphrase_invalid")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def encrypt_document(name: str, document: dict[str, Any], passphrase: str, *,
                     trial_id: str = ORIGINAL_TRIAL) -> bytes:
    """Pure codec; runtime persistence additionally enforces the private path."""
    _file_path(name, trial_id=trial_id)
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > _MAX_PLAINTEXT:
        raise ValueError("browser_vault_document_invalid")
    salt, nonce = os.urandom(16), os.urandom(12)
    ciphertext = AESGCM(_derive(passphrase, salt)).encrypt(
        nonce, payload, _aad(name, trial_id=trial_id)
    )
    return b"BSV1" + salt + nonce + ciphertext


def decrypt_document(name: str, ciphertext: bytes, passphrase: str, *,
                     trial_id: str = ORIGINAL_TRIAL) -> dict[str, Any]:
    _file_path(name, trial_id=trial_id)
    try:
        if (not isinstance(ciphertext, bytes) or not 48 <= len(ciphertext) <= _MAX_CIPHERTEXT
                or ciphertext[:4] != b"BSV1"):
            raise ValueError
        payload = AESGCM(_derive(passphrase, ciphertext[4:20])).decrypt(
            ciphertext[20:32], ciphertext[32:], _aad(name, trial_id=trial_id)
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


def _read_checked_ciphertext(path: Path, *, expected_name: Literal[
    "operator.bundle.enc", "business.token.enc", "deactivation.bundle.enc"
], trial_id: str = ORIGINAL_TRIAL) -> bytes:
    """Shared file checks; the input transport cannot relax vault metadata."""
    if path != _file_path(expected_name, trial_id=trial_id):
        raise ValueError("browser_vault_path_invalid")
    _check_trial_directory(trial_id)
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
        return contents
    except (OSError, ValueError):
        raise ValueError("browser_vault_unlock_failed") from None


def read_encrypted(path: Path, *, expected_name: Literal[
    "operator.bundle.enc", "business.token.enc", "deactivation.bundle.enc"
]) -> dict[str, Any]:
    """Explicit path only. Opening a symlink or an unexpected filename fails."""
    _private_console()
    contents = _read_checked_ciphertext(path, expected_name=expected_name)
    try:
        return decrypt_document(expected_name, contents, prompt_passphrase())
    except (OSError, ValueError):
        raise ValueError("browser_vault_unlock_failed") from None


def read_private_operator_document(passphrase: SecretStr, *,
                                   trial_id: str = ORIGINAL_TRIAL) -> dict[str, Any]:
    """Only the explicit private-stdin operator path; no caller-selected file."""
    return _read_private_document(OPERATOR_FILE, passphrase, trial_id=trial_id)


def read_private_business_document(passphrase: SecretStr, *,
                                   trial_id: str = ORIGINAL_TRIAL) -> dict[str, Any]:
    return _read_private_document(BUSINESS_FILE, passphrase, trial_id=trial_id)


def read_private_deactivation_document(passphrase: SecretStr, *,
                                       trial_id: str = ORIGINAL_TRIAL) -> dict[str, Any]:
    return _read_private_document(DEACTIVATION_FILE, passphrase, trial_id=trial_id)


def _read_private_document(name: Literal[
    "operator.bundle.enc", "business.token.enc", "deactivation.bundle.enc"
], passphrase: SecretStr, *, trial_id: str = ORIGINAL_TRIAL) -> dict[str, Any]:
    contents = _read_checked_ciphertext(_file_path(name, trial_id=trial_id),
                                       expected_name=name, trial_id=trial_id)
    try:
        if not isinstance(passphrase, SecretStr):
            raise ValueError
        return decrypt_document(name, contents, passphrase.get_secret_value(), trial_id=trial_id)
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
