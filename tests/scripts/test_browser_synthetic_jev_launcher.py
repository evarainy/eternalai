"""Exact host field projection and dispatch using generated, synthetic input only."""

from __future__ import annotations

import io
import json
import secrets
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import run_browser_synthetic_jev_launcher as entry


def _future() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _key() -> str:
    return secrets.token_urlsafe(32)


@pytest.mark.parametrize("operation", ["refresh", "submit", "inspect", "deactivate"])
@pytest.mark.parametrize("trial_id", ["diagnostic-2", "visible-complete"])
def test_diagnostic_recipients_are_fixed_and_phrase_only(
    operation: str, monkeypatch: pytest.MonkeyPatch, trial_id: str,
) -> None:
    phrase = secrets.token_urlsafe(32)
    captured: dict = {}
    monkeypatch.setattr(entry, "_preflight", Mock())
    monkeypatch.setattr(entry, "_check_image", Mock())
    monkeypatch.setattr(entry, "_check_refresh_script", Mock())
    reader = Mock(return_value=phrase)
    key = Mock(side_effect=AssertionError("provider key read"))
    monkeypatch.setattr(entry, "_read_exact_field", reader)
    monkeypatch.setattr(entry, "read_exact_jev_key", key)

    class Process:
        returncode = 0
        def __init__(self, arguments, **kwargs):
            captured["arguments"] = arguments
            captured["kwargs"] = kwargs
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def communicate(self, *, input):
            captured["frame"] = input

    monkeypatch.setattr(entry.subprocess, "Popen", Process)
    options = {"refresh_script_sha256": "c" * 64} if operation == "refresh" else {}
    assert (
        entry.launch(
            operation,
            approved_deadline_utc=_future(),
            expected_image_id="sha256:" + "a" * 64,
            phrase_from_exact_field=True,
            trial_id=trial_id,
            **options,
        )
        == 0
    )
    reader.assert_called_once_with(b"jev-passport")
    key.assert_not_called()
    arguments = captured["arguments"]
    assert (
        arguments[arguments.index("--name") + 1] == entry._recipient(operation, trial_id)[1]
    )
    assert arguments[arguments.index("--trial-id") + 1] == trial_id
    assert json.loads(captured["frame"]) == {
        "version": entry._FRAME_VERSION,
        "vault_passphrase": phrase,
    }
    assert phrase not in repr(arguments) and phrase not in repr(captured["kwargs"])
    if operation == "refresh":
        assert str(entry._REFRESH_SCRIPT) + ":" + entry._REFRESH_TARGET + ":ro" in arguments
        assert arguments[arguments.index("--entrypoint") + 1] == "python"


def test_unknown_trial_is_rejected_before_secret_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = Mock()
    monkeypatch.setattr(entry, "_read_exact_field", reader)
    with pytest.raises(ValueError, match="^browser_jev_launcher_arguments_invalid$"):
        entry.launch(
            "submit",
            approved_deadline_utc=_future(),
            expected_image_id="sha256:" + "a" * 64,
            phrase_from_exact_field=True,
            trial_id="diagnostic-3",
        )
    reader.assert_not_called()


@pytest.mark.parametrize("quote", [b"", b"'", b'"'])
@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"])
def test_projects_exact_field_without_interpreting_decoys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quote: bytes, bom: bytes,
) -> None:
    key, decoy = _key(), _key()
    source = tmp_path / "synthetic.input"
    source.write_bytes(bom + b"JEV-KEY=" + decoy.encode() + b"\r\n"
                       + b"other=\xff\xfe$(anything)`anything`\r\n"
                       + b" jev-key \t= " + quote + key.encode() + quote + b" \t\r\n"
                       + b"jev_key=" + decoy.encode() + b"\r\n")
    monkeypatch.setattr(entry, "_SOURCE", source)
    assert entry.read_exact_jev_key() == key


@pytest.mark.parametrize("mutation", [
    "missing", "empty", "duplicate", "quote", "multiline", "control", "nonascii",
    "oversize_key", "oversize_line", "oversize_file", "leading_cr", "double_cr",
])
def test_invalid_selection_is_closed_and_never_renders_input(
    mutation: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = _key().encode()
    contents = {
        "missing": b"JEV-KEY=" + secret,
        "empty": b"jev-key=",
        "duplicate": b"jev-key=" + secret + b"\njev-key=" + secret,
        "quote": b"jev-key='" + secret + b'"',
        "multiline": b"jev-key='" + secret + b"\ncontinued'",
        "control": b"jev-key=" + secret + b"\x00",
        "nonascii": b"jev-key=" + secret + b"\xff",
        "oversize_key": b"jev-key=" + b"x" * 4097,
        "oversize_line": b"ignored=" + b"x" * entry._MAX_LINE,
        "oversize_file": b"x" * (entry._MAX_SOURCE + 1),
        "leading_cr": b"jev-key=\r" + secret,
        "double_cr": b"jev-key=" + secret + b"\r\r\n",
    }[mutation]
    source = tmp_path / "synthetic.input"
    source.write_bytes(contents)
    monkeypatch.setattr(entry, "_SOURCE", source)
    with pytest.raises(ValueError) as caught:
        entry.read_exact_jev_key()
    assert caught.value.args == ("browser_jev_exact_field_invalid",)
    assert secret.decode() not in repr(caught.value)
    assert capsys.readouterr() == ("", "")


def test_field_projection_never_uses_unbounded_read() -> None:
    key = _key()

    class Bounded(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            assert size in {1, 3}
            return super().read(size)

    assert entry._project_field(Bounded(b"decoy=\xff\njev-key=" + key.encode())) == key


def test_source_reparse_point_is_rejected_before_open(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Mock()
    source.lstat.return_value = SimpleNamespace(
        st_mode=0o100600, st_nlink=1, st_file_attributes=0x400, st_size=64,
    )
    opened = Mock(side_effect=AssertionError("opening forbidden"))
    monkeypatch.setattr(entry, "_SOURCE", source)
    monkeypatch.setattr(entry.os, "open", opened)
    with pytest.raises(ValueError, match="^browser_jev_exact_field_invalid$"):
        entry.read_exact_jev_key()
    opened.assert_not_called()


@pytest.mark.parametrize("deadline", [
    "2026-10-04T04:48:46Z", "2000-01-01T00:00:00Z", "2030-02-30T00:00:00Z",
    "2099-01-01T00:00:00", "2099-01-01T00:00:00+01:00", "", "invalid",
])
def test_invalid_deadline_precedes_any_input_or_subprocess(
    deadline: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    sentinels = [Mock(side_effect=AssertionError("must not run")) for _ in range(4)]
    for name, mock in zip(("read_exact_jev_key", "_prompt_phrase", "_preflight"), sentinels):
        monkeypatch.setattr(entry, name, mock)
    monkeypatch.setattr(entry.subprocess, "run", sentinels[3])
    result = entry.main(["--operation", "once", "--approved-deadline-utc", deadline,
                         "--expected-image-id", "sha256:" + "a" * 64])
    assert result == 2
    assert capsys.readouterr() == ("", "browser_jev_launcher_deadline_invalid\n")
    for mock in sentinels:
        mock.assert_not_called()


@pytest.mark.parametrize("operation,service,name", [
    ("prepare", "browser-publication", "browser-v42-single-prepare"),
    ("activate", "browser-publication", "browser-v42-single-activate"),
    ("api", "browser-synthetic-api", "browser-v42-single-api"),
    ("once", "browser-single-run", "browser-v42-single-once"),
])
def test_one_fixed_recipient_receives_secrets_only_on_anonymous_stdin(
    operation: str, service: str, name: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key, phrase = _key(), secrets.token_urlsafe(48)
    calls: list[tuple[list[str], dict]] = []
    frames: list[bytes] = []
    events: list[str] = []
    monkeypatch.setattr(entry.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(entry.sys, "stderr", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(entry, "_preflight", lambda *_: events.append("preflight"))
    monkeypatch.setattr(entry, "_check_image", lambda *_: events.append("image_recheck"))
    monkeypatch.setattr(entry, "read_exact_jev_key", lambda: (events.append("key") or key))
    monkeypatch.setattr(entry, "_prompt_phrase", lambda: (events.append("phrase") or phrase))
    monkeypatch.setenv("JEV_KEY", key)
    monkeypatch.setenv("UNRELATED_SECRET", phrase)

    class Process:
        returncode = 0

        def __init__(self, arguments: list[str], **kwargs: object) -> None:
            calls.append((arguments, kwargs))

        def __enter__(self) -> Process:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def communicate(self, *, input: bytes) -> None:
            frames.append(input)

    monkeypatch.setattr(entry.subprocess, "Popen", Process)
    assert entry.launch(operation, approved_deadline_utc=_future(),
                        expected_image_id="sha256:" + "a" * 64) == 0
    assert events == ["preflight", "key", "phrase", "image_recheck"]
    assert len(calls) == len(frames) == 1
    arguments, kwargs = calls[0]
    assert arguments[:len(entry._compose())] == entry._compose()
    assert arguments[len(entry._compose()):arguments.index(service)] == [
        "run", "--no-deps", "--pull", "never", "-T", "--name", name,
    ] + (["--use-aliases"] if operation == "api" else [])
    assert arguments[arguments.index(service) + 1:][:2] == ["--enable", "--private-stdin"]
    if operation == "once":
        assert arguments[-2:] == ["--approved-budget-usd", "0.01"]
    assert "--rm" not in arguments
    assert key not in repr(arguments) and phrase not in repr(arguments)
    assert key not in repr(kwargs) and phrase not in repr(kwargs)
    assert kwargs["stdin"] is subprocess.PIPE
    assert kwargs["stdout"] is None and kwargs["stderr"] is None
    assert "JEV_KEY" not in kwargs["env"] and "UNRELATED_SECRET" not in kwargs["env"]
    assert kwargs["env"]["BROWSER_V42_SINGLE_RUN_IMAGE"] == "sha256:" + "a" * 64
    assert json.loads(frames[0]) == {
        "version": entry._FRAME_VERSION, "vault_passphrase": phrase, "jev_key": key,
    }
    assert capsys.readouterr().out == ""


def test_existing_compose_pins_shared_anchor_and_api_to_same_public_image_id() -> None:
    worktree = Path(entry.__file__).resolve().parent.parent
    model = (worktree / "infra/docker/browser-runtime/compose.single-run.yaml").read_text()
    image = "image: ${BROWSER_V42_SINGLE_RUN_IMAGE:-eternalai-browser-v42:local}"
    assert "x-browser-single-run-image: &browser-single-run-image\n  " + image in model
    assert "services:\n  browser-synthetic-api:\n    " + image in model
    assert model.count(image) == 2


def test_deadline_rechecked_after_prompt_prevents_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Mock(side_effect=[None, None, ValueError("browser_jev_launcher_deadline_invalid")])
    process = Mock()
    monkeypatch.setattr(entry.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(entry.sys, "stderr", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(entry, "_require_future_deadline", gate)
    monkeypatch.setattr(entry, "_preflight", lambda *_: None)
    monkeypatch.setattr(entry, "read_exact_jev_key", _key)
    monkeypatch.setattr(entry, "_prompt_phrase", lambda: secrets.token_urlsafe(48))
    monkeypatch.setattr(entry.subprocess, "Popen", process)
    with pytest.raises(ValueError, match="^browser_jev_launcher_deadline_invalid$"):
        entry.launch("once", approved_deadline_utc=_future(),
                     expected_image_id="sha256:" + "a" * 64)
    assert gate.call_count == 3
    process.assert_not_called()


def test_owner_terminal_required_before_source_or_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Mock()
    phrase = Mock()
    monkeypatch.setattr(entry.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(entry, "read_exact_jev_key", source)
    monkeypatch.setattr(entry, "_prompt_phrase", phrase)
    with pytest.raises(ValueError, match="^browser_jev_owner_terminal_required$"):
        entry.launch("api", approved_deadline_utc=_future(),
                     expected_image_id="sha256:" + "a" * 64)
    source.assert_not_called()
    phrase.assert_not_called()


def test_hidden_phrase_warning_never_falls_back_to_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entry.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(entry.sys, "stderr", SimpleNamespace(isatty=lambda: True))

    def warning(_: str) -> str:
        import warnings
        warnings.warn("synthetic nonsecret warning", entry.getpass.GetPassWarning)
        raise AssertionError("echo fallback must not continue")

    monkeypatch.setattr(entry.getpass, "getpass", warning)
    with pytest.raises(ValueError, match="^browser_jev_passphrase_invalid$"):
        entry._prompt_phrase()


@pytest.mark.parametrize("operation", ["client", "supervisor", "worker"])
def test_no_other_recipient_can_consume_secret_input(
    operation: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Mock()
    monkeypatch.setattr(entry, "read_exact_jev_key", source)
    with pytest.raises(ValueError, match="^browser_jev_launcher_arguments_invalid$"):
        entry.launch(operation, approved_deadline_utc=_future(),
                     expected_image_id="sha256:" + "a" * 64)
    source.assert_not_called()


def test_exact_passport_preserves_unicode_spaces_and_rejects_duplicates() -> None:
    phrase = "  synthetic vault phrase \u4e00 "
    payload = b"other=\xff\xfe\nJEV-PASSPORT=decoy\njev-passport='" + phrase.encode() + b"'\r\n"
    assert entry._project_field(io.BytesIO(payload), b"jev-passport") == phrase
    with pytest.raises(ValueError):
        entry._project_field(io.BytesIO(payload + b"jev-passport=duplicate\n"), b"jev-passport")


@pytest.mark.parametrize("operation", ["inspect", "deactivate"])
def test_automatic_cleanup_sends_phrase_only_without_prompt_or_key(
    operation: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    phrase = secrets.token_urlsafe(32)
    reads: list[bytes] = []
    frames: list[bytes] = []
    arguments: list[list[str]] = []
    monkeypatch.setattr(entry.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(entry.sys, "stderr", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(entry, "_preflight", lambda *_: None)
    monkeypatch.setattr(entry, "_check_image", lambda *_: None)
    monkeypatch.setattr(entry, "_read_exact_field", lambda field: (reads.append(field) or phrase))
    monkeypatch.setattr(entry, "read_exact_jev_key", Mock(side_effect=AssertionError("key read")))
    monkeypatch.setattr(entry, "_prompt_phrase", Mock(side_effect=AssertionError("prompt")))

    class Process:
        returncode = 0
        def __init__(self, args: list[str], **kwargs: object) -> None:
            arguments.append(args)
            assert kwargs["stdin"] is subprocess.PIPE
            assert phrase not in repr(args) and phrase not in repr(kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *_: object) -> None:
            pass
        def communicate(self, *, input: bytes) -> None:
            frames.append(input)

    monkeypatch.setattr(entry.subprocess, "Popen", Process)
    assert entry.launch(operation, approved_deadline_utc=_future(),
                        expected_image_id="sha256:"+"a"*64, phrase_from_exact_field=True) == 0
    assert reads == [b"jev-passport"]
    assert len(arguments) == len(frames) == 1
    assert arguments[0][-2:] == ["--operation", operation]
    assert json.loads(frames[0]) == {"version": entry._FRAME_VERSION, "vault_passphrase": phrase}


def test_preflight_uses_only_filtered_metadata_and_quiet_compose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = Path(entry.__file__).resolve().parent.parent
    empty, seccomp = tmp_path / "empty.env", tmp_path / "profile.json"
    empty.write_bytes(b"")
    seccomp.write_bytes(b"{}")
    monkeypatch.setattr(entry, "_WORKTREE", worktree)
    monkeypatch.setattr(entry, "_EMPTY_ENV", empty)
    monkeypatch.setattr(entry, "_SECCOMP", seccomp)
    monkeypatch.setattr(entry, "_SECCOMP_SHA256", entry.hashlib.sha256(b"{}").hexdigest())
    monkeypatch.chdir(worktree)
    image_id = "sha256:" + "a" * 64
    calls: list[tuple[list[str], dict]] = []

    def run(arguments: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=0, stdout=(image_id + "|pwuser").encode()
                               if "inspect" in arguments else b"")

    monkeypatch.setattr(entry.subprocess, "run", run)
    entry._preflight("api", image_id, {})
    assert calls[0][0] == ["docker", "image", "inspect", "--format",
                           "{{.Id}}|{{.Config.User}}", "eternalai-browser-v42:local"]
    assert calls[1][0] == ["docker", "container", "ls", "--all", "--filter",
                           "name=^/browser-v42-single-api$", "--format", "{{.Names}}"]
    assert calls[2][0] == entry._compose() + ["config", "--quiet"]
    assert calls[2][1]["stdout"] is subprocess.DEVNULL
    assert all(kwargs["stderr"] is subprocess.DEVNULL for _, kwargs in calls)


@pytest.mark.parametrize("failure", ["image", "user", "retained", "compose"])
def test_preflight_failures_stop_before_secret_read_or_dispatch(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = Path(entry.__file__).resolve().parent.parent
    empty, seccomp = tmp_path / "empty.env", tmp_path / "profile.json"
    empty.write_bytes(b"")
    seccomp.write_bytes(b"{}")
    monkeypatch.setattr(entry, "_WORKTREE", worktree)
    monkeypatch.setattr(entry, "_EMPTY_ENV", empty)
    monkeypatch.setattr(entry, "_SECCOMP", seccomp)
    monkeypatch.setattr(entry, "_SECCOMP_SHA256", entry.hashlib.sha256(b"{}").hexdigest())
    monkeypatch.chdir(worktree)
    monkeypatch.setattr(entry.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(entry.sys, "stderr", SimpleNamespace(isatty=lambda: True))
    image_id = "sha256:" + "a" * 64

    def run(arguments: list[str], **_: object) -> SimpleNamespace:
        if "inspect" in arguments:
            value = "sha256:" + "b" * 64 if failure == "image" else image_id
            return SimpleNamespace(returncode=0, stdout=(value + ("|root" if failure == "user"
                                                                  else "|pwuser")).encode())
        if "ls" in arguments:
            return SimpleNamespace(returncode=0, stdout=b"retained" if failure == "retained"
                                   else b"")
        return SimpleNamespace(returncode=1 if failure == "compose" else 0, stdout=b"")

    source, process = Mock(), Mock()
    monkeypatch.setattr(entry.subprocess, "run", run)
    monkeypatch.setattr(entry.subprocess, "Popen", process)
    monkeypatch.setattr(entry, "read_exact_jev_key", source)
    with pytest.raises(ValueError, match="^browser_jev_launcher_preflight_failed$"):
        entry.launch("api", approved_deadline_utc=_future(), expected_image_id=image_id)
    source.assert_not_called()
    process.assert_not_called()
