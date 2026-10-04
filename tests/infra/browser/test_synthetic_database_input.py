"""Fixed task-file boundaries and all engine routes, without real secret or DB IO."""

from __future__ import annotations

import asyncio
import errno
import importlib.util
import secrets
import stat
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from app.infra.browser import synthetic_bootstrap as bootstrap
from app.infra.browser import synthetic_configuration as configuration
from app.ports.auth import SessionTokenError
from app.ports.browser_publication_store import BrowserPublicationError


@pytest.fixture
def mounted_file(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Only synthesized bytes and fake descriptors; approved bind metadata is retained."""
    file_io = SimpleNamespace(
        O_RDONLY=0, O_NOFOLLOW=0x20000, O_CLOEXEC=0x80000, O_NONBLOCK=0x800,
        ST_RDONLY=1,
        open=Mock(return_value=7),
        fstat=Mock(return_value=SimpleNamespace(
            st_mode=stat.S_IFREG | 0o777, st_size=64, st_uid=0,
        )),
        fstatvfs=Mock(return_value=SimpleNamespace(f_flag=1)),
        read=Mock(return_value=secrets.token_urlsafe(48).encode("ascii")),
        close=Mock(),
    )
    monkeypatch.setattr(configuration, "os", file_io)
    monkeypatch.setattr(configuration, "sys", SimpleNamespace(platform="linux"))
    return file_io


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_non_linux_refuses_before_open(
    platform: str, mounted_file: SimpleNamespace,
) -> None:
    configuration.sys.platform = platform
    with pytest.raises(ValueError, match="^browser_database_password_file_invalid$"):
        configuration.read_database_password()
    mounted_file.open.assert_not_called()
    mounted_file.read.assert_not_called()


def test_symlink_refuses_without_read(mounted_file: SimpleNamespace) -> None:
    def refuse_symlink(path: str, flags: int) -> int:
        assert path == "/run/secrets/task-db-password"
        assert flags & mounted_file.O_NOFOLLOW
        raise OSError(errno.ELOOP, secrets.token_urlsafe(24))

    mounted_file.open.side_effect = refuse_symlink
    with pytest.raises(ValueError, match="^browser_database_password_file_invalid$"):
        configuration.read_database_password()
    mounted_file.read.assert_not_called()
    mounted_file.close.assert_not_called()


@pytest.mark.parametrize("mode,size", [
    (stat.S_IFDIR, 64), (stat.S_IFIFO, 64), (stat.S_IFLNK, 64),
    (stat.S_IFREG, 0), (stat.S_IFREG, 63), (stat.S_IFREG, 65),
])
def test_invalid_file_metadata_refuses_before_read(
    mode: int, size: int, mounted_file: SimpleNamespace,
) -> None:
    mounted_file.fstat.return_value = SimpleNamespace(st_mode=mode | 0o777, st_size=size)
    with pytest.raises(ValueError, match="^browser_database_password_file_invalid$"):
        configuration.read_database_password()
    mounted_file.read.assert_not_called()
    mounted_file.close.assert_called_once_with(7)


def test_writable_mount_refuses_before_read(mounted_file: SimpleNamespace) -> None:
    mounted_file.fstatvfs.return_value = SimpleNamespace(f_flag=0)
    with pytest.raises(ValueError, match="^browser_database_password_file_invalid$"):
        configuration.read_database_password()
    mounted_file.fstatvfs.assert_called_once_with(7)
    mounted_file.read.assert_not_called()
    mounted_file.close.assert_called_once_with(7)


@pytest.mark.parametrize("operation", ["open", "fstat", "fstatvfs", "read", "close"])
def test_file_error_is_redacted(
    operation: str, mounted_file: SimpleNamespace,
) -> None:
    marker = secrets.token_urlsafe(24)
    getattr(mounted_file, operation).side_effect = OSError(marker)
    with pytest.raises(ValueError, match="^browser_database_password_file_invalid$") as caught:
        configuration.read_database_password()
    assert marker not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__suppress_context__ is True
    if operation != "open":
        mounted_file.close.assert_called_once_with(7)


@pytest.mark.parametrize("malformation", [
    "space", "newline", "nul", "non-ascii", "punctuation", "short", "extra",
])
def test_invalid_file_content_fails_closed(
    malformation: str, mounted_file: SimpleNamespace,
) -> None:
    valid = mounted_file.read.return_value
    replacements = {
        "space": b" ", "newline": b"\n", "nul": b"\x00",
        "non-ascii": b"\xff", "punctuation": b"/",
    }
    malformed = (valid[:-1] if malformation == "short"
                 else valid + b"A" if malformation == "extra"
                 else replacements[malformation] + valid[1:])
    mounted_file.read.return_value = malformed
    with pytest.raises(ValueError, match="^browser_database_password_file_invalid$"):
        configuration.read_database_password()
    mounted_file.read.assert_called_once_with(7, 65)
    mounted_file.close.assert_called_once_with(7)


def test_password_reads_fixed_readonly_file_and_has_redacted_representation(
    mounted_file: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    value = mounted_file.read.return_value.decode("ascii")
    result = configuration.read_database_password()
    assert result == SecretStr(value)
    assert result.get_secret_value() == value
    assert value not in repr(result)
    mounted_file.open.assert_called_once_with(
        "/run/secrets/task-db-password",
        mounted_file.O_RDONLY | mounted_file.O_NOFOLLOW | mounted_file.O_CLOEXEC
        | mounted_file.O_NONBLOCK,
    )
    mounted_file.fstat.assert_called_once_with(7)
    mounted_file.fstatvfs.assert_called_once_with(7)
    mounted_file.read.assert_called_once_with(7, 65)
    mounted_file.close.assert_called_once_with(7)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_missing_file_has_no_console_or_environment_fallback(
    monkeypatch: pytest.MonkeyPatch, mounted_file: SimpleNamespace,
) -> None:
    forbidden = Mock(side_effect=AssertionError("fallback reached"))
    monkeypatch.setattr("builtins.input", forbidden)
    mounted_file.getenv = forbidden
    mounted_file.environ = SimpleNamespace(get=forbidden)
    configuration.sys.stdin = SimpleNamespace(isatty=forbidden, read=forbidden)
    configuration.sys.stderr = SimpleNamespace(isatty=forbidden)
    mounted_file.open.side_effect = FileNotFoundError(secrets.token_urlsafe(24))
    with pytest.raises(ValueError, match="^browser_database_password_file_invalid$"):
        configuration.read_database_password()
    forbidden.assert_not_called()
    mounted_file.read.assert_not_called()


class _EngineReached(Exception):
    pass


@pytest.mark.parametrize("route", ["preflight", "init", "operator", "deactivate"])
def test_every_engine_receives_file_password_only_in_connect_args(
    route: str, monkeypatch: pytest.MonkeyPatch, mounted_file: SimpleNamespace,
) -> None:
    secret = SecretStr(mounted_file.read.return_value.decode("ascii"))
    engine = Mock(side_effect=_EngineReached)

    async def invoke() -> None:
        if route in {"preflight", "init"}:
            monkeypatch.setattr(bootstrap, "create_async_engine", engine)
            if route == "preflight":
                await bootstrap.preflight_synthetic_bootstrap()
            else:
                monkeypatch.setattr(bootstrap, "_private_console", lambda: None)
                await bootstrap.initialize_synthetic_bootstrap(
                    configuration.synthetic_jev_manifest(), enabled=True,
                )
            return
        # Windows' existing test environment uses httpx; no HTTP transport is used here.
        if (sys.platform == "win32" and "httpx2" not in sys.modules
                and importlib.util.find_spec("httpx2") is None):
            import httpx

            monkeypatch.setitem(sys.modules, "httpx2", httpx)
        from app.infra.browser import synthetic_operator as operator

        monkeypatch.setattr(operator, "create_async_engine", engine)
        monkeypatch.setattr(operator, "image_deployment", AsyncMock(return_value=None))
        documents, _ = bootstrap._material(configuration.synthetic_jev_manifest())
        if route == "deactivate":
            await operator.deactivate_synthetic_publication(
                configuration.SyntheticDeactivationBundle.model_validate(
                    documents["deactivation.bundle.enc"],
                ), enabled=True,
            )
        else:
            async with operator.open_synthetic_operator(
                configuration.SyntheticOperatorBundle.model_validate(
                    documents["operator.bundle.enc"],
                ), jev_key=SecretStr(secrets.token_urlsafe(24)), enabled=True,
                input_mode="structured",
            ):
                pytest.fail("engine interception must precede runtime IO")

    with pytest.raises(_EngineReached):
        asyncio.run(invoke())
    mounted_file.read.assert_called_once_with(7, 65)
    engine.assert_called_once()
    args, kwargs = engine.call_args
    assert args == (configuration.DATABASE_URL,)
    assert SecretStr(kwargs["connect_args"]["password"]) == secret
    assert kwargs["echo"] is False
    assert kwargs["hide_parameters"] is True
    other_arguments = {
        key: value for key, value in kwargs.items() if key != "connect_args"
    }
    other_connect_arguments = {
        key: value for key, value in kwargs["connect_args"].items() if key != "password"
    }
    assert secret.get_secret_value() not in repr((args, other_arguments, other_connect_arguments))


@pytest.mark.parametrize("failure", ["file", "engine"])
def test_bootstrap_cli_redacts_file_and_engine_errors(
    failure: str, monkeypatch: pytest.MonkeyPatch, mounted_file: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.bootstrap_browser_synthetic import main

    value = mounted_file.read.return_value.decode("ascii")
    engine = Mock(side_effect=ValueError(value))
    if failure == "file":
        mounted_file.open.side_effect = OSError(value)
    monkeypatch.setattr(bootstrap, "create_async_engine", engine)
    assert main([]) == 2
    if failure == "file":
        engine.assert_not_called()
    else:
        mounted_file.read.assert_called_once_with(7, 65)
        engine.assert_called_once()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "browser_bootstrap_unavailable\n"
    assert value not in captured.err


@pytest.fixture
def publication_cli(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Mock the actual publication assembly/await seams; no external IO is reachable."""
    if (sys.platform == "win32" and "httpx2" not in sys.modules
            and importlib.util.find_spec("httpx2") is None):
        import httpx

        monkeypatch.setitem(sys.modules, "httpx2", httpx)
    from scripts import manage_browser_synthetic_publication as cli

    vertical = SimpleNamespace(prepare_seed=AsyncMock(), activate_seed=AsyncMock())
    components = SimpleNamespace(vertical=vertical, publication_owner=object())
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=components)
    context.__aexit__ = AsyncMock(return_value=False)
    opening = Mock(return_value=context)
    bundle = Mock(return_value=object())
    key = Mock(return_value=SecretStr(secrets.token_urlsafe(24)))
    monkeypatch.setattr(cli, "prompt_operator_bundle", bundle)
    monkeypatch.setattr(cli, "prompt_openrouter_key", key)
    monkeypatch.setattr(cli, "open_synthetic_operator", opening)
    return SimpleNamespace(
        cli=cli, vertical=vertical, components=components, context=context,
        opening=opening, bundle=bundle, key=key,
    )


def test_publication_empty_key_is_input_failure_before_assembly(
    publication_cli: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.infra.browser import openrouter_jev as jev

    monkeypatch.setattr(jev, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True),
        stderr=SimpleNamespace(isatty=lambda: True),
    ))
    hidden_prompt = Mock(return_value="")
    monkeypatch.setattr(jev.getpass, "getpass", hidden_prompt)
    monkeypatch.setattr(publication_cli.cli, "prompt_openrouter_key", jev.prompt_openrouter_key)
    assert publication_cli.cli.main(["--enable", "--operation", "prepare"]) == 2
    publication_cli.bundle.assert_called_once_with(None)
    hidden_prompt.assert_called_once()
    publication_cli.opening.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "browser_synthetic_publication_key_input_invalid\n"


def test_publication_later_configuration_failure_is_distinct(
    publication_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    publication_cli.context.__aenter__.side_effect = ValueError(
        "browser_operator_installation_unavailable",
    )
    assert publication_cli.cli.main(["--enable", "--operation", "prepare"]) == 2
    publication_cli.key.assert_called_once_with()
    publication_cli.context.__aenter__.assert_awaited_once()
    publication_cli.vertical.prepare_seed.assert_not_awaited()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "browser_synthetic_publication_configuration_invalid\n"


def test_publication_prepare_database_error_emits_only_fixed_code(
    publication_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    publication_cli.vertical.prepare_seed.side_effect = SQLAlchemyError(secrets.token_urlsafe(24))
    assert publication_cli.cli.main(["--enable", "--operation", "prepare"]) == 2
    publication_cli.vertical.prepare_seed.assert_awaited_once_with(
        publication_cli.components.publication_owner,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "browser_synthetic_publication_database_unavailable\n"


@pytest.mark.parametrize("failure", ["token", "publication"])
def test_publication_authorization_failure_is_fixed(
    failure: str, publication_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    if failure == "token":
        publication_cli.context.__aenter__.side_effect = SessionTokenError(
            secrets.token_urlsafe(24),
        )
    else:
        publication_cli.vertical.activate_seed.side_effect = BrowserPublicationError(
            "browser_operator_business_denied",
        )
    assert publication_cli.cli.main(["--enable", "--operation", "activate"]) == 2
    publication_cli.context.__aenter__.assert_awaited_once()
    if failure == "publication":
        publication_cli.vertical.activate_seed.assert_awaited_once_with(
            publication_cli.components.publication_owner,
        )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "browser_synthetic_publication_authorization_denied\n"


@pytest.mark.parametrize("failure", [ValueError, RuntimeError])
def test_publication_unknown_error_remains_generic(
    failure: type[Exception], publication_cli: SimpleNamespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    publication_cli.context.__aenter__.side_effect = failure(secrets.token_urlsafe(24))
    assert publication_cli.cli.main(["--enable", "--operation", "prepare"]) == 2
    publication_cli.context.__aenter__.assert_awaited_once()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "browser_synthetic_publication_unavailable\n"


def test_publication_success_preserves_marker_and_operation_arguments(
    publication_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    assert publication_cli.cli.main([
        "--enable", "--operation", "prepare", "--input-mode", "structured",
    ]) == 0
    publication_cli.opening.assert_called_once_with(
        publication_cli.bundle.return_value, jev_key=publication_cli.key.return_value,
        enabled=True, require_active_publication=False, input_mode="structured",
    )
    publication_cli.vertical.prepare_seed.assert_awaited_once_with(
        publication_cli.components.publication_owner,
    )
    publication_cli.vertical.activate_seed.assert_not_awaited()
    publication_cli.context.__aexit__.assert_awaited_once_with(None, None, None)
    captured = capsys.readouterr()
    assert captured.out == "browser_synthetic_publication_updated\n"
    assert captured.err == ""
