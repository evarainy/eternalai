"""Worker entry tests use synthetic components and no production installation."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace

import pytest

from scripts import run_browser_synthetic_worker as entry


def test_worker_reuses_supervisor_and_stops_after_stop_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def run_ready() -> object:
        return ()

    async def execute() -> None:
        stop = asyncio.Event()

        class FakeSupervisor:
            last_pass_failed = False

            def __init__(self, callback: object) -> None:
                assert callback == run_ready

            async def start(self) -> None:
                events.append("start")
                stop.set()

            async def stop(self) -> None:
                events.append("stop")

        monkeypatch.setattr(entry, "BrowserWorkerSupervisor", FakeSupervisor)
        await entry.run_worker(SimpleNamespace(run_ready=run_ready), stop)

    asyncio.run(execute())
    assert events == ["start", "stop"]


def test_worker_does_not_report_success_after_failed_supervisor_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FailedPassSupervisor:
        last_pass_failed = False

        def __init__(self, callback: object) -> None:
            assert callable(callback)
            self._run_ready = callback

        async def start(self) -> None:
            events.append("start")
            try:
                await self._run_ready()
            except RuntimeError:
                self.last_pass_failed = True

        async def stop(self) -> None:
            events.append("stop")

    async def run_ready() -> object:
        raise RuntimeError("synthetic failed pass")

    async def execute() -> None:
        stop = asyncio.Event()
        stop.set()
        await entry.run_worker(SimpleNamespace(run_ready=run_ready), stop)

    monkeypatch.setattr(entry, "BrowserWorkerSupervisor", FailedPassSupervisor)
    with pytest.raises(ValueError):
        asyncio.run(execute())
    assert events == ["start", "stop"]


def test_worker_stops_supervisor_when_start_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FailingSupervisor:
        def __init__(self, callback: object) -> None:
            assert callable(callback)

        async def start(self) -> None:
            events.append("start")
            raise RuntimeError("synthetic start failure")

        async def stop(self) -> None:
            events.append("stop")

    async def run_ready() -> object:
        return ()

    monkeypatch.setattr(entry, "BrowserWorkerSupervisor", FailingSupervisor)
    with pytest.raises(RuntimeError, match="synthetic start failure"):
        asyncio.run(entry.run_worker(SimpleNamespace(run_ready=run_ready), asyncio.Event()))
    assert events == ["start", "stop"]


def test_worker_stops_supervisor_on_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    async def run_ready() -> object:
        return ()

    async def execute() -> None:
        started = asyncio.Event()

        class FakeSupervisor:
            last_pass_failed = False

            def __init__(self, callback: object) -> None:
                assert callback == run_ready

            async def start(self) -> None:
                events.append("start")
                started.set()

            async def stop(self) -> None:
                events.append("stop")

        monkeypatch.setattr(entry, "BrowserWorkerSupervisor", FakeSupervisor)
        task = asyncio.create_task(
            entry.run_worker(SimpleNamespace(run_ready=run_ready), asyncio.Event())
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(execute())
    assert events == ["start", "stop"]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--factory", "operator_installation:open_components"],
        ["--enable"],
        ["--enable", "--factory", "../operator.py:open_components"],
        ["--enable", "--factory", "C:\\operator.py:open_components"],
        ["--enable", "--factory", "https://example.invalid/install"],
        ["--enable", "--factory", "module:factory()"],
        ["--enable", "--factory", "module:factory", "unexpected-private-argument"],
        ["--help"],
    ],
)
def test_disabled_or_invalid_start_never_imports_factory(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    imported: list[str] = []

    def forbidden_import(name: str) -> None:
        imported.append(name)
        raise RuntimeError("synthetic import guard")

    monkeypatch.setattr(entry.importlib, "import_module", forbidden_import)
    assert entry.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "browser_worker_unavailable"
    assert "unexpected-private-argument" not in captured.err
    assert imported == []


def test_operator_factory_context_is_used_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FakeComponents:
        pass

    @asynccontextmanager
    async def open_components():
        events.append("enter")
        try:
            yield FakeComponents()
        finally:
            events.append("exit")

    async def fake_run_worker(components: object, stop: asyncio.Event) -> None:
        assert isinstance(components, FakeComponents)
        assert isinstance(stop, asyncio.Event)
        events.append("run")

    module = ModuleType("operator_installation_test")
    module.open_components = open_components
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(entry, "BrowserVerticalComponents", FakeComponents)
    monkeypatch.setattr(entry, "run_worker", fake_run_worker)

    assert entry.main(["--enable", "--factory", "operator_installation_test:open_components"]) == 0
    assert events == ["enter", "run", "exit"]


@pytest.mark.parametrize("mode", ["import_failure", "not_context_manager", "wrong_components"])
def test_installation_failure_is_one_generic_nonzero_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    class FakeComponents:
        pass

    @asynccontextmanager
    async def wrong_components():
        yield object()

    module = ModuleType("operator_failure_test")
    if mode == "not_context_manager":
        module.open_components = lambda: object()
    else:
        module.open_components = wrong_components
    if mode != "import_failure":
        monkeypatch.setitem(sys.modules, module.__name__, module)
    else:
        def failed_import(name: str) -> None:
            raise RuntimeError("synthetic private diagnostic")

        monkeypatch.setattr(entry.importlib, "import_module", failed_import)
    monkeypatch.setattr(entry, "BrowserVerticalComponents", FakeComponents)

    assert entry.main(["--enable", "--factory", "operator_failure_test:open_components"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "browser_worker_unavailable"
