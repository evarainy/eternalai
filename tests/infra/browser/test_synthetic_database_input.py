"""TTY password boundaries and all engine routes, without database or secret IO."""

from __future__ import annotations

import asyncio
import getpass
import importlib.util
import secrets
import sys
import traceback
import warnings
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import SecretStr

from app.infra.browser import synthetic_bootstrap as bootstrap
from app.infra.browser import synthetic_configuration as configuration


@pytest.fixture
def private_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(configuration, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True),
        stderr=SimpleNamespace(isatty=lambda: True),
    ))


@pytest.mark.parametrize("stream", ["stdin", "stderr"])
def test_non_tty_refuses_before_read(
    stream: str, monkeypatch: pytest.MonkeyPatch, private_console: None,
) -> None:
    monkeypatch.setattr(getattr(configuration.sys, stream), "isatty", lambda: False)
    reader = Mock(side_effect=AssertionError("input must not be reached"))
    monkeypatch.setattr(getpass, "getpass", reader)
    with pytest.raises(ValueError, match="^browser_database_private_console_required$"):
        configuration.prompt_database_password()
    reader.assert_not_called()


def test_warning_stops_before_echo_fallback(
    monkeypatch: pytest.MonkeyPatch, private_console: None,
) -> None:
    fallback = Mock()

    def insecure(prompt: str) -> str:
        warnings.warn("synthetic unavailable terminal", getpass.GetPassWarning, stacklevel=2)
        fallback()
        return secrets.token_urlsafe(24)

    monkeypatch.setattr(getpass, "getpass", insecure)
    with pytest.raises(ValueError, match="^browser_database_private_console_required$"):
        configuration.prompt_database_password()
    fallback.assert_not_called()


@pytest.mark.parametrize("error_type", [EOFError, OSError, RuntimeError, KeyboardInterrupt])
def test_read_error_is_redacted(
    error_type: type[BaseException], monkeypatch: pytest.MonkeyPatch, private_console: None,
) -> None:
    marker = secrets.token_urlsafe(24)
    monkeypatch.setattr(getpass, "getpass", Mock(side_effect=error_type(marker)))
    with pytest.raises(ValueError, match="^browser_database_private_console_required$") as caught:
        configuration.prompt_database_password()
    assert marker not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("value", ["", "\x00", "x" * 4097], ids=["empty", "nul", "too-long"])
def test_invalid_input_fails_closed(
    value: str, monkeypatch: pytest.MonkeyPatch, private_console: None,
) -> None:
    monkeypatch.setattr(getpass, "getpass", Mock(return_value=value))
    with pytest.raises(ValueError, match="^browser_database_password_invalid$"):
        configuration.prompt_database_password()


def test_password_preserves_exact_input_and_has_redacted_representation(
    monkeypatch: pytest.MonkeyPatch, private_console: None, capsys: pytest.CaptureFixture[str],
) -> None:
    value = " " + secrets.token_urlsafe(24) + " "
    reader = Mock(return_value=value)
    monkeypatch.setattr(getpass, "getpass", reader)
    result = configuration.prompt_database_password()
    assert result == SecretStr(value)
    assert result.get_secret_value() == value
    assert value not in repr(result)
    reader.assert_called_once_with("PostgreSQL browser_v42_test password (hidden): ")
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


class _EngineReached(Exception):
    pass


@pytest.mark.parametrize("route", ["preflight", "init", "operator", "deactivate"])
def test_every_engine_receives_tty_password_only_in_connect_args(
    route: str, monkeypatch: pytest.MonkeyPatch, private_console: None,
) -> None:
    secret = SecretStr(secrets.token_urlsafe(24))
    reader = Mock(return_value=secret.get_secret_value())
    monkeypatch.setattr(getpass, "getpass", reader)
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
        if "httpx2" not in sys.modules and importlib.util.find_spec("httpx2") is None:
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
    reader.assert_called_once()
    engine.assert_called_once()
    args, kwargs = engine.call_args
    assert args == (configuration.DATABASE_URL,)
    assert SecretStr(kwargs["connect_args"]["password"]) == secret
    assert kwargs["echo"] is False
    assert kwargs["hide_parameters"] is True


def test_bootstrap_cli_redacts_engine_error(
    monkeypatch: pytest.MonkeyPatch, private_console: None, capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.bootstrap_browser_synthetic import main

    value = secrets.token_urlsafe(24)
    reader = Mock(return_value=value)
    engine = Mock(side_effect=ValueError(value))
    monkeypatch.setattr(getpass, "getpass", reader)
    monkeypatch.setattr(bootstrap, "create_async_engine", engine)
    assert main([]) == 2
    reader.assert_called_once()
    engine.assert_called_once()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "browser_bootstrap_unavailable\n"
    assert value not in captured.err
