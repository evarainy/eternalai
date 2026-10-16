"""Local pipe worker + closed-output supervisor for d_form_flow.py."""

import asyncio
import json
import logging
import math
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

WALL_LIMIT = 1800
CANCEL_GRACE = 30
EVENT_PATTERN = re.compile(r"Local\\EAI_D_CANCEL_[0-9a-f]{32}\Z")


def python_executables(configured_python: Path) -> frozenset[Path]:
    """Recognize this Windows venv's launcher and its own base interpreter."""
    configured = configured_python.resolve()
    allowed = {configured}
    if sys.platform == "win32" and configured == Path(sys.executable).resolve():
        base = getattr(sys, "_base_executable", None)
        if isinstance(base, str) and base:
            path = Path(base)
            if path.is_absolute() and path.is_file():
                allowed.add(path.resolve())
    return frozenset(allowed)


def system_console_executable() -> Path | None:
    """Resolve Windows' console host without trusting PATH or a file basename."""
    if sys.platform != "win32":
        return None
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    kernel.GetSystemDirectoryW.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    size = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < size < len(buffer):
        return None
    path = (Path(buffer.value) / "conhost.exe").resolve()
    return path if path.is_absolute() and path.is_file() else None


def process_executable_allowed(
    executable: Path,
    parent_executable: Path | None,
    allowed: frozenset[Path],
    console: Path | None,
) -> bool:
    """Called only for descendants of this execution's verified root process."""
    return executable in allowed or (
        console is not None and executable == console and parent_executable in allowed
    )


def windows_events():
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateEventW.restype = ctypes.c_void_p
    kernel.OpenEventW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel.OpenEventW.restype = ctypes.c_void_p
    kernel.SetEvent.argtypes = [ctypes.c_void_p]
    kernel.SetEvent.restype = ctypes.c_int
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    return kernel


def cancel_run(event_name):
    # Windows TerminateProcess does not deliver Python SIGTERM. A caller must
    # set this transient, task-scoped event for graceful cancellation instead.
    if sys.platform != "win32" or not EVENT_PATTERN.fullmatch(event_name):
        return False
    kernel = windows_events()
    handle = kernel.OpenEventW(0x2, False, event_name)
    if not handle:
        return False
    try:
        return bool(kernel.SetEvent(handle))
    finally:
        kernel.CloseHandle(handle)


STAGES = set(range(1, 9))
STATUSES = {"PASS", "FAIL"}
# Kept independent of the browser runtime so the supervisor imports no browser.
CODES = {
    "OK",
    "CONFIG_FAILED",
    "SESSION_BUSY",
    "LOGIN_REQUIRED",
    "PAGE_CHANGED",
    "UNEXPECTED_DIALOG",
    "DOWNLOAD_BLOCKED",
    "EGRESS_BLOCKED",
    "SOURCE_URL_PARSE",
    "SOURCE_URL_CREDENTIALS",
    "SOURCE_URL_SCHEME",
    "SOURCE_URL_HOST",
    "SOURCE_URL_PORT",
    "SOURCE_ORIGIN_MISMATCH",
    "SOURCE_SET_CHANGED",
    "CONTROL_MISSING",
    "CONTROL_AMBIGUOUS",
    "CONTROL_CHANGED",
    "PAGE_STATE_MISMATCH",
    "ACTION_TIMEOUT",
    "OBSERVE_TIMEOUT",
    "CONNECT_TIMEOUT",
    "RESULT_MISMATCH",
    "REPORTER_MISSING",
    "REPORTER_AMBIGUOUS",
    "REPORTER_INVALID",
    "CANCELLED",
    "CLEANUP_FAILED",
    "REQUEST_GUARD_FAILED",
    "PREP_FAILED",
    "PAGE_CONTEXT_EVENT",
    "PAGE_MAIN_FRAME_NAV",
    "PAGE_CHILD_FRAME_NAV",
    "PAGE_TARGET_CLOSED",
    "PAGE_CONTEXT_SET_CHANGED",
    "PAGE_FRAME_SET_CHANGED",
    "WINDOW_PREP_FAILED",
    "WINDOW_LOOKUP_FAILED",
    "WINDOW_SET_FAILED",
    "WINDOW_VERIFY_FAILED",
}


def validate(line):
    try:
        item = json.loads(line)
        if type(item) is not dict or set(item) != {"stage", "status", "seconds", "error_code"}:
            return None
        if type(item["stage"]) is not int or item["stage"] not in STAGES:
            return None
        if item["status"] not in STATUSES or item["error_code"] not in CODES:
            return None
        value = item["seconds"]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 7200:
            return None
        if item["status"] == "PASS" and item["error_code"] != "OK":
            return None
        if item["status"] == "FAIL" and item["error_code"] == "OK":
            return None
        return item
    except BaseException:
        return None


def emit(stage, status, seconds, code):
    item = dict(stage=stage, status=status, seconds=round(seconds, 3), error_code=code)
    if validate(json.dumps(item)) is None:
        raise ValueError("CONFIG_FAILED")
    os.write(1, (json.dumps(item) + "\n").encode())


def guard_environment():
    essential = {
        k: os.environ[k]
        for k in ("SystemRoot", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP")
        if k in os.environ
    }
    os.environ.clear()
    os.environ.update(essential)
    os.environ.update(
        PYTHON_DOTENV_DISABLED="1", OTEL_SDK_DISABLED="true", ANONYMIZED_TELEMETRY="false"
    )
    logging.disable(logging.CRITICAL)

    class Sink:
        def write(self, text):
            return len(text)

        def flush(self):
            pass

        def isatty(self):
            return False

    sys.stdout = Sink()
    sys.stderr = Sink()


async def worker(request_path, event_name):
    # This file name is a local receipt destination; the request itself arrives
    # on stdin. Never persist parameters, bindings, names or exception details.
    guard_environment()
    from d_form_flow import CODES as runtime_codes
    from d_form_flow import Flow, Stop

    if runtime_codes != CODES:
        emit(1, "FAIL", 0, "CONFIG_FAILED")
        return 2
    cancel = asyncio.Event()
    loop = asyncio.get_running_loop()
    watcher = None
    event_handle = None
    kernel = None
    if sys.platform == "win32":
        kernel = windows_events()
        event_handle = kernel.OpenEventW(0x100000, False, event_name)
        if not event_handle:
            emit(1, "FAIL", 0, "CONFIG_FAILED")
            return 2

        async def watch():
            while not cancel.is_set():
                state = kernel.WaitForSingleObject(event_handle, 0)
                if state == 0:
                    cancel.set()
                    return
                if state != 258:
                    cancel.set()
                    return
                await asyncio.sleep(0.1)

        watcher = asyncio.create_task(watch())

    def stop(*_):
        loop.call_soon_threadsafe(cancel.set)

    for code in (signal.SIGINT, signal.SIGTERM):
        signal.signal(code, stop)
    try:
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768:
            emit(1, "FAIL", 0, "CONFIG_FAILED")
            return 2
        request = json.loads(raw)
        flow = Flow(request, emit, cancel)
        # Python worker sockets can only reach its task-owned loopback CDP or
        # its own asyncio listener. No model service is on this allowance.
        import psutil

        def audit(event, args):
            if event == "socket.connect":
                address = args[1]
                if isinstance(address, tuple) and address[:2] == ("127.0.0.1", flow.binding.port):
                    return
                if (
                    isinstance(address, tuple)
                    and address[0] == "127.0.0.1"
                    and any(
                        c.status == psutil.CONN_LISTEN and (c.laddr.ip, c.laddr.port) == address[:2]
                        for c in psutil.Process().net_connections(kind="tcp")
                    )
                ):
                    return
                raise RuntimeError("EGRESS_BLOCKED")
            if event == "socket.getaddrinfo" and args[0] not in (None, "127.0.0.1"):
                raise RuntimeError("EGRESS_BLOCKED")

        sys.addaudithook(audit)
        emit(1, "PASS", 0, "OK")
        summary = await flow.run()
        with Path(request_path).open("x", encoding="utf-8") as output:
            output.write(json.dumps(summary))
        return 0 if summary["passed"] else 2
    except Stop as error:
        emit(1, "FAIL", 0, error.code)
        return 2
    except BaseException:
        emit(1, "FAIL", 0, "PREP_FAILED")
        return 2
    finally:
        if watcher:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        if event_handle:
            kernel.CloseHandle(event_handle)


def supervisor(receipt_path, request_bytes, cancel_event_name=None):
    if len(request_bytes) > 32768:
        emit(1, "FAIL", 0, "CONFIG_FAILED")
        return 2
    receipt_path = Path(receipt_path)
    if receipt_path.suffix != ".json" or receipt_path.exists() or not receipt_path.parent.is_dir():
        emit(1, "FAIL", 0, "CONFIG_FAILED")
        return 2
    child = None
    kernel = None
    event_handle = None
    event_name = ""
    owned = {}
    cancelled_at = None
    import importlib.util

    import psutil

    spec = importlib.util.find_spec("playwright")
    node = Path(spec.origin).parent / "driver" / ("node.exe" if sys.platform == "win32" else "node")
    allowed_executables = python_executables(Path(sys.executable)) | {node.resolve()}
    console = system_console_executable()

    def remember():
        if child is None or child.poll() is not None:
            return
        try:
            parent = psutil.Process(child.pid)
            if child.pid not in owned or parent.create_time() != owned[child.pid][0]:
                return
            for process in parent.children(recursive=True):
                try:
                    executable = Path(process.exe()).resolve()
                    direct_parent = process.parent() if executable == console else None
                    parent_executable = (
                        Path(direct_parent.exe()).resolve()
                        if direct_parent is not None
                        and direct_parent.create_time() <= process.create_time()
                        else None
                    )
                    if process_executable_allowed(
                        executable,
                        parent_executable,
                        allowed_executables,
                        console,
                    ):
                        owned[process.pid] = (process.create_time(), executable)
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass

    def reclaim():
        clean = True
        stopped = []
        # Only recorded descendants of this exact worker, with identical
        # creation time and Python/Playwright Node executable, can be killed.
        for pid, (created, executable) in reversed(list(owned.items())):
            try:
                process = psutil.Process(pid)
                if process.create_time() != created or Path(process.exe()).resolve() != executable:
                    clean = False
                    continue
                process.kill()
                stopped.append(process)
            except psutil.NoSuchProcess:
                pass
            except BaseException:
                clean = False
        if stopped:
            _, alive = psutil.wait_procs(stopped, timeout=5)
            if alive:
                clean = False
        return clean

    def request_cancel():
        if event_handle:
            return bool(kernel.SetEvent(event_handle))
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGTERM)
        return True

    def cancel_and_wait(deadline=None):
        nonlocal cancelled_at
        if cancelled_at is None:
            cancelled_at = time.perf_counter()
        if deadline is None:
            deadline = cancelled_at + CANCEL_GRACE
        clean = True
        try:
            remember()
            clean = request_cancel()
            if child is not None:
                until = deadline
                while child.poll() is None and time.perf_counter() < until:
                    remember()
                    time.sleep(0.1)
                if child.poll() is None:
                    clean = False
        except BaseException:
            clean = False
        # Reclaim any proven helper/driver left behind even if worker exited.
        return reclaim() and clean

    try:
        if sys.platform == "win32":
            import ctypes

            kernel = windows_events()
            event_name = cancel_event_name or "Local\\EAI_D_CANCEL_" + uuid.uuid4().hex
            if not EVENT_PATTERN.fullmatch(event_name):
                emit(1, "FAIL", 0, "CONFIG_FAILED")
                return 2
            event_handle = kernel.CreateEventW(None, True, False, event_name)
            if not event_handle or ctypes.get_last_error() == 183:
                emit(1, "FAIL", 0, "CONFIG_FAILED")
                return 2
        elif cancel_event_name is not None:
            emit(1, "FAIL", 0, "CONFIG_FAILED")
            return 2

        def signal_cancel(*_):
            raise KeyboardInterrupt()

        previous = {
            code: signal.signal(code, signal_cancel) for code in (signal.SIGINT, signal.SIGTERM)
        }
        child = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(Path(__file__).resolve()),
                "--worker",
                str(receipt_path),
                event_name,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        process = psutil.Process(child.pid)
        owned[child.pid] = (process.create_time(), Path(process.exe()).resolve())
        messages = queue.Queue(maxsize=32)

        def io():
            try:
                child.stdin.write(request_bytes)
                child.stdin.close()
                for line in child.stdout:
                    item = validate(line.decode("utf-8", errors="replace"))
                    if item is not None:
                        messages.put(item)
            except BaseException:
                pass
            finally:
                messages.put(None)

        threading.Thread(target=io, daemon=True).start()
        last = None
        start = time.perf_counter()
        while True:
            remember()
            if (
                event_handle
                and kernel.WaitForSingleObject(event_handle, 0) == 0
                and cancelled_at is None
            ):
                cancelled_at = time.perf_counter()
            if (
                time.perf_counter() - start >= WALL_LIMIT
                or cancelled_at is not None
                and time.perf_counter() - cancelled_at >= CANCEL_GRACE
            ):
                deadline = cancelled_at + CANCEL_GRACE if cancelled_at is not None else None
                clean = cancel_and_wait(deadline)
                if not clean:
                    emit(7, "FAIL", 0, "CLEANUP_FAILED")
                emit(
                    8,
                    "FAIL",
                    time.perf_counter() - start,
                    "CANCELLED" if clean else "CLEANUP_FAILED",
                )
                return 2
            try:
                item = messages.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                break
            last = item
            emit(
                **dict(
                    stage=item["stage"],
                    status=item["status"],
                    seconds=item["seconds"],
                    code=item["error_code"],
                )
            )
        code = child.wait(timeout=5)
        if not reclaim():
            emit(7, "FAIL", 0, "CLEANUP_FAILED")
            emit(8, "FAIL", time.perf_counter() - start, "CLEANUP_FAILED")
            return 2
        return (
            0
            if code == 0 and last is not None and last["stage"] == 8 and last["status"] == "PASS"
            else 2
        )
    except BaseException:
        # Ctrl+C sets the local event and then reclaims only proven descendants.
        deadline = cancelled_at + CANCEL_GRACE if cancelled_at is not None else None
        clean = child is None or cancel_and_wait(deadline)
        if not clean:
            emit(7, "FAIL", 0, "CLEANUP_FAILED")
        emit(8, "FAIL", 0, "CANCELLED" if clean else "CLEANUP_FAILED")
        return 2
    finally:
        if event_handle:
            kernel.CloseHandle(event_handle)
        for code, handler in locals().get("previous", {}).items():
            signal.signal(code, handler)


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        return asyncio.run(worker(sys.argv[2], sys.argv[3]))
    if len(sys.argv) == 3 and sys.argv[1] == "--cancel":
        result = cancel_run(sys.argv[2])
        emit(7, "PASS" if result else "FAIL", 0, "OK" if result else "CONFIG_FAILED")
        return 0 if result else 2
    if (
        len(sys.argv) not in (3, 5)
        or sys.argv[1] != "--receipt"
        or len(sys.argv) == 5
        and sys.argv[3] != "--cancel-event"
    ):
        emit(1, "FAIL", 0, "CONFIG_FAILED")
        return 2
    try:
        return supervisor(
            Path(sys.argv[2]).resolve(),
            sys.stdin.buffer.read(32769),
            sys.argv[4] if len(sys.argv) == 5 else None,
        )
    except BaseException:
        emit(1, "FAIL", 0, "PREP_FAILED")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
