"""Private stdin, fixed operator vault guards and real entrypoint wiring; no live IO."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import secrets
import stat
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr

from app.infra.browser import synthetic_private_input as private
from app.infra.browser import synthetic_vault as vault
from app.infra.browser.synthetic_configuration import (
    SyntheticOperatorBundle,
    synthetic_jev_manifest,
)
from scripts import manage_browser_synthetic_publication as publication
from scripts import run_browser_synthetic_api as api
from scripts import run_browser_synthetic_once as once


def _document() -> dict:
    fields = ("session_signing_key", "session_binding_key", "input_digest_key",
              "result_digest_key", "proof_context_key", "publication_token",
              "cleanup_token", "business_token")
    result = {name: base64.b64encode(secrets.token_bytes(32)).decode() for name in fields}
    for prefix in ("payload", "resource", "request_digest"):
        result[prefix + "_keys"] = {"generated": secrets.token_urlsafe(32)}
        result["active_" + prefix + "_key_id"] = "generated"
    result["jev_manifest"] = synthetic_jev_manifest().model_dump(mode="json")
    return result


def _frame(**changes: object) -> bytes:
    return json.dumps({"version": private.FRAME_VERSION,
                       "vault_passphrase": secrets.token_urlsafe(24),
                       "jev_key": secrets.token_urlsafe(32), **changes}).encode("ascii")


def _stdin(monkeypatch: pytest.MonkeyPatch, payload: bytes, *, tty: bool = False) -> io.BytesIO:
    stream = io.BytesIO(payload)
    monkeypatch.setattr(private, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: tty, buffer=stream),
    ))
    return stream


def test_valid_frame_unlocks_fixed_operator_and_returns_secretstr_without_echo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    document = _document()
    phrase = "  " + secrets.token_urlsafe(32) + " \u4e00\U0001f31f "
    key = secrets.token_urlsafe(32)
    _stdin(monkeypatch, _frame(vault_passphrase=phrase, jev_key=key))
    unlock = Mock(return_value=document)
    monkeypatch.setattr(private, "read_private_operator_document", unlock)
    bundle, actual_key = private.read_private_operator_input()
    assert isinstance(bundle, SyntheticOperatorBundle)
    assert isinstance(actual_key, SecretStr)
    assert actual_key.get_secret_value() == key
    supplied = unlock.call_args.args[0]
    assert isinstance(supplied, SecretStr) and supplied.get_secret_value() == phrase
    assert key not in repr(actual_key) and phrase not in repr(supplied)
    assert bundle.publication_token.get_secret_value() == document["publication_token"]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("kind", [
    "empty", "array", "version", "extra", "missing", "duplicate", "trailing",
    "nonascii", "oversize", "phrase_short", "phrase_long", "phrase_type", "phrase_surrogate",
])
def test_malformed_frame_never_unlocks_and_has_only_fixed_error(
    kind: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    marker = secrets.token_urlsafe(32)
    valid = _frame(jev_key=marker)
    frame = json.loads(valid)
    missing = {name: value for name, value in frame.items() if name != "jev_key"}
    mutations = {
        "empty": b"", "array": b"[]", "version": _frame(version="unapproved"),
        "extra": _frame(extra=marker), "missing": json.dumps(missing).encode(),
        "duplicate": valid[:-1] + b',"jev_key":"' + marker.encode() + b'"}',
        "trailing": valid + b"{}", "nonascii": b"\xff" + valid,
        "oversize": b" " * (private.MAX_FRAME_BYTES + 1),
        "phrase_short": _frame(vault_passphrase=marker[:11]),
        "phrase_long": _frame(vault_passphrase="x" * 4097),
        "phrase_type": _frame(vault_passphrase=[marker]),
        "phrase_surrogate": _frame(vault_passphrase=marker + "\ud800"),
    }
    _stdin(monkeypatch, mutations[kind])
    unlock = Mock()
    monkeypatch.setattr(private, "read_private_operator_document", unlock)
    with pytest.raises(ValueError) as caught:
        private.read_private_operator_input()
    assert caught.value.args == ("browser_private_input_invalid",)
    assert marker not in repr(caught.value)
    assert marker not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__suppress_context__
    unlock.assert_not_called()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("kind", ["empty", "space", "control", "nonascii", "oversize", "type"])
def test_bad_key_preserves_safe_fixed_diagnostic_without_unlock(
    kind: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = secrets.token_urlsafe(32)
    key = {"empty": "", "space": " " + marker, "control": marker + "\n",
           "nonascii": marker + "\u4e00", "oversize": "x" * 4097, "type": [marker]}[kind]
    _stdin(monkeypatch, _frame(jev_key=key))
    unlock = Mock()
    monkeypatch.setattr(private, "read_private_operator_document", unlock)
    with pytest.raises(ValueError, match="^jev_key_input_invalid$") as caught:
        private.read_private_operator_input()
    assert marker not in "".join(traceback.format_exception(caught.value))
    unlock.assert_not_called()


def test_private_reader_uses_one_bounded_read_and_rejects_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read = Mock(return_value=_frame())
    monkeypatch.setattr(private, "sys", SimpleNamespace(stdin=SimpleNamespace(
        isatty=lambda: False, buffer=SimpleNamespace(read=read),
    )))
    monkeypatch.setattr(private, "read_private_operator_document", lambda _: _document())
    private.read_private_operator_input()
    read.assert_called_once_with(private.MAX_FRAME_BYTES + 1)
    read.reset_mock()
    monkeypatch.setattr(private.sys.stdin, "isatty", lambda: True)
    with pytest.raises(ValueError, match="^browser_private_input_invalid$"):
        private.read_private_operator_input()
    read.assert_not_called()


def test_unlock_failure_is_suppressed_without_rendering_ciphertext_or_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = secrets.token_urlsafe(48)
    _stdin(monkeypatch, _frame(vault_passphrase=marker))
    monkeypatch.setattr(private, "read_private_operator_document",
                        Mock(side_effect=ValueError(marker)))
    with pytest.raises(ValueError, match="^browser_operator_bundle_invalid$") as caught:
        private.read_private_operator_input()
    assert marker not in "".join(traceback.format_exception(caught.value))


def test_operator_unlock_has_no_path_selection_or_console_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phrase = secrets.token_urlsafe(32)
    document = _document()
    encrypted = vault.encrypt_document(vault.OPERATOR_FILE, document, phrase)
    checked = Mock(return_value=encrypted)
    console = Mock(side_effect=AssertionError("TTY path reached"))
    monkeypatch.setattr(vault, "_read_checked_ciphertext", checked)
    monkeypatch.setattr(vault, "_private_console", console)
    monkeypatch.setattr(vault, "prompt_passphrase", console)
    assert vault.read_private_operator_document(SecretStr(phrase)) == document
    checked.assert_called_once_with(vault.VAULT_DIRECTORY / vault.OPERATOR_FILE,
                                    expected_name=vault.OPERATOR_FILE)
    console.assert_not_called()


def test_default_vault_still_requires_private_console_before_read(monkeypatch: pytest.MonkeyPatch):
    checked = Mock()
    console = Mock(side_effect=ValueError("browser_vault_private_console_required"))
    monkeypatch.setattr(vault, "_read_checked_ciphertext", checked)
    monkeypatch.setattr(vault, "_private_console", console)
    with pytest.raises(ValueError, match="^browser_vault_private_console_required$"):
        vault.read_encrypted(vault.VAULT_DIRECTORY / vault.OPERATOR_FILE,
                             expected_name=vault.OPERATOR_FILE)
    checked.assert_not_called()


@pytest.mark.parametrize("boundary", ["mode", "owner", "nlink", "oversize", "symlink"])
@pytest.mark.parametrize("stage", ["lstat", "fstat"])
def test_shared_ciphertext_reader_retains_metadata_and_nofollow_guards(
    boundary: str, stage: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe = dict(st_mode=stat.S_IFREG | 0o600, st_uid=1000, st_nlink=1, st_size=64)
    bad = {**safe, **{
        "mode": {"st_mode": stat.S_IFREG | 0o644}, "owner": {"st_uid": 0},
        "nlink": {"st_nlink": 2}, "oversize": {"st_size": vault._MAX_CIPHERTEXT + 1},
        "symlink": {"st_mode": stat.S_IFLNK | 0o600},
    }[boundary]}
    path = Mock()
    path.lstat.return_value = SimpleNamespace(**(bad if stage == "lstat" else safe))
    stream = Mock()
    stream.__enter__ = Mock(return_value=stream)
    stream.__exit__ = Mock(return_value=False)
    stream.fileno.return_value = 7
    file_io = SimpleNamespace(
        O_RDONLY=0, O_NOFOLLOW=0x20000, O_CLOEXEC=0x80000,
        open=Mock(return_value=7), fdopen=Mock(return_value=stream),
        fstat=Mock(return_value=SimpleNamespace(**(bad if stage == "fstat" else safe))),
    )
    directory = Mock()
    monkeypatch.setattr(vault, "os", file_io)
    monkeypatch.setattr(vault, "_current_uid", lambda: 1000)
    monkeypatch.setattr(vault, "_file_path", lambda _: path)
    monkeypatch.setattr(vault, "_check_directory", directory)
    with pytest.raises(ValueError, match="^browser_vault_unlock_failed$"):
        vault._read_checked_ciphertext(path, expected_name=vault.OPERATOR_FILE)
    directory.assert_called_once_with(create=False)
    stream.read.assert_not_called()
    if stage == "lstat":
        file_io.open.assert_not_called()
    else:
        file_io.open.assert_called_once_with(path, 0x20000 | 0x80000)


@pytest.mark.parametrize("operation", ["prepare", "activate"])
@pytest.mark.parametrize("private_stdin", [False, True])
def test_publication_real_entry_wires_exact_bundle_and_key(
    operation: str, private_stdin: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = SyntheticOperatorBundle.model_validate(_document())
    key = SecretStr(secrets.token_urlsafe(32))
    reader = Mock(return_value=(bundle, key))
    prompt_bundle, prompt_key = Mock(return_value=bundle), Mock(return_value=key)
    monkeypatch.setattr(publication, "read_private_operator_input", reader)
    monkeypatch.setattr(publication, "prompt_operator_bundle", prompt_bundle)
    monkeypatch.setattr(publication, "prompt_openrouter_key", prompt_key)
    components = SimpleNamespace(publication_owner=object(), vertical=SimpleNamespace(
        prepare_seed=AsyncMock(), activate_seed=AsyncMock(),
    ))
    opened: list[tuple] = []

    @asynccontextmanager
    async def operator(actual: object, **kwargs: object):
        opened.append((actual, kwargs))
        yield components

    monkeypatch.setattr(publication, "open_synthetic_operator", operator)
    asyncio.run(publication._operate(
        operation, input_mode="structured", private_stdin=private_stdin,
    ))
    assert opened == [(bundle, {"jev_key": key, "enabled": True,
                                "require_active_publication": False, "input_mode": "structured"})]
    getattr(components.vertical, operation + "_seed").assert_awaited_once_with(
        components.publication_owner,
    )
    if private_stdin:
        reader.assert_called_once_with()
        prompt_bundle.assert_not_called()
        prompt_key.assert_not_called()
    else:
        reader.assert_not_called()
        prompt_bundle.assert_called_once_with(None)
        prompt_key.assert_called_once_with()


def test_publication_private_key_failure_preserves_key_diagnostic(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(publication, "read_private_operator_input",
                        Mock(side_effect=ValueError("jev_key_input_invalid")))
    assert publication.main(["--enable", "--operation", "prepare", "--private-stdin"]) == 2
    assert capsys.readouterr() == ("", "browser_synthetic_publication_key_input_invalid\n")


def test_publication_private_stdin_rejects_deactivation_before_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, deactivate = Mock(), Mock()
    monkeypatch.setattr(publication, "read_private_operator_input", reader)
    monkeypatch.setattr(publication, "prompt_deactivation_bundle", deactivate)
    with pytest.raises(ValueError, match="^browser_operator_arguments_invalid$"):
        asyncio.run(publication._operate("deactivate", private_stdin=True))
    reader.assert_not_called()
    deactivate.assert_not_called()


@pytest.mark.parametrize("private_stdin", [False, True])
def test_api_real_entry_wires_exact_input_and_existing_server(
    private_stdin: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = SyntheticOperatorBundle.model_validate(_document())
    key = SecretStr(secrets.token_urlsafe(32))
    reader = Mock(return_value=(bundle, key))
    prompt_bundle, prompt_key = Mock(return_value=bundle), Mock(return_value=key)
    monkeypatch.setattr(api, "read_private_operator_input", reader)
    monkeypatch.setattr(api, "prompt_operator_bundle", prompt_bundle)
    monkeypatch.setattr(api, "prompt_openrouter_key", prompt_key)
    components, application = object(), object()
    opened: list[tuple] = []

    @asynccontextmanager
    async def operator(actual: object, **kwargs: object):
        opened.append((actual, kwargs))
        yield components

    create = Mock(return_value=application)
    config = Mock(return_value=object())
    serve = AsyncMock()
    monkeypatch.setattr(api, "open_synthetic_operator", operator)
    monkeypatch.setattr(api, "create_synthetic_api", create)
    monkeypatch.setattr(api.uvicorn, "Config", config)
    monkeypatch.setattr(api.uvicorn, "Server", Mock(return_value=SimpleNamespace(serve=serve)))
    asyncio.run(api._serve(
        input_mode="structured", operator_vault=None, private_stdin=private_stdin,
    ))
    assert opened == [(bundle, {"jev_key": key, "enabled": True, "input_mode": "structured"})]
    create.assert_called_once_with(components)
    assert config.call_args.args == (application,)
    assert config.call_args.kwargs["access_log"] is False
    serve.assert_awaited_once_with()
    if private_stdin:
        reader.assert_called_once_with()
        prompt_bundle.assert_not_called()
        prompt_key.assert_not_called()
    else:
        reader.assert_not_called()
        prompt_bundle.assert_called_once_with(encrypted_path=None)
        prompt_key.assert_called_once_with()


@pytest.mark.parametrize("private_stdin", [False, True])
def test_once_real_entry_preserves_single_attempt_guard_and_receipt(
    private_stdin: bool, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = SyntheticOperatorBundle.model_validate(_document())
    key = SecretStr(secrets.token_urlsafe(32))
    reader = Mock(return_value=(bundle, key))
    prompt_bundle, prompt_key = Mock(return_value=bundle), Mock(return_value=key)
    monkeypatch.setattr(once, "read_private_operator_input", reader)
    monkeypatch.setattr(once, "prompt_operator_bundle", prompt_bundle)
    monkeypatch.setattr(once, "prompt_openrouter_key", prompt_key)
    reference = {"task_id": "generated_task", "run_id": "generated_run"}
    receipt = Mock(return_value=reference)
    monkeypatch.setattr(once, "read_trial_file", receipt)
    budget = SimpleNamespace(request_count=0, reserve=Mock())
    construct_budget = Mock(return_value=budget)
    monkeypatch.setattr(once, "SingleJevAttempt", construct_budget)
    components = object()
    opened: list[tuple] = []

    @asynccontextmanager
    async def operator(actual: object, **kwargs: object):
        opened.append((actual, kwargs))
        yield components

    async def execute(actual: object, expected: object) -> bool:
        assert actual is components
        assert (expected.task_id, expected.run_id) == (reference["task_id"], reference["run_id"])
        budget.request_count = 1
        return True

    monkeypatch.setattr(once, "open_synthetic_operator", operator)
    monkeypatch.setattr(once, "execute_once", execute)
    assert asyncio.run(once._run("0.01", private_stdin=private_stdin)) is True
    construct_budget.assert_called_once_with("0.01")
    receipt.assert_called_once_with("trial.run.json")
    assert opened == [(bundle, {"jev_key": key, "enabled": True, "input_mode": "structured",
                                "attempt_guard": budget.reserve})]
    result = capsys.readouterr()
    assert result.err == ""
    assert json.loads(result.out) == {"jev_request_count": 1, "max_jev_requests": 1,
                                     "actual_cost_usd": None, "cost_status": "unknown"}
    assert key.get_secret_value() not in result.out
    if private_stdin:
        reader.assert_called_once_with()
        prompt_bundle.assert_not_called()
        prompt_key.assert_not_called()
    else:
        reader.assert_not_called()
        prompt_bundle.assert_called_once_with(
            encrypted_path=vault.VAULT_DIRECTORY / vault.OPERATOR_FILE,
        )
        prompt_key.assert_called_once_with()


@pytest.mark.parametrize("entry,arguments", [
    (publication, ["--operation", "prepare", "--private-stdin"]),
    (api, ["--private-stdin"]),
    (once, ["--approved-budget-usd", "0.01", "--private-stdin"]),
])
def test_private_flag_cannot_bypass_enable(
    entry: object, arguments: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = Mock()
    monkeypatch.setattr(entry, "read_private_operator_input", reader)
    assert entry.main(arguments) == 2
    reader.assert_not_called()


@pytest.mark.parametrize("entry", [publication, api])
def test_private_flag_cannot_select_another_vault(
    entry: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = Mock()
    monkeypatch.setattr(entry, "read_private_operator_input", reader)
    with pytest.raises(ValueError, match="^browser_operator_arguments_invalid$"):
        if entry is publication:
            asyncio.run(entry._operate("prepare", operator_vault=Path("unapproved"),
                                       private_stdin=True))
        else:
            asyncio.run(entry._serve(input_mode="structured", operator_vault=Path("unapproved"),
                                    private_stdin=True))
    reader.assert_not_called()
