"""Bounded D-style Windows Chrome/CDP launch for the task-owned read fixture.

No Browser Use Agent, remote model, login, restore, upload or Linux attestation.
Process metadata is collected only for this Run's created root and descendants.
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import hmac
import importlib
import json
import os
import re
import socket
import subprocess
import threading
import time
from _thread import LockType
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from app.browser_skill.executor import DecisionContextFactory
from app.browser_skill.models import (
    ActionCommand,
    BrowserSessionRef,
    BrowserSkill,
    DecisionCallContext,
    DecisionRequest,
    DecisionSource,
    DispatchPermit,
    ExecutionContext,
    ParameterPurpose,
    ParameterRef,
    ReadSpec,
    ScopeBinding,
    SealedParameter,
    TargetRef,
)
from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.browser_skill.verifier import failure
from app.infra.browser.chat_read_fixture import (
    CHAT_READ_TENANT,
    CHAT_READ_USER,
    ChatReadFixture,
    ChatReadHTTPFixture,
    QueryBinding,
    QueryLedger,
    admitted_arguments,
    make_project_output,
    make_read_spec_resolver,
)
from app.infra.browser.playwright_actions import LiveBrowser
from app.infra.browser.playwright_observer import PlaywrightObserver
from app.infra.browser.playwright_web_adapter import OperationAuthority
from app.infra.browser.read_execution import RegisteredReadExecution
from app.infra.persistence.browser.crypto import BrowserClaimProofContext
from app.ports.browser_read_execution import (
    BrowserCaptureResolution,
    BrowserReadExecutionError,
    BrowserWorkerCheckpoint,
)
from app.ports.browser_run_store import RunCleanup, RunSnapshot
from app.ports.browser_store import (
    BrowserCleanupAuthorityPort,
    BrowserLeaseClaim,
    BrowserLeaseError,
    BrowserLeaseStorePort,
    BrowserProviderExpectation,
    BrowserProviderFact,
)
from app.ports.credential_vault import BrowserAuthFact, BrowserBindingFact


@dataclass(frozen=True, slots=True, repr=False)
class DWindowsQueryOptions:
    chromium: Path
    chromium_sha256: str
    runs_root: Path
    provider_key: str = "chat_read_windows"

    def manifest(self, source: ChatReadFixture) -> bytes:
        if (
            len(self.chromium_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.chromium_sha256)
            or not self.chromium.is_absolute()
            or not self.runs_root.is_absolute()
            or re.fullmatch(r"[A-Za-z0-9_-]{1,96}", self.provider_key) is None
        ):
            raise ValueError("windows_query_configuration_invalid")
        return json.dumps(
            {
                "schema_version": "browser.windows.task_query.v1",
                "platform": "windows",
                "transport": "cdp",
                "binary_sha256": self.chromium_sha256,
                "fixture_digest": source.manifest.site.source.fixture_digest,
                "sandbox_launch": "chromium_default_no_disable_flags",
                "windows_sandbox_attestation": False,
                "os_protocol_containment": False,
                "crash_atomic_cleanup": False,
                "profile_restore": False,
                "descendant_membership": "suspended_root_private_nonbreakaway_job_v1",
                "http_scope": "exact_loopback_query_routes",
                "execution_seconds": 90,
                "cleanup_seconds": 15,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")


def fixture_subject_digest(source: ChatReadFixture, tenant: str, user: str) -> bytes:
    return hashlib.sha256(
        json.dumps(
            [
                "task-owned-no-login-subject.v1",
                source.manifest.site.source.fixture_digest,
                tenant,
                user,
            ],
            separators=(",", ":"),
        ).encode("ascii")
    ).digest()


class _JobLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong),
        ("job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _JobIO(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")
    ]


class _ExtendedJobLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _JobLimits),
        ("io", _JobIO),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _JobAccounting(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_longlong)
        for name in ("user_time", "kernel_time", "period_user", "period_kernel")
    ] + [(name, wintypes.DWORD) for name in ("faults", "total", "active", "terminated")]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("reserved", wintypes.LPWSTR),
        ("desktop", wintypes.LPWSTR),
        ("title", wintypes.LPWSTR),
        ("x", wintypes.DWORD),
        ("y", wintypes.DWORD),
        ("width", wintypes.DWORD),
        ("height", wintypes.DWORD),
        ("x_chars", wintypes.DWORD),
        ("y_chars", wintypes.DWORD),
        ("fill", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("show", wintypes.WORD),
        ("reserved_size", wintypes.WORD),
        ("reserved_bytes", ctypes.POINTER(ctypes.c_ubyte)),
        ("stdin", wintypes.HANDLE),
        ("stdout", wintypes.HANDLE),
        ("stderr", wintypes.HANDLE),
    ]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", wintypes.HANDLE),
        ("thread", wintypes.HANDLE),
        ("pid", wintypes.DWORD),
        ("tid", wintypes.DWORD),
    ]


@dataclass(frozen=True, slots=True, repr=False)
class _NativeCleanupFact:
    job_ref: str
    root_pid: int
    root_created: int
    root_executable: str
    root_exit: int
    active: int
    members: tuple[int, ...]
    cdp_port: int
    inspected: float


class _OwnedWindowsJob:
    """One anonymous Run job and original handles; no host-job or PID-based signaling."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise BrowserReadExecutionError("unsupported")
        self.api = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        pointer, handle, dword = ctypes.c_void_p, wintypes.HANDLE, wintypes.DWORD
        signatures = {
            "CreateJobObjectW": (handle, [pointer, wintypes.LPCWSTR]),
            "SetInformationJobObject": (wintypes.BOOL, [handle, ctypes.c_int, pointer, dword]),
            "QueryInformationJobObject": (
                wintypes.BOOL,
                [handle, ctypes.c_int, pointer, dword, ctypes.POINTER(dword)],
            ),
            "GetHandleInformation": (wintypes.BOOL, [handle, ctypes.POINTER(dword)]),
            "AssignProcessToJobObject": (wintypes.BOOL, [handle, handle]),
            "IsProcessInJob": (wintypes.BOOL, [handle, handle, ctypes.POINTER(wintypes.BOOL)]),
            "CreateProcessW": (
                wintypes.BOOL,
                [
                    wintypes.LPCWSTR,
                    wintypes.LPWSTR,
                    pointer,
                    pointer,
                    wintypes.BOOL,
                    dword,
                    pointer,
                    wintypes.LPCWSTR,
                    ctypes.POINTER(_StartupInfo),
                    ctypes.POINTER(_ProcessInfo),
                ],
            ),
            "ResumeThread": (dword, [handle]),
            "TerminateProcess": (wintypes.BOOL, [handle, wintypes.UINT]),
            "TerminateJobObject": (wintypes.BOOL, [handle, wintypes.UINT]),
            "WaitForSingleObject": (dword, [handle, dword]),
            "GetExitCodeProcess": (wintypes.BOOL, [handle, ctypes.POINTER(dword)]),
            "GetProcessTimes": (
                wintypes.BOOL,
                [handle, *([ctypes.POINTER(wintypes.FILETIME)] * 4)],
            ),
            "QueryFullProcessImageNameW": (
                wintypes.BOOL,
                [handle, dword, wintypes.LPWSTR, ctypes.POINTER(dword)],
            ),
            "CloseHandle": (wintypes.BOOL, [handle]),
            "OpenProcess": (handle, [dword, wintypes.BOOL, dword]),
        }
        for name, (result, arguments) in signatures.items():
            function = getattr(self.api, name)
            function.restype, function.argtypes = result, arguments
        # Explicit sizes for the supported 32/64-bit Win32 ABI, never packed.
        sizes = (64, 144, 104, 24) if ctypes.sizeof(pointer) == 8 else (48, 112, 68, 16)
        if (
            tuple(
                ctypes.sizeof(model)
                for model in (_JobLimits, _ExtendedJobLimits, _StartupInfo, _ProcessInfo)
            )
            != sizes
        ):
            raise BrowserReadExecutionError("unsupported")
        self.lock = threading.RLock()
        self.job_ref = uuid4().hex
        self.job: int | None = None
        self.root: int | None = None
        self.primary_thread: int | None = None
        self.pid = 0
        self.created = 0
        self.executable = ""
        self.assigned = False
        self.resumed = False
        self.close_failed = False

    @staticmethod
    def _require(value: object) -> None:
        if not value:
            raise BrowserReadExecutionError("unsupported")

    def _query(self, kind: int, result: Any) -> None:
        self._require(bool(self.job))
        returned = wintypes.DWORD()
        self._require(
            self.api.QueryInformationJobObject(
                self.job, kind, ctypes.byref(result), ctypes.sizeof(result), ctypes.byref(returned)
            )
        )
        self._require(returned.value == ctypes.sizeof(result))

    def _check_limits(self) -> None:
        limits, flags = _ExtendedJobLimits(), wintypes.DWORD()
        self._query(9, limits)
        self._require(
            limits.basic.flags == 0x2000
        )  # KILL_ON_JOB_CLOSE only; neither breakaway bit.
        self._require(
            self.api.GetHandleInformation(self.job, ctypes.byref(flags)) and not flags.value & 1
        )

    def create_job(self) -> None:
        with self.lock:
            self._require(self.job is None)
            self.job = self.api.CreateJobObjectW(None, None)  # Anonymous, default non-inheritable.
            if not self.job:
                self.job = None
                raise BrowserReadExecutionError("unsupported")
            limits = _ExtendedJobLimits()
            limits.basic.flags = 0x2000
            self._require(
                self.api.SetInformationJobObject(
                    self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
            )
            self._check_limits()

    def create_root(
        self,
        binary: Path,
        arguments: list[str],
        environment: Mapping[str, str],
        cwd: Path,
        *,
        visible: bool = False,
    ) -> None:
        with self.lock:
            self._check_limits()
            self._require(self.root is None and not self.pid)
            startup, info = _StartupInfo(), _ProcessInfo()
            startup.cb, startup.flags = ctypes.sizeof(startup), 1
            startup.show = 1 if visible else 0  # Owned OA window / existing hidden fixture.
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
            env = ctypes.create_unicode_buffer(
                "\0".join(
                    name + "=" + value
                    for name, value in sorted(environment.items(), key=lambda item: item[0].upper())
                )
                + "\0\0"
            )
            self._require(
                self.api.CreateProcessW(
                    str(binary),
                    command,
                    None,
                    None,
                    False,
                    0x00000004 | 0x00000400 | 0x08000000,
                    ctypes.cast(env, ctypes.c_void_p),
                    str(cwd),
                    ctypes.byref(startup),
                    ctypes.byref(info),
                )
            )
            # Register both original handles immediately, before metadata reads or awaits.
            self.root, self.primary_thread, self.pid = info.process, info.thread, info.pid
            self._require(self.root and self.primary_thread and self.pid)
            self.created, self.executable = self._root_identity()
            self._require(self.executable == os.path.normcase(str(binary)))

    def _root_identity(self) -> tuple[int, str]:
        self._require(bool(self.root))
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        self._require(
            self.api.GetProcessTimes(
                self.root,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
        )
        text, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        self._require(self.api.QueryFullProcessImageNameW(self.root, 0, text, ctypes.byref(length)))
        self._require(0 < length.value < 32768)
        return (created.dwHighDateTime << 32) | created.dwLowDateTime, os.path.normcase(text.value)

    def bind_root(self) -> None:
        with self.lock:
            self._require(
                self.root is not None
                and self.primary_thread is not None
                and not self.assigned
                and not self.resumed
            )
            self._require(self.api.AssignProcessToJobObject(self.job, self.root))
            self.assigned = True  # A later failed check still requires stopping this owned job.
            inside = wintypes.BOOL()
            self._require(
                self.api.IsProcessInJob(self.root, self.job, ctypes.byref(inside)) and inside.value
            )
            self._check_limits()
            active, members = self.members()
            self._require(active == 1 and members == (self.pid,))

    def resume(self) -> None:
        with self.lock:
            self._require(self.assigned and not self.resumed and self.primary_thread is not None)
            self._check_limits()
            self._require(self.api.ResumeThread(self.primary_thread) == 1)
            self.resumed = True
            if not self.api.CloseHandle(self.primary_thread):
                self.close_failed = True
                raise BrowserReadExecutionError("unsupported")
            self.primary_thread = None

    def members(self) -> tuple[int, tuple[int, ...]]:
        with self.lock:
            self._check_limits()
            accounting = _JobAccounting()
            self._query(1, accounting)
            capacity = 256

            class ProcessList(ctypes.Structure):
                _fields_ = [
                    ("assigned", wintypes.DWORD),
                    ("listed", wintypes.DWORD),
                    ("pids", ctypes.c_size_t * capacity),
                ]

            result, returned = ProcessList(), wintypes.DWORD()
            self._require(
                self.api.QueryInformationJobObject(
                    self.job, 3, ctypes.byref(result), ctypes.sizeof(result), ctypes.byref(returned)
                )
            )  # ERROR_MORE_DATA is unknown, never empty.
            self._require(
                result.assigned == result.listed
                and result.listed <= capacity
                and ProcessList.pids.offset + result.listed * ctypes.sizeof(ctypes.c_size_t)
                <= returned.value
                <= ctypes.sizeof(result)
            )
            members = tuple(int(pid) for pid in result.pids[: result.listed])
            self._require(len(set(members)) == len(members) and all(pid > 0 for pid in members))
            return accounting.active, members

    def poll(self) -> int | None:
        with self.lock:
            self._require(self.root is not None)
            status = self.api.WaitForSingleObject(self.root, 0)
            if status == 258:  # WAIT_TIMEOUT, not STILL_ACTIVE-as-an-exit-code.
                return None
            self._require(status == 0)
            result = wintypes.DWORD()
            self._require(self.api.GetExitCodeProcess(self.root, ctypes.byref(result)))
            return int(result.value)

    @contextmanager
    def member_identity_scope(self, pid: int) -> Iterator[bool]:
        """Pin a census member and verify ownership before reading its metadata."""
        with self.lock:
            self._require(bool(self.job))
            borrowed = pid == self.pid
            handle = self.root if borrowed else self.api.OpenProcess(0x1000, False, pid)
            if not handle:
                self._require(pid not in self.members()[1])
                yield False
                return
            try:
                inside = wintypes.BOOL()
                self._require(self.api.IsProcessInJob(handle, self.job, ctypes.byref(inside)))
                yield bool(inside.value)
            finally:
                if not borrowed and not self.api.CloseHandle(handle):
                    self.close_failed = True
                    raise BrowserReadExecutionError("unsupported")

    def wait(self, timeout: float) -> int:
        with self.lock:
            self._require(self.root is not None)
            status = self.api.WaitForSingleObject(
                self.root, max(0, min(0xFFFFFFFE, int(timeout * 1000)))
            )
            if status == 258:
                raise BrowserReadExecutionError("timeout")
            self._require(status == 0)
            result = self.poll()
            if result is None:
                raise BrowserReadExecutionError("unsupported")
            return int(result)

    def stop_and_wait(self, deadline: float) -> None:
        # Only this private handle can be terminated. Never query the host job.
        with self.lock:
            if self.root is not None and self.poll() is None:
                if self.assigned:
                    self._require(self.api.TerminateJobObject(self.job, 1))
                else:
                    self._require(
                        self.api.TerminateProcess(self.root, 1)
                    )  # Still suspended if bind failed.
            elif self.assigned:
                self._require(
                    self.api.TerminateJobObject(self.job, 1)
                )  # Reparented survivors remain members.
        if self.root is not None:
            self.wait(max(0, deadline - time.monotonic()))
        while time.monotonic() < deadline:
            active, members = self.members()
            if active == 0 and members == ():
                return
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        raise BrowserReadExecutionError("timeout")

    def inspect_empty(self, cdp_port: int) -> _NativeCleanupFact:
        with self.lock:
            self._require(self.assigned and self.resumed and not self.close_failed)
            self._require(self._root_identity() == (self.created, self.executable))
            exit_code = self.poll()
            if exit_code is None:
                raise BrowserReadExecutionError("unsupported")
            active, members = self.members()
            self._require(active == 0 and members == ())
            return _NativeCleanupFact(
                self.job_ref,
                self.pid,
                self.created,
                self.executable,
                int(exit_code),
                active,
                members,
                cdp_port,
                time.monotonic(),
            )

    def inspect_live_root(self) -> None:
        with self.lock:
            self._require(self.assigned and self.resumed and not self.close_failed)
            self._require(
                self._root_identity() == (self.created, self.executable) and self.poll() is None
            )
            inside = wintypes.BOOL()
            self._require(
                self.api.IsProcessInJob(self.root, self.job, ctypes.byref(inside)) and inside.value
            )
            active, members = self.members()
            self._require(active > 0 and self.pid in members)

    def inspect_owned_creation_empty(self) -> None:
        """Exact private handles, also for an interrupted suspended creation.

        No fictitious root or target is returned when CreateProcess did not
        create one. Existing fixture release continues to require inspect_empty.
        """
        with self.lock:
            self._require(bool(self.job) and not self.close_failed)
            if self.root is not None:
                self._require(self._root_identity() == (self.created, self.executable))
                self._require(self.poll() is not None)
            else:
                self._require(self.pid == 0 and self.primary_thread is None)
            active, members = self.members()
            self._require(active == 0 and members == ())

    def close_handles(self) -> bool:
        with self.lock:
            # Caller must first consume live job proof, or retain quarantine.
            if self.close_failed:
                return False
            for name in ("primary_thread", "root", "job"):
                value = getattr(self, name)
                if value is not None:
                    if self.api.CloseHandle(value):
                        setattr(self, name, None)
                    else:
                        self.close_failed = True
            return self.handles_closed

    @property
    def handles_closed(self) -> bool:
        return (
            not self.close_failed
            and self.job is None
            and self.root is None
            and self.primary_thread is None
        )


@dataclass(frozen=True, slots=True, repr=False)
class _TerminationReceipt:
    run_id: str
    task_id: str
    claim: BrowserLeaseClaim
    resource_ref: bytes
    fact: _NativeCleanupFact


@dataclass(frozen=True, slots=True, repr=False)
class RunProcessIdentity:
    pid: int
    created: float
    executable: str
    parent_pid: int | None
    parent_created: float | None


@dataclass(slots=True, repr=False)
class ProcessInventory:
    module: Any
    process: _OwnedWindowsJob
    root: RunProcessIdentity
    identities: dict[int, RunProcessIdentity]
    lock: LockType = field(default_factory=threading.Lock)
    stop: threading.Event = field(default_factory=threading.Event)
    stopping: bool = False
    live_confirmed: bool = False
    errors: set[str] = field(default_factory=set)
    scans: int = 0
    last_scan: float = field(default_factory=time.monotonic)
    thread: threading.Thread | None = None


def _same_process(module: Any, identity: RunProcessIdentity) -> Any | None:
    try:
        actual = module.Process(identity.pid)
        if (
            actual.create_time() != identity.created
            or os.path.normcase(actual.exe()) != identity.executable
        ):
            raise BrowserReadExecutionError("stale")
        return actual if actual.is_running() else None
    except module.NoSuchProcess:
        return None


def collect_run_process_identities(inventory: ProcessInventory) -> None:
    """Enumerate kernel job membership, including reparented/nested-job members."""
    with inventory.lock:
        try:
            now = time.monotonic()
            if inventory.scans and now - inventory.last_scan > 0.5:
                inventory.errors.add("collection_gap")
            if inventory.process.poll() is not None and not inventory.stopping:
                inventory.errors.add("unexpected_root_exit")
            _, members = inventory.process.members()
            for pid in members:
                try:
                    with inventory.process.member_identity_scope(pid) as owned:
                        if not owned:
                            continue
                        child = inventory.module.Process(pid)
                        # Parent PID is diagnostic, not a source of membership;
                        # never open a possibly unrelated/reused parent process.
                        entry = RunProcessIdentity(
                            pid,
                            child.create_time(),
                            os.path.normcase(child.exe()),
                            child.ppid(),
                            None,
                        )
                except inventory.module.NoSuchProcess:
                    # The kernel job still accounts for a short-lived member;
                    # only a fresh complete empty-job check can prove cleanup.
                    if pid in inventory.process.members()[1]:
                        raise BrowserReadExecutionError("stale") from None
                    continue
                known = inventory.identities.get(pid)
                if known is not None and (known.created, known.executable) != (
                    entry.created,
                    entry.executable,
                ):
                    raise BrowserReadExecutionError("stale")
                inventory.identities.setdefault(pid, entry)
            if len(inventory.identities) > 256:
                raise BrowserReadExecutionError("unsupported")
            inventory.scans += 1
            inventory.last_scan = now
            inventory.live_confirmed = not inventory.errors
        except Exception:
            inventory.errors.add("inventory_unconfirmed")
            inventory.live_confirmed = False


def _collect_continuously(inventory: ProcessInventory) -> None:
    while not inventory.stop.is_set():
        collect_run_process_identities(inventory)
        inventory.stop.wait(0.05)


def stop_and_wait(inventory: ProcessInventory, deadline: float) -> None:
    """Stop the Run's kernel membership, then wait on the original root handle."""
    inventory.stopping = True
    try:
        collect_run_process_identities(inventory)
        inventory.process.stop_and_wait(deadline)
    except Exception:
        inventory.errors.add("stop_unconfirmed")
    finally:
        collect_run_process_identities(inventory)
        inventory.stop.set()
        if inventory.thread is not None:
            inventory.thread.join(timeout=max(0, deadline - time.monotonic()))
        if inventory.thread is None or inventory.thread.is_alive():
            inventory.errors.add("collector_exit_unconfirmed")
        inventory.live_confirmed = inventory.live_confirmed and not inventory.errors


def _stop_created_handle(process: _OwnedWindowsJob, deadline: float) -> None:
    """Also covers the original still-suspended root before a failed assignment."""
    process.stop_and_wait(deadline)


def verify_cleanup(
    inventory: ProcessInventory, cdp_port: int, fixture: ChatReadHTTPFixture
) -> _NativeCleanupFact | None:
    """Separate fresh process/port/thread reads; requested stop is never evidence."""
    try:
        if (
            inventory.errors
            or inventory.scans < 2
            or inventory.thread is None
            or inventory.thread.is_alive()
            or inventory.process.poll() is None
            or not fixture.closed()
        ):
            return None
        # No PID reopening after stop: the original root handle and the entire
        # private kernel job, including unsampled members, are the exit basis.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            known_closed = cdp_port > 0 and probe.connect_ex(("127.0.0.1", cdp_port)) != 0
        return inventory.process.inspect_empty(cdp_port) if known_closed else None
    except Exception:
        return None


@dataclass(slots=True, repr=False)
class _RunBrowser:
    run: RunSnapshot
    checkpoint: BrowserWorkerCheckpoint
    arguments: dict[str, str]
    deadline: float
    resource_ref: bytes = field(default_factory=lambda: uuid4().hex.encode("ascii"))
    claim: BrowserLeaseClaim | None = None
    session: BrowserSessionRef | None = None
    live: LiveBrowser | None = None
    page: Any = None
    observer: PlaywrightObserver | None = None
    fixture: ChatReadHTTPFixture | None = None
    query_binding: QueryBinding | None = None
    inventory: ProcessInventory | None = None
    profile: Path | None = None
    process: _OwnedWindowsJob | None = None
    cdp_port: int = 0
    acquired: bool = False
    closed: bool = False
    cleanup_proven: bool = False
    cleanup_status: RunCleanup = "pending"
    cleanup_deadline: float | None = None
    cleanup_task: asyncio.Task[_NativeCleanupFact | None] | None = field(default=None, repr=False)
    startup_abort_task: asyncio.Task[RunCleanup] | None = field(default=None, repr=False)
    cleanup_abandoned: bool = False
    terminal_receipt: _TerminationReceipt | None = field(default=None, repr=False)
    cancellation: asyncio.Event = field(default_factory=asyncio.Event)
    dispatch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    lease_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    publication_guard: Callable[[RunSnapshot], Awaitable[None]] | None = None
    operation_context: ExecutionContext | None = None
    read_spec: ReadSpec | None = None
    acquisition_evidence: bytes | None = None
    termination_evidence: bytes | None = None


class DWindowsQueryFactory:
    def __init__(
        self,
        source: ChatReadFixture,
        binding: BrowserBindingFact,
        options: DWindowsQueryOptions,
        proof_context: BrowserClaimProofContext,
        request_lookup: Callable[[RunSnapshot], Awaitable[str]],
        driver: Any,
    ) -> None:
        if (
            binding.tenant_id != CHAT_READ_TENANT
            or binding.ai_user_id != CHAT_READ_USER
            or binding.target_system != "oa"
            or binding.subject_digest
            != fixture_subject_digest(source, binding.tenant_id, binding.ai_user_id)
        ):
            raise ValueError("windows_query_binding_invalid")
        self.source, self.binding, self.options = source, binding, options
        self.proof_context, self.request_lookup = proof_context, request_lookup
        self.driver = driver  # Task-host owned; never a Run-owned Playwright subprocess.
        self.manifest_digest = hashlib.sha256(options.manifest(source)).digest()
        self.ledger = QueryLedger()
        self._states: dict[str, _RunBrowser] = {}
        self._leases: BrowserLeaseStorePort | None = None
        self._lifecycle: DWindowsQueryLifecycle | None = None

    def install_leases(self, leases: BrowserLeaseStorePort) -> None:
        if self._leases is not None:
            raise ValueError("windows_query_leases_already_installed")
        self._leases = leases

    def _state(self, run: RunSnapshot) -> _RunBrowser:
        state = self._states.get(run.run_id)
        if state is None or state.run.admission != run.admission:
            raise BrowserReadExecutionError("stale")
        return state

    async def _fence(self, state: _RunBrowser) -> None:
        if state.closed or state.cancellation.is_set():
            raise BrowserReadExecutionError("cancelled")
        if time.monotonic() >= state.deadline:
            raise BrowserReadExecutionError("timeout")
        before = state.run
        current = await state.checkpoint.refresh()
        if (
            current.admission != before.admission
            or current.worker_epoch != before.worker_epoch
            or current.worker_id != before.worker_id
            or current.cancel_requested
            or current.status != "running"
        ):
            raise BrowserReadExecutionError("stale")
        state.run = current
        if state.publication_guard is not None:
            await state.publication_guard(current)
        if state.claim is not None:
            if self._leases is None:
                raise BrowserReadExecutionError("unavailable")
            async with state.lease_lock:
                state.claim = await self._leases.renew(state.claim, ttl_seconds=120)
                if state.acquired:
                    actual = await self._leases.authorize_resource(state.claim)
                    if not hmac.compare_digest(actual, state.resource_ref):
                        raise BrowserReadExecutionError("stale")

    async def open(
        self,
        run: RunSnapshot,
        manifest: BrowserPublicationManifest,
        private_input: Mapping[str, object],
        checkpoint: BrowserWorkerCheckpoint,
    ) -> RegisteredReadExecution:
        if (
            os.name != "nt"
            or self._leases is None
            or self._lifecycle is None
            or manifest != self.source.manifest
            or run.run_id in self._states
            or run.lease_epoch is not None
            or run.owner.tenant_id != self.binding.tenant_id
            or run.owner.user_id != self.binding.ai_user_id
            or run.admission.binding_id != self.binding.binding_id
            or run.admission.binding_revision != self.binding.binding_revision
            or run.admission.target_system != "oa"
            or run.admission.publication_digest.hex() != manifest.digest
        ):
            raise BrowserReadExecutionError("denied")
        arguments = admitted_arguments(private_input, manifest)
        state = _RunBrowser(run, checkpoint, arguments, time.monotonic() + 90)
        self._states[run.run_id] = state  # Ownership precedes every allocation/await.
        try:
            async with asyncio.timeout_at(state.deadline):
                await self._fence(state)
                await self._preflight()
                await self._fence(state)
                auth = BrowserAuthFact(
                    run.owner,
                    None,
                    run.admission.auth_fingerprint,
                    run.admission.auth_expires_at,
                    run.run_id,
                    "verified-session-v1",
                )
                state.claim = await self._leases.reserve(
                    auth, self.binding, self.options.provider_key, ttl_seconds=120
                )
                await self._fence(state)
                state.run = await checkpoint.attach_lease(
                    provider_key=self.options.provider_key,
                    provider_manifest_digest=self.manifest_digest,
                    lease_epoch=state.claim.lease_epoch,
                )
                state.claim = await self._leases.start_acquisition(state.claim)
                await self._fence(state)
                scope = ScopeBinding(
                    owner=run.owner,
                    binding_id=self.binding.binding_id,
                    binding_revision=self.binding.binding_revision,
                    lease_epoch=state.claim.lease_epoch,
                    authorization_run_id=run.run_id,
                    evidence_version="verified-session-v1",
                )
                state.session = BrowserSessionRef(session_ref=uuid4().hex, binding=scope)
                request_id = await self.request_lookup(state.run)
                await self._fence(state)
                state.query_binding = self.ledger.bind_run(state.run, request_id, arguments)
                state.fixture = ChatReadHTTPFixture(self.source, self.ledger, state.query_binding)
                await asyncio.to_thread(state.fixture.start)
                await self._fence(state)
                await self._launch(state)
                await self._fence(state)
                state.acquisition_evidence = self.proof_context.digest(
                    "windows-query-acquired.v1", state.resource_ref
                )
                state.claim = await self._leases.record_acquired(
                    state.claim, state.acquisition_evidence
                )
                state.acquired = True
                await self._fence(state)
                context = self._context(state)
                observer = PlaywrightObserver(
                    self, {self.source.region.region_id: self.source.region}
                )
                state.observer = observer
                await observer.register_page(state.session, state.page)
                await self._fence(state)
                resolver = make_read_spec_resolver(state.run, private_input, scope, manifest)
                spec = await resolver(state.session, context, scope)
                state.read_spec = spec

                def bind_publication(
                    guard: Callable[[RunSnapshot], Awaitable[None]],
                ) -> ExecutionContext:
                    if state.publication_guard is not None:
                        raise BrowserReadExecutionError("denied")
                    state.publication_guard = guard
                    return context

                def bind_authority(bound: ExecutionContext) -> OperationAuthority:
                    if (
                        state.publication_guard is None
                        or state.operation_context is not None
                        or bound.execution_id != context.execution_id
                        or bound.expected_binding != scope
                        or bound.cancellation is not state.cancellation
                    ):
                        raise failure("denied")
                    state.operation_context = bound

                    async def authority(
                        session: BrowserSessionRef,
                        actual: ExecutionContext,
                        subject: ActionCommand | ReadSpec,
                    ) -> LiveBrowser:
                        if actual is not bound:
                            raise failure("denied")
                        self._check_subject(state, session, actual.skill, subject, scope)
                        return await self.resolve_live(session)

                    return authority

                return RegisteredReadExecution(
                    state.session,
                    context,
                    spec.business_key,
                    self,
                    observer,
                    (self.source.rules,),
                    self.source.site,
                    None,
                    resolver,
                    self._decisions(state),
                    make_project_output(
                        state.run, private_input, state.query_binding, self.ledger, manifest
                    ),
                    bind_publication_guard=bind_publication,
                    bind_operation_authority=bind_authority,
                )
        except BaseException as error:
            # Creation failures/cancellation must own stop/wait even before the
            # worker's finally. A second cancellation cannot abandon that task.
            state.startup_abort_task = asyncio.create_task(self._lifecycle.cleanup(state.run))
            try:
                async with asyncio.timeout_at(state.deadline):
                    await asyncio.shield(state.startup_abort_task)
            except BaseException:
                pass  # Cleanup retains its own bounded task and truthful quarantine.
            if isinstance(error, (asyncio.CancelledError, BrowserReadExecutionError)):
                raise
            if isinstance(error, TimeoutError):
                raise BrowserReadExecutionError("timeout") from None
            raise BrowserReadExecutionError("unavailable") from None

    async def _preflight(self) -> None:
        if any(
            os.environ.get(name) for name in ("DEBUG", "PWDEBUG", "SSLKEYLOGFILE", "NODE_OPTIONS")
        ):
            raise BrowserReadExecutionError("unsupported")
        try:
            importlib.import_module("psutil")
        except ImportError:
            raise BrowserReadExecutionError("unsupported") from None
        binary = self.options.chromium.resolve(strict=True)
        if (
            not binary.is_file()
            or hashlib.sha256(await asyncio.to_thread(binary.read_bytes)).hexdigest()
            != self.options.chromium_sha256
        ):
            raise BrowserReadExecutionError("unsupported")
        if self.driver is None or not callable(
            getattr(self.driver.chromium, "connect_over_cdp", None)
        ):
            raise BrowserReadExecutionError("unsupported")

    async def _launch(self, state: _RunBrowser) -> None:
        session = state.session
        if session is None:
            raise BrowserReadExecutionError("denied")
        processes = importlib.import_module("psutil")
        binary = self.options.chromium.resolve(strict=True)
        run_root = self.options.runs_root.resolve() / ("run-" + uuid4().hex)
        run_root.mkdir(parents=True, exist_ok=False)
        profile = run_root / "profile"
        profile.mkdir()
        state.profile = profile
        for child in ("temp", "cache", "browser-cache"):
            (run_root / child).mkdir()
        env = {
            name: os.environ[name]
            for name in ("SystemRoot", "WINDIR", "COMSPEC")
            if name in os.environ
        }
        env.update(
            TEMP=str(run_root / "temp"),
            TMP=str(run_root / "temp"),
            LOCALAPPDATA=str(run_root / "cache"),
        )
        arguments = [
            str(binary),
            "--headless=new",
            "--disable-gpu",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--user-data-dir=" + str(profile),
            "--disk-cache-dir=" + str(run_root / "browser-cache"),
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-sync",
            "--disable-extensions",
            "--disable-breakpad",
            "--disable-crash-reporter",
            "--no-proxy-server",
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
            "about:blank",
        ]
        process = _OwnedWindowsJob()
        state.process = process
        process.create_job()
        process.create_root(binary, arguments, env, run_root)
        await self._fence(state)  # Root remains suspended, including cancellation here.
        process.bind_root()
        # Diagnostic metadata is matched to the original native root before its
        # first instruction. Job membership supplies the independent coverage.
        root = processes.Process(process.pid)
        identity = RunProcessIdentity(
            root.pid, root.create_time(), os.path.normcase(root.exe()), None, None
        )
        if identity.executable != process.executable:
            raise BrowserReadExecutionError("denied")
        inventory = ProcessInventory(processes, process, identity, {identity.pid: identity})
        state.inventory = inventory
        collect_run_process_identities(inventory)
        inventory.thread = threading.Thread(
            target=_collect_continuously,
            args=(inventory,),
            name="chat-read-process-collector",
            daemon=False,
        )
        inventory.thread.start()
        await self._fence(state)
        process.resume()
        active = profile / "DevToolsActivePort"
        startup_deadline = min(state.deadline, time.monotonic() + 25)
        while not active.is_file():
            if process.poll() is not None or time.monotonic() >= startup_deadline:
                raise BrowserReadExecutionError("timeout")
            await asyncio.sleep(0.05)
            await self._fence(state)
        lines = active.read_text(encoding="utf-8").splitlines()
        if (
            len(lines) != 2
            or not lines[0].isdigit()
            or not lines[1].startswith("/devtools/browser/")
        ):
            raise BrowserReadExecutionError("invalid_response")
        state.cdp_port = int(lines[0])
        if (
            not 1 <= state.cdp_port <= 65535
            or not any(
                connection.status == "LISTEN"
                and connection.laddr.ip == "127.0.0.1"
                and connection.laddr.port == state.cdp_port
                for connection in root.net_connections(kind="tcp")
            )
            or "--user-data-dir=" + str(profile) not in root.cmdline()
            or os.path.normcase(str(binary)) != identity.executable
        ):
            raise BrowserReadExecutionError("denied")
        state.live = LiveBrowser(session, None, None)
        browser = await self.driver.chromium.connect_over_cdp(
            "http://127.0.0.1:" + str(state.cdp_port),
            timeout=min(5000, max(1, int((state.deadline - time.monotonic()) * 1000))),
        )
        state.live.browser = browser
        if len(browser.contexts) != 1:
            raise BrowserReadExecutionError("denied")
        context = browser.contexts[0]
        state.live.context = context
        if (
            await context.cookies()
            or len(context.pages) != 1
            or context.pages[0].url != "about:blank"
        ):
            raise BrowserReadExecutionError("denied")
        state.page = context.pages[0]
        state.page.set_default_timeout(5000)
        state.page.set_default_navigation_timeout(5000)

        async def route_request(route: Any) -> None:
            request = route.request
            parsed = urlsplit(request.url)
            origin = self.source.manifest.site.source.origin
            permit = (
                not state.cancellation.is_set()
                and not state.closed
                and parsed.scheme == "http"
                and parsed.netloc == urlsplit(origin).netloc
                and not parsed.query
                and not parsed.fragment
                and request.redirected_from is None
                and (request.method, parsed.path)
                in {("GET", "/"), ("GET", "/query"), ("POST", "/result")}
                and request.resource_type == "document"
            )
            if permit and request.method == "POST":
                try:
                    params = parse_qs(
                        request.post_data or "", strict_parsing=True, max_num_fields=3
                    )
                    binding = state.query_binding
                    permit = binding is not None and params == {
                        "code": [binding.code],
                        "period": [binding.period],
                        "query_sequence": [str(binding.query_sequence)],
                    }
                except ValueError:
                    permit = False
            if not permit:
                await route.abort()
                return
            try:
                await self._fence(state)
                await route.continue_()
            except Exception:
                await route.abort()

        async def refuse_socket(route: Any) -> None:
            await route.close(code=1008, reason="task_query_only")

        async def close_extra(page: Any) -> None:
            if page is not state.page:
                await page.close()

        async def cancel_download(download: Any) -> None:
            await download.cancel()

        await context.route("**/*", route_request)
        await context.route_web_socket("**/*", refuse_socket)
        context.on("page", close_extra)
        state.page.on("download", cancel_download)
        state.page.on("dialog", lambda dialog: dialog.dismiss())
        # The existing resolution round requires a registered origin and region
        # before it can dispatch navigate. Bootstrap only the permitted GET after
        # routes and fences are active; no POST/ledger query occurs in open.
        await self._fence(state)
        await state.page.goto(
            self.source.manifest.site.source.origin + "/query",
            wait_until="domcontentloaded",
            timeout=min(5000, max(1, int((state.deadline - time.monotonic()) * 1000))),
        )
        await self._fence(state)
        if state.page.url != self.source.manifest.site.source.origin + "/query":
            raise BrowserReadExecutionError("denied")

    async def resolve_live(self, session: BrowserSessionRef) -> LiveBrowser:
        state = next((state for state in self._states.values() if state.session == session), None)
        if state is None:
            raise failure("denied")
        await self._fence(state)
        if (
            state.live is None
            or state.live.context is None
            or state.page is None
            or state.page.is_closed()
        ):
            raise failure("stale")
        return state.live

    async def assert_business_authority(
        self, session: BrowserSessionRef, source: DecisionSource
    ) -> LiveBrowser:
        if source != self.source.manifest.site.source:
            raise failure("denied")
        return await self.resolve_live(session)

    def _check_subject(
        self,
        state: _RunBrowser,
        session: BrowserSessionRef,
        skill: BrowserSkill,
        subject: ActionCommand | ReadSpec,
        binding: ScopeBinding,
    ) -> None:
        manifest = self.source.manifest
        if (
            state.session != session
            or session.binding != binding
            or skill != manifest.skill
            or subject.binding != binding
        ):
            raise failure("denied")
        if isinstance(subject, ActionCommand):
            if (
                subject.skill_digest != skill.digest
                or subject.step not in skill.steps
                or subject.step.effect != "read_only"
            ):
                raise failure("denied")
        elif state.read_spec is None or subject != state.read_spec:
            raise failure("denied")

    def _context(self, state: _RunBrowser) -> ExecutionContext:
        session = state.session
        if session is None:
            raise BrowserReadExecutionError("unavailable")
        scope, manifest = session.binding, self.source.manifest
        execution_id = uuid4().hex

        async def current(actual: BrowserSessionRef) -> ScopeBinding:
            if actual != session:
                raise failure("denied")
            await self._fence(state)
            return scope

        async def authorize(
            actual: BrowserSessionRef,
            skill: BrowserSkill,
            subject: ActionCommand | ReadSpec,
            binding: ScopeBinding,
        ) -> None:
            self._check_subject(state, actual, skill, subject, binding)
            await self._fence(state)

        async def resolve(
            ref: ParameterRef,
            purpose: ParameterPurpose,
            binding: ScopeBinding,
            skill_digest: str,
            step_id: str,
        ) -> SealedParameter:
            await self._fence(state)
            if binding != scope or skill_digest != manifest.skill.digest:
                raise failure("denied")
            step = next((step for step in manifest.skill.steps if step.step_id == step_id), None)
            if purpose == "business_key":
                allowed = (
                    step_id == manifest.verifier.verifier_id
                    and ref == manifest.site.read_rule.key_ref
                )
            else:
                allowed = (
                    step is not None
                    and {"fill_value": step.value_ref, "navigation_url": step.url_ref}.get(purpose)
                    == ref
                )
            if not allowed:
                raise failure("denied")
            value = (
                manifest.site.source.origin + "/query"
                if ref.name == "query_url"
                else state.arguments.get(ref.name)
            )
            if type(value) is not str:
                raise failure("denied")
            return SealedParameter(
                value,
                ref=ref,
                purpose=purpose,
                binding=binding,
                skill_digest=skill_digest,
                step_id=step_id,
            )

        @asynccontextmanager
        async def barrier(
            actual: BrowserSessionRef, command: ActionCommand, binding: ScopeBinding
        ) -> AsyncIterator[DispatchPermit]:
            async with state.dispatch_lock:
                await authorize(actual, manifest.skill, command, binding)

                def begin() -> None:
                    if (
                        state.cancellation.is_set()
                        or state.closed
                        or time.monotonic() >= state.deadline
                        or state.claim is None
                        or state.claim.deadline <= datetime.now(UTC)
                        or state.run.worker_deadline is None
                        or state.run.worker_deadline <= datetime.now(UTC)
                    ):
                        raise failure("stale")

                yield DispatchPermit(execution_id, command, begin)
                await self._fence(state)

        return ExecutionContext(
            execution_id,
            manifest.skill,
            scope,
            manifest.site.source,
            state.deadline,
            state.cancellation,
            manifest.site.navigation_origins,
            current,
            authorize,
            resolve,
            barrier,
        )

    def _decisions(
        self, state: _RunBrowser
    ) -> DecisionContextFactory:
        def create(context: ExecutionContext, request: DecisionRequest) -> DecisionCallContext:
            def targets() -> tuple[TargetRef, ...]:
                observer, session = state.observer, state.session
                if observer is None or session is None or state.cancellation.is_set():
                    return ()
                observed = observer._observed.get((session.session_ref, request.scope.region_id))
                if observed is None or observed.scope != request.scope:
                    return ()
                wanted = tuple(candidate.ref for candidate in request.candidates)
                actual = {
                    (candidate.target_id, candidate.epoch) for candidate in observed.candidates
                }
                return (
                    wanted
                    if all((ref.target_id, ref.candidate_epoch) in actual for ref in wanted)
                    else ()
                )

            return DecisionCallContext(
                context.deadline_monotonic,
                self.source.manifest.site.decision_manifest,
                context.source,
                targets,
                self.source.manifest.site.decision_budget,
                state.cancellation,
            )

        return create

    async def check_subject(self, claim: BrowserLeaseClaim, resource_ref: bytes) -> None:
        state = self._claim_state(claim, resource_ref)
        if (
            state.inventory is None
            or state.process is None
            or state.live is None
            or state.live.context is None
            or state.live.browser is None
            or state.profile is None
            or state.page is None
        ):
            raise BrowserLeaseError("browser_resource_subject_unconfirmed")
        state.process.inspect_live_root()
        collect_run_process_identities(state.inventory)
        root = _same_process(state.inventory.module, state.inventory.root)
        parsed = urlsplit(state.page.url)
        page_in_scope = (
            state.page.url == "about:blank"
            or parsed.scheme == "http"
            and parsed.netloc == urlsplit(self.source.manifest.site.source.origin).netloc
            and parsed.path in {"/", "/query", "/result"}
            and not parsed.query
            and not parsed.fragment
        )
        if (
            not state.inventory.live_confirmed
            or state.inventory.errors
            or root is None
            or state.closed
            or state.cancellation.is_set()
            or "--user-data-dir=" + str(state.profile) not in root.cmdline()
            or not any(
                connection.status == "LISTEN"
                and connection.laddr.ip == "127.0.0.1"
                and connection.laddr.port == state.cdp_port
                for connection in root.net_connections(kind="tcp")
            )
            or not state.live.browser.is_connected()
            or state.live.browser.contexts != [state.live.context]
            or state.live.context.pages != [state.page]
            or state.page.is_closed()
            or not page_in_scope
            or state.fixture is None
            or state.query_binding is None
            or state.session is None
            or state.fixture.server is None
            or state.fixture.server.fileno() == -1
            or state.fixture.thread is None
            or not state.fixture.thread.is_alive()
            or state.fixture.closing
            or state.fixture.binding != state.query_binding
            or state.session.binding.owner != claim.auth.owner
            or await asyncio.wait_for(state.live.context.cookies(), 2)
        ):
            raise BrowserLeaseError("browser_resource_subject_unconfirmed")

    def _claim_state(self, claim: BrowserLeaseClaim, resource_ref: bytes | None) -> _RunBrowser:
        state = self._states.get(claim.auth.authorization_run_id or "")
        if (
            state is None
            or state.claim is None
            or state.claim.auth != claim.auth
            or state.claim.binding != claim.binding
            or state.claim.operation_id != claim.operation_id
            or state.claim.holder_id != claim.holder_id
            or state.claim.lease_epoch != claim.lease_epoch
            or claim.provider_key != self.options.provider_key
            or resource_ref is not None
            and not hmac.compare_digest(state.resource_ref, resource_ref)
        ):
            raise BrowserLeaseError("browser_resource_claim_mismatch")
        return state

    @staticmethod
    def _receipt_matches(state: _RunBrowser, claim: BrowserLeaseClaim) -> bool:
        receipt, process = state.terminal_receipt, state.process
        if receipt is None or process is None or not process.handles_closed:
            return False
        fact = receipt.fact
        return (
            receipt.run_id == state.run.run_id == claim.auth.authorization_run_id
            and receipt.task_id == state.run.task_id
            and receipt.claim == claim
            and receipt.claim.auth.owner == state.run.owner
            and state.claim == claim
            and state.run.lease_epoch == claim.lease_epoch
            and hmac.compare_digest(receipt.resource_ref, state.resource_ref)
            and fact.job_ref == process.job_ref
            and fact.root_pid == process.pid
            and fact.root_created == process.created
            and fact.root_executable == process.executable
            and fact.active == 0
            and fact.members == ()
            and fact.cdp_port == state.cdp_port
            and state.cleanup_deadline is not None
            and state.cleanup_deadline - 15 <= fact.inspected <= state.cleanup_deadline
            and state.closed
            and state.cancellation.is_set()
            and state.fixture is not None
            and state.fixture.closed()
            and state.inventory is not None
            and not state.inventory.errors
            and state.inventory.thread is not None
            and not state.inventory.thread.is_alive()
        )

    async def verify(
        self, expected: BrowserProviderExpectation, evidence: bytes
    ) -> BrowserProviderFact:
        state = self._claim_state(expected.claim, expected.resource_ref)
        if expected.manifest_digest != self.manifest_digest or not hmac.compare_digest(
            expected.challenge, self.proof_context.challenge(expected.claim, self.manifest_digest)
        ):
            raise BrowserLeaseError("browser_provider_proof_mismatch")
        outcome: Literal["acquired", "terminated"]
        if state.acquisition_evidence is not None and hmac.compare_digest(
            evidence, state.acquisition_evidence
        ):
            await self.check_subject(expected.claim, state.resource_ref)
            outcome = "acquired"
        elif (
            state.termination_evidence is not None
            and hmac.compare_digest(evidence, state.termination_evidence)
            and state.cleanup_proven
            and state.inventory is not None
            and state.fixture is not None
            and state.process is not None
        ):
            if state.terminal_receipt is None:
                # Consume proof by querying the SAME still-open private job here.
                # A requested stop or a previous successful scan is insufficient.
                fact = verify_cleanup(state.inventory, state.cdp_port, state.fixture)
                if (
                    fact is None
                    or state.cleanup_deadline is None
                    or time.monotonic() >= state.cleanup_deadline
                ):
                    raise BrowserLeaseError("browser_provider_proof_unconfirmed")
                receipt = _TerminationReceipt(
                    state.run.run_id, state.run.task_id, expected.claim, state.resource_ref, fact
                )
                # Live provider verification has consumed the original handles.
                # Only successful closure can seal a monotonic terminal receipt.
                if not state.process.close_handles():
                    raise BrowserLeaseError("browser_provider_handle_close_unconfirmed")
                state.terminal_receipt = receipt
            if not self._receipt_matches(state, expected.claim):
                raise BrowserLeaseError("browser_provider_proof_unconfirmed")
            outcome = "terminated"
        else:
            raise BrowserLeaseError("browser_provider_proof_unconfirmed")
        return BrowserProviderFact(
            expected.claim.provider_key,
            expected.claim.operation_id,
            expected.claim.holder_id,
            expected.claim.lease_epoch,
            expected.challenge,
            self.manifest_digest,
            state.resource_ref,
            outcome,
        )


class DWindowsQueryLifecycle:
    def __init__(
        self,
        factory: DWindowsQueryFactory,
        cleanup_authority: BrowserCleanupAuthorityPort,
        no_resource: Callable[[RunSnapshot], Awaitable[bool]],
    ) -> None:
        self.factory, self.authority, self.no_resource = factory, cleanup_authority, no_resource
        if factory._lifecycle is not None:
            raise ValueError("windows_query_lifecycle_already_installed")
        factory._lifecycle = self

    async def lookup_capture(
        self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint
    ) -> BrowserCaptureResolution:
        await checkpoint.refresh(allow_cancel=True)
        return BrowserCaptureResolution("unsupported")

    async def send_capture(
        self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint
    ) -> BrowserCaptureResolution:
        await checkpoint.refresh(allow_cancel=True)
        return BrowserCaptureResolution("unsupported")

    async def stop(self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint) -> None:
        await checkpoint.refresh(allow_cancel=True)
        await self.cleanup(run)

    def _start_owned_stop(self, state: _RunBrowser) -> asyncio.Task[_NativeCleanupFact | None]:
        state.closed = True
        if state.cleanup_task is None:
            if state.cleanup_deadline is None:
                raise BrowserReadExecutionError("denied")
            state.cleanup_task = asyncio.create_task(
                self._stop_resources(state, state.cleanup_deadline)
            )
        return state.cleanup_task

    async def _stop_resources(
        self, state: _RunBrowser, deadline: float
    ) -> _NativeCleanupFact | None:
        """One bounded owned-resource stop; no DB release and no query re-execution."""
        confirmed = True
        if state.inventory is not None:
            state.inventory.stopping = True
            collect_run_process_identities(state.inventory)
        if state.live is not None:
            try:
                await asyncio.wait_for(
                    state.live.close(), min(2, max(0.001, deadline - time.monotonic()))
                )
            except Exception:
                # A failed CDP close cannot prove anything; the native stop and
                # separate process/port/thread reads below still must run.
                pass
        try:
            native_deadline = max(time.monotonic(), deadline - 0.5)
            if state.inventory is not None:
                await asyncio.to_thread(stop_and_wait, state.inventory, native_deadline)
            elif state.process is not None:
                await asyncio.to_thread(_stop_created_handle, state.process, native_deadline)
                confirmed = False  # No complete startup registration, so quarantine.
        except Exception:
            confirmed = False
            if state.inventory is not None:
                state.inventory.errors.add("stop_unconfirmed")
                state.inventory.stop.set()
        # Attempt the independent component even when the native stop failed.
        try:
            if state.fixture is not None:
                await asyncio.to_thread(state.fixture.close, deadline)
        except Exception:
            confirmed = False
        fact = (
            await asyncio.to_thread(verify_cleanup, state.inventory, state.cdp_port, state.fixture)
            if confirmed
            and state.inventory is not None
            and state.fixture is not None
            and time.monotonic() < deadline
            else None
        )
        if fact is None or state.cleanup_abandoned:
            # Never sign terminal proof on this path. Retain original handles if
            # root exit is unknown; closing a job is also not an exit observation.
            if state.process is not None:
                try:
                    if state.process.root is None or state.process.poll() is not None:
                        state.process.close_handles()
                except Exception:
                    pass
            return None
        return fact  # Job remains open until the provider consumes fresh proof.

    async def cleanup(self, run: RunSnapshot) -> RunCleanup:
        state = self.factory._states.get(run.run_id)
        if state is None:
            return "released" if await self.no_resource(run) else "quarantined"
        if state.run.admission != run.admission:
            raise BrowserReadExecutionError("denied")
        async with state.cleanup_lock:
            if state.cleanup_status == "terminated":
                await self.check_cancel(run)  # Consume sealed fact; no query on a closed handle.
                return state.cleanup_status
            if state.cleanup_status in {"released", "quarantined"}:
                return state.cleanup_status
            if state.cleanup_deadline is None:
                state.cleanup_deadline = time.monotonic() + 15
            state.cancellation.set()
            try:
                async with asyncio.timeout_at(state.cleanup_deadline):
                    if state.claim is None or self.factory._leases is None:
                        state.closed = True
                        state.cleanup_status = (
                            "released" if await self.no_resource(run) else "quarantined"
                        )
                        return state.cleanup_status
                    await self.authority.check_cleanup(state.claim)
                    async with state.dispatch_lock:
                        stop_task = self._start_owned_stop(state)
                        # Caller cancellation must not abandon native processes
                        # or server threads. This task can only stop components;
                        # it cannot later mint a release after we report unknown.
                        state.cleanup_proven = await asyncio.shield(stop_task) is not None
                        if state.cleanup_proven:
                            state.termination_evidence = self.factory.proof_context.digest(
                                "windows-query-terminated.v1", state.resource_ref
                            )
                            if await self.factory._leases.cleanup(
                                state.claim, state.termination_evidence
                            ):
                                state.cleanup_status = "terminated"
                                return state.cleanup_status
                        await self.factory._leases.quarantine(state.claim)
            except asyncio.CancelledError:
                state.cleanup_abandoned = True
                state.cleanup_proven = False
                state.cleanup_status = "quarantined"
                # Authority/dispatch failure can precede task creation. Owned
                # stop is still required, but cannot issue proof or release.
                self._start_owned_stop(state)
                if (
                    state.cleanup_task is not None
                    and state.cleanup_task.done()
                    and state.process is not None
                ):
                    try:
                        if state.process.root is None or state.process.poll() is not None:
                            state.process.close_handles()
                    except Exception:
                        pass
                raise
            except Exception:
                # No release callback was invoked with an unproven outcome.
                state.cleanup_abandoned = True
                state.cleanup_proven = False
                self._start_owned_stop(state)
                if (
                    state.cleanup_task is not None
                    and state.cleanup_task.done()
                    and state.process is not None
                ):
                    try:
                        if state.process.root is None or state.process.poll() is not None:
                            state.process.close_handles()
                    except Exception:
                        pass
            state.cleanup_status = "quarantined"
            return state.cleanup_status

    async def check_cancel(self, run: RunSnapshot) -> None:
        state = self.factory._states.get(run.run_id)
        if state is None:
            if await self.no_resource(run):
                return
            raise BrowserLeaseError("browser_cancel_proof_unconfirmed")
        if (
            state.run.admission != run.admission
            or not state.cancellation.is_set()
            or state.cleanup_status != "terminated"
            or not state.cleanup_proven
            or state.claim is None
            or not self.factory._receipt_matches(state, state.claim)
        ):
            raise BrowserLeaseError("browser_cancel_proof_unconfirmed")

    async def check_cleanup(self, run: RunSnapshot, outcome: RunCleanup) -> None:
        if outcome == "quarantined":
            state = self.factory._states.get(run.run_id)
            if state is not None and state.run.admission != run.admission:
                raise BrowserLeaseError("browser_cleanup_claim_mismatch")
            return
        if outcome == "released" and await self.no_resource(run):
            return
        if outcome == "terminated":
            await self.check_cancel(run)
            return
        raise BrowserLeaseError("browser_cleanup_proof_unconfirmed")
