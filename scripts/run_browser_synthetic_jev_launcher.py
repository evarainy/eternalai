"""Owner-terminal launcher for one fixed synthetic Jev input recipient.

Standard library only; importing this module does no IO. The exact authorized
host fields are projected in memory and sent once through an anonymous stdin pipe.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, NoReturn

_WORKTREE = Path("E:/code/eternalai/.worktrees/browser-runtime-v42")
_SOURCE = Path("E:/code/eternalai/.env")
_EMPTY_ENV = Path(
    "C:/Users/Administrator/AppData/Local/Temp/browser-v42-20261002/"
    "publication-fixed-classification-20261004/empty.env"
)
_SECCOMP = Path(
    "C:/Users/Administrator/AppData/Local/Temp/browser-v42-20261002/"
    "sandbox-probe-approved/chromium-seccomp-docker-daa0cb7.json"
)
_SECCOMP_SHA256 = "e67623828ce94bb9f4917d029b1c5b83191f18ecd1c3538da63956b773dd33c5"
_IMAGE = "eternalai-browser-v42:local"
_ORIGINAL_TRIAL = "single-run"
_DIAGNOSTIC_TRIAL = "diagnostic-2"
_REFRESH_SCRIPT = Path(
    "C:/Users/Administrator/AppData/Local/Temp/browser-v42-20261002/"
    "identity-refresh-diagnostic-2-20261004/refresh_existing_tokens.py"
)
_REFRESH_TARGET = "/opt/browser-diagnostic-refresh.py"
_FRAME_VERSION = "browser.synthetic.private-input.v1"
_MAX_FRAME = 65536
_MAX_SOURCE = 1048576
_MAX_LINE = 32768
_RECIPIENTS = {
    "prepare": ("browser-publication", "browser-v42-single-prepare"),
    "activate": ("browser-publication", "browser-v42-single-activate"),
    "api": ("browser-synthetic-api", "browser-v42-single-api"),
    "once": ("browser-single-run", "browser-v42-single-once"),
    "inspect": ("browser-client", "browser-v42-single-client-inspect-auto"),
    "submit": ("browser-client", "browser-v42-single-client-submit-auto"),
    "deactivate": ("browser-publication", "browser-v42-single-deactivate-auto"),
    "refresh": ("browser-publication", "browser-v42-single-refresh"),
}


class _SilentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError("browser_jev_launcher_arguments_invalid")


def _recipient(operation: str, trial_id: str) -> tuple[str, str]:
    if (trial_id not in {_ORIGINAL_TRIAL, _DIAGNOSTIC_TRIAL} or operation not in _RECIPIENTS
            or (operation == "refresh" and trial_id != _DIAGNOSTIC_TRIAL)):
        raise ValueError("browser_jev_launcher_arguments_invalid")
    service, name = _RECIPIENTS[operation]
    return service, (name if trial_id == _ORIGINAL_TRIAL
                     else name.replace("-single-", "-diagnostic-2-", 1))


def _check_refresh_script(expected_sha256: str | None) -> None:
    if (type(expected_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or not _regular_file(_REFRESH_SCRIPT.lstat(), maximum=65536)
            or hashlib.sha256(_REFRESH_SCRIPT.read_bytes()).hexdigest() != expected_sha256):
        raise ValueError("browser_jev_launcher_preflight_failed")


def _require_future_deadline(value: str) -> None:
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is None:
            raise ValueError
        deadline = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if deadline <= datetime.now(timezone.utc):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("browser_jev_launcher_deadline_invalid") from None


def _regular_file(metadata: os.stat_result, *, maximum: int) -> bool:
    return (stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1
            and not getattr(metadata, "st_file_attributes", 0) & 0x400
            and metadata.st_size <= maximum)


def _project_field(stream: BinaryIO, field: bytes = b"jev-key") -> str:
    """Discard every nonselected value as bytes, without decoding or interpolation."""
    if field not in {b"jev-key", b"jev-passport"}:
        raise ValueError
    prefix = bytearray()
    selected = bytearray()
    value: bytes | None = None
    is_value = False
    is_selected = False
    line_size = 0
    total = 0

    def finish_line() -> None:
        nonlocal value
        if is_selected:
            if value is not None:
                raise ValueError
            value = bytes(selected).removesuffix(b"\r").strip(b" \t")

    # A UTF-8 BOM is accepted only at the start. No whole-file decoding occurs.
    start = stream.read(3)
    total += len(start)
    initial = b"" if start == b"\xef\xbb\xbf" else start
    while True:
        if initial:
            byte, initial = initial[:1], initial[1:]
        else:
            byte = stream.read(1)
            total += len(byte)
        if not byte:
            finish_line()
            break
        if total > _MAX_SOURCE:
            raise ValueError
        line_size += 1
        if line_size > _MAX_LINE:
            raise ValueError
        if byte == b"\n":
            finish_line()
            prefix.clear()
            selected.clear()
            is_value = is_selected = False
            line_size = 0
        elif not is_value:
            if byte == b"=":
                is_value = True
                is_selected = bytes(prefix).strip(b" \t") == field
                prefix.clear()
            elif len(prefix) < 256:
                prefix.extend(byte)
            else:
                # An oversized field name can never select the exact field.
                is_value = True
                prefix.clear()
        elif is_selected:
            selected.extend(byte)
            if len(selected) > (4100 if field == b"jev-key" else 16400):
                raise ValueError
    if value is None or not value:
        raise ValueError
    if value[:1] in {b"'", b'"'}:
        if len(value) < 2 or value[-1:] != value[:1]:
            raise ValueError
        value = value[1:-1]
    if field == b"jev-key":
        if not 1 <= len(value) <= 4096 or any(byte < 33 or byte > 126 for byte in value):
            raise ValueError
        return value.decode("ascii")
    phrase = value.decode("utf-8")
    if not 12 <= len(phrase) <= 4096:
        raise ValueError
    return phrase


def read_exact_jev_key() -> str:
    return _read_exact_field(b"jev-key")


def _read_exact_field(field: bytes) -> str:
    """Open only the fixed approved source; reject links and malformed selection."""
    try:
        before = _SOURCE.lstat()
        if not _regular_file(before, maximum=_MAX_SOURCE):
            raise ValueError
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(_SOURCE, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not _regular_file(opened, maximum=_MAX_SOURCE)
                    or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
                raise ValueError
            return _project_field(stream, field)
    except (Exception, KeyboardInterrupt):
        raise ValueError("browser_jev_exact_field_invalid") from None


def _prompt_phrase() -> str:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ValueError("browser_jev_owner_terminal_required")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            phrase = getpass.getpass("Same synthetic task vault passphrase (hidden): ")
        if not 12 <= len(phrase) <= 4096:
            raise ValueError
        phrase.encode("utf-8")
        return phrase
    except (Exception, KeyboardInterrupt):
        raise ValueError("browser_jev_passphrase_invalid") from None


def _docker_environment() -> dict[str, str]:
    # Keep only nonsecret Windows CLI support paths; no source fields or phrase.
    # Docker CLI discovers its Windows Compose plugin under these system paths.
    names = ("PATH", "SystemRoot", "WINDIR", "ProgramFiles", "ProgramData",
             "COMSPEC", "TEMP", "TMP",
             "USERPROFILE", "APPDATA", "LOCALAPPDATA")
    result = {name: os.environ[name] for name in names if name in os.environ}
    result["BROWSER_V42_SECCOMP_PROFILE"] = str(_SECCOMP)
    return result


def _compose() -> list[str]:
    return [
        "docker", "compose", "--env-file", str(_EMPTY_ENV),
        "--project-name", "browser-v42-single-run",
        "--file", "infra/docker/browser-runtime/compose.yaml",
        "--file", "infra/docker/browser-runtime/compose.single-run.yaml",
        "--profile", "operator-browser-runtime",
    ]


def _metadata(arguments: list[str], environment: dict[str, str]) -> str:
    result = subprocess.run(arguments, cwd=_WORKTREE, env=environment, check=False,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
    if result.returncode != 0 or len(result.stdout) > 1024:
        raise ValueError("browser_jev_launcher_preflight_failed")
    return result.stdout.decode("ascii").strip()


def _check_image(expected_image_id: str, environment: dict[str, str]) -> None:
    image = _metadata(["docker", "image", "inspect", "--format",
                       "{{.Id}}|{{.Config.User}}", _IMAGE], environment)
    if image != expected_image_id + "|pwuser":
        raise ValueError("browser_jev_launcher_preflight_failed")


def _preflight(operation: str, expected_image_id: str, environment: dict[str, str],
               trial_id: str = _ORIGINAL_TRIAL, refresh_script_sha256: str | None = None) -> None:
    if (Path.cwd().resolve() != _WORKTREE.resolve(strict=True)
            or Path(__file__).resolve().parent.parent != _WORKTREE.resolve(strict=True)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", expected_image_id) is None):
        raise ValueError("browser_jev_launcher_preflight_failed")
    if (not _regular_file(_EMPTY_ENV.lstat(), maximum=0)
            or not _regular_file(_SECCOMP.lstat(), maximum=1048576)
            or hashlib.sha256(_SECCOMP.read_bytes()).hexdigest() != _SECCOMP_SHA256):
        raise ValueError("browser_jev_launcher_preflight_failed")
    _check_image(expected_image_id, environment)
    _, name = _recipient(operation, trial_id)
    if operation == "refresh":
        _check_refresh_script(refresh_script_sha256)
    present = _metadata(["docker", "container", "ls", "--all", "--filter",
                         "name=^/" + name + "$", "--format", "{{.Names}}"], environment)
    if present:
        raise ValueError("browser_jev_launcher_preflight_failed")
    result = subprocess.run(_compose() + ["config", "--quiet"], cwd=_WORKTREE,
                            env=environment, check=False, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=15)
    if result.returncode != 0:
        raise ValueError("browser_jev_launcher_preflight_failed")


def launch(operation: str, *, approved_deadline_utc: str, expected_image_id: str,
           phrase_from_exact_field: bool = False, trial_id: str = _ORIGINAL_TRIAL,
           refresh_script_sha256: str | None = None) -> int:
    """All preflights precede secret input; dispatch exactly once to a fixed service."""
    _require_future_deadline(approved_deadline_utc)
    service, name = _recipient(operation, trial_id)
    if ((operation == "refresh" and refresh_script_sha256 is None)
            or (operation != "refresh" and refresh_script_sha256 is not None)):
        raise ValueError("browser_jev_launcher_arguments_invalid")
    if not phrase_from_exact_field and (not sys.stdin.isatty() or not sys.stderr.isatty()):
        raise ValueError("browser_jev_owner_terminal_required")
    environment = _docker_environment()
    # Public immutable image identity pins Compose's actual recipient. The
    # preflight validates its exact SHA-256 syntax before any Docker command.
    environment["BROWSER_V42_SINGLE_RUN_IMAGE"] = expected_image_id
    if trial_id == _ORIGINAL_TRIAL:
        _preflight(operation, expected_image_id, environment)
    else:
        _preflight(operation, expected_image_id, environment, trial_id, refresh_script_sha256)
    _require_future_deadline(approved_deadline_utc)
    key = (
        read_exact_jev_key()
        if operation not in {"submit", "inspect", "deactivate", "refresh"}
        else None
    )
    phrase = (_read_exact_field(b"jev-passport") if phrase_from_exact_field else _prompt_phrase())
    document = {"version": _FRAME_VERSION, "vault_passphrase": phrase}
    if key is not None:
        document["jev_key"] = key
    frame = json.dumps(document, separators=(",", ":")).encode("ascii")
    if len(frame) > _MAX_FRAME:
        raise ValueError("browser_jev_launcher_input_invalid")
    arguments = _compose() + ["run", "--no-deps", "--pull", "never", "-T", "--name", name]
    if operation == "api":
        arguments.append("--use-aliases")
    if operation == "refresh":
        # Reuse the existing internal-network publication service. Only this
        # exact reviewed one-off script is mounted; no arbitrary code loader.
        arguments += ["--entrypoint", "python", "--volume",
                      str(_REFRESH_SCRIPT) + ":" + _REFRESH_TARGET + ":ro", "--volume",
                      "browser-v42-single-run-secrets:/run/browser-synthetic-secrets:rw",
                      service, _REFRESH_TARGET, "--enable", "--private-stdin"]
    else:
        arguments += [service, "--enable", "--private-stdin"]
    if trial_id == _DIAGNOSTIC_TRIAL:
        arguments += ["--trial-id", trial_id]
    if operation in {"prepare", "activate"}:
        arguments += ["--operation", operation, "--input-mode", "structured"]
    elif operation == "api":
        arguments += ["--input-mode", "structured"]
    elif operation == "once":
        arguments += ["--approved-budget-usd", "0.01"]
    elif operation != "refresh":
        arguments += ["--operation", operation]
    # Recheck after hidden input, before starting any container. stdout/stderr
    # remain the owner's private terminal; communicate closes stdin with EOF.
    _require_future_deadline(approved_deadline_utc)
    _check_image(expected_image_id, environment)
    if operation == "refresh":
        _check_refresh_script(refresh_script_sha256)
    with subprocess.Popen(arguments, cwd=_WORKTREE, env=environment,
                          stdin=subprocess.PIPE, stdout=None, stderr=None) as process:
        process.communicate(input=frame)
        return process.returncode


def main(argv: list[str] | None = None) -> int:
    parser = _SilentParser(allow_abbrev=False, add_help=False)
    parser.add_argument("--operation", choices=tuple(_RECIPIENTS), required=True)
    parser.add_argument("--approved-deadline-utc", required=True)
    parser.add_argument("--expected-image-id", required=True)
    parser.add_argument("--phrase-from-exact-field", action="store_true")
    parser.add_argument(
        "--trial-id", choices=(_ORIGINAL_TRIAL, _DIAGNOSTIC_TRIAL), default=_ORIGINAL_TRIAL
    )
    parser.add_argument("--refresh-script-sha256")
    try:
        args = parser.parse_args(argv)
        return launch(args.operation, approved_deadline_utc=args.approved_deadline_utc,
                      expected_image_id=args.expected_image_id,
                      phrase_from_exact_field=args.phrase_from_exact_field, trial_id=args.trial_id,
                      refresh_script_sha256=args.refresh_script_sha256)
    except (Exception, KeyboardInterrupt) as error:
        codes = {
            "browser_jev_launcher_arguments_invalid", "browser_jev_launcher_deadline_invalid",
            "browser_jev_owner_terminal_required", "browser_jev_launcher_preflight_failed",
            "browser_jev_exact_field_invalid", "browser_jev_passphrase_invalid",
            "browser_jev_launcher_input_invalid",
        }
        code = (error.args[0] if type(error) is ValueError and len(error.args) == 1
                and type(error.args[0]) is str and error.args[0] in codes
                else "browser_jev_launcher_unavailable")
        print(code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
