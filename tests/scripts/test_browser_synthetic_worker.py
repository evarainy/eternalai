"""Worker entry tests use synthetic components and no production installation."""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace

import pytest

from scripts import run_browser_synthetic_worker as entry


async def _cleanup_ready() -> object:
    return ()


async def _start_resources() -> None:
    return None


async def _stop_resources() -> None:
    return None


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

            def __init__(
                self, callback: object, *, cleanup_ready: object,
                start_resources: object, stop_resources: object,
            ) -> None:
                assert callback == run_ready
                assert cleanup_ready == _cleanup_ready
                assert start_resources == _start_resources
                assert stop_resources == _stop_resources

            async def start(self) -> None:
                events.append("start")
                stop.set()

            async def stop(self) -> None:
                events.append("stop")

        monkeypatch.setattr(entry, "BrowserWorkerSupervisor", FakeSupervisor)
        await entry.run_worker(SimpleNamespace(
            run_business_ready=run_ready, cleanup_ready=_cleanup_ready,
            start_resources=_start_resources, stop_resources=_stop_resources,
        ), stop)

    asyncio.run(execute())
    assert events == ["start", "stop"]


def test_worker_does_not_report_success_after_failed_supervisor_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FailedPassSupervisor:
        last_pass_failed = False

        def __init__(
            self, callback: object, *, cleanup_ready: object,
            start_resources: object, stop_resources: object,
        ) -> None:
            assert callback == run_ready
            assert cleanup_ready == _cleanup_ready
            assert start_resources == _start_resources
            assert stop_resources == _stop_resources
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
        await entry.run_worker(SimpleNamespace(
            run_business_ready=run_ready, cleanup_ready=_cleanup_ready,
            start_resources=_start_resources, stop_resources=_stop_resources,
        ), stop)

    monkeypatch.setattr(entry, "BrowserWorkerSupervisor", FailedPassSupervisor)
    with pytest.raises(ValueError):
        asyncio.run(execute())
    assert events == ["start", "stop"]


def test_worker_stops_supervisor_when_start_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class FailingSupervisor:
        def __init__(
            self, callback: object, *, cleanup_ready: object,
            start_resources: object, stop_resources: object,
        ) -> None:
            assert callback == run_ready
            assert cleanup_ready == _cleanup_ready
            assert start_resources == _start_resources
            assert stop_resources == _stop_resources

        async def start(self) -> None:
            events.append("start")
            raise RuntimeError("synthetic start failure")

        async def stop(self) -> None:
            events.append("stop")

    async def run_ready() -> object:
        return ()

    monkeypatch.setattr(entry, "BrowserWorkerSupervisor", FailingSupervisor)
    with pytest.raises(RuntimeError, match="synthetic start failure"):
        asyncio.run(entry.run_worker(SimpleNamespace(
            run_business_ready=run_ready, cleanup_ready=_cleanup_ready,
            start_resources=_start_resources, stop_resources=_stop_resources,
        ), asyncio.Event()))
    assert events == ["start", "stop"]


def test_worker_stops_supervisor_on_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    async def run_ready() -> object:
        return ()

    async def execute() -> None:
        started = asyncio.Event()

        class FakeSupervisor:
            last_pass_failed = False

            def __init__(
                self, callback: object, *, cleanup_ready: object,
                start_resources: object, stop_resources: object,
            ) -> None:
                assert callback == run_ready
                assert cleanup_ready == _cleanup_ready
                assert start_resources == _start_resources
                assert stop_resources == _stop_resources

            async def start(self) -> None:
                events.append("start")
                started.set()

            async def stop(self) -> None:
                events.append("stop")

        monkeypatch.setattr(entry, "BrowserWorkerSupervisor", FakeSupervisor)
        task = asyncio.create_task(
            entry.run_worker(SimpleNamespace(
                run_business_ready=run_ready, cleanup_ready=_cleanup_ready,
                start_resources=_start_resources, stop_resources=_stop_resources,
            ), asyncio.Event())
        )
        started_waiter = asyncio.create_task(started.wait())
        try:
            done, _ = await asyncio.wait(
                (task, started_waiter), timeout=2, return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                await task
                pytest.fail("worker exited before cancellation")
            assert started_waiter in done, "supervisor did not start within the timeout"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=2)
        finally:
            for pending in (task, started_waiter):
                if not pending.done():
                    pending.cancel()
            _, pending = await asyncio.wait((task, started_waiter), timeout=2)
            assert not pending, "worker test tasks did not stop within the timeout"
            await asyncio.gather(task, started_waiter, return_exceptions=True)

    asyncio.run(execute())
    assert events == ["start", "stop"]


@pytest.mark.parametrize("outcome", ["stop", "cancel", "business_failure", "cleanup_failure"])
def test_worker_settles_both_lanes_with_actual_supervisor(outcome: str) -> None:
    calls: list[str] = []

    async def execute() -> None:
        business_started, cleanup_started, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def start_resources() -> None:
            calls.append("start_resources")

        async def stop_resources() -> None:
            calls.append("stop_resources")

        async def business() -> object:
            calls.append("business")
            business_started.set()
            if outcome == "business_failure":
                raise RuntimeError("synthetic business failure")
            return ()

        async def cleanup() -> object:
            calls.append("cleanup")
            cleanup_started.set()
            if outcome == "cleanup_failure":
                raise RuntimeError("synthetic cleanup failure")
            return ()

        task = asyncio.create_task(entry.run_worker(SimpleNamespace(
            run_business_ready=business, cleanup_ready=cleanup,
            start_resources=start_resources, stop_resources=stop_resources,
        ), stop))
        readiness = asyncio.gather(business_started.wait(), cleanup_started.wait())
        try:
            await asyncio.wait_for(readiness, timeout=2)
            if outcome == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=2)
            else:
                stop.set()
                if outcome.endswith("failure"):
                    with pytest.raises(entry._StartupError):
                        await asyncio.wait_for(task, timeout=2)
                else:
                    await asyncio.wait_for(task, timeout=2)
        finally:
            for pending in (task, readiness):
                if not pending.done():
                    pending.cancel()
            _, pending = await asyncio.wait((task, readiness), timeout=2)
            assert not pending, "actual supervisor did not settle within the timeout"
            await asyncio.gather(task, readiness, return_exceptions=True)
        assert not any(
            pending.get_name() in {"browser-readonly-worker", "browser-readonly-cleanup"}
            for pending in asyncio.all_tasks()
        )

    asyncio.run(execute())
    assert calls == ["start_resources", "business", "cleanup", "stop_resources"]


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
