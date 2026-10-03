"""Local Chromium ownership and native exit evidence, never client-supplied proof.

The Node helper uses public BrowserServer.process(); private IPC is not logged.
Unknown processes after a worker restart remain quarantined. This adapter never
guesses a PID, reconnects by a stale endpoint, or claims durable profile capture.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.metadata
import json
import os
import re
import secrets
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from app.browser_skill.models import BrowserOperationError, BrowserSessionRef
from app.infra.browser.playwright_actions import LiveBrowser
from app.infra.persistence.browser.crypto import BrowserClaimProofContext, resource_identity
from app.ports.browser_read_execution import (
    BrowserCaptureResolution,
    BrowserReadExecutionError,
    BrowserWorkerCheckpoint,
)
from app.ports.browser_run_store import RunCleanup, RunSnapshot
from app.ports.browser_store import (
    BrowserBindingKey,
    BrowserCleanupAuthorityPort,
    BrowserLeaseClaim,
    BrowserLeaseError,
    BrowserProviderExpectation,
    BrowserProviderFact,
    ProviderOutcome,
)
from app.ports.credential_vault import BrowserBindingFact

if TYPE_CHECKING:
    from app.infra.browser.fixed_synthetic_seed import FixedSyntheticSource
    from app.infra.browser.local_read_execution import LocalBrowserReadExecutionFactory

Guard = Callable[[], Awaitable[None]]


def _json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def local_subject_digest(
    tenant_id: str,
    user_id: str,
    object_type: str = "system_message_collection",
) -> bytes:
    """Synthetic SUBJECT codec for the trusted binding provisioner and live read."""
    return hashlib.sha256(
        _json(
            [
                "browser.synthetic.subject.v1",
                tenant_id,
                user_id,
                object_type,
            ]
        )
    ).digest()


@dataclass(frozen=True, slots=True, repr=False)
class LocalChromiumDeployment:
    provider_key: str
    manifest_digest: bytes
    node_executable: Path
    playwright_package: Path
    browsers_path: Path
    helper_digest: bytes
    node_digest: bytes
    chromium_digest: bytes
    enabled: bool = False
    timeout_seconds: float = 25.0

    def __post_init__(self) -> None:
        if (
            re.fullmatch(r"[A-Za-z0-9_-]{1,96}", self.provider_key) is None
            or any(
                type(v) is not bytes or len(v) != 32
                for v in (
                    self.manifest_digest,
                    self.helper_digest,
                    self.node_digest,
                    self.chromium_digest,
                )
            )
            or any(
                not isinstance(p, Path) or not p.is_absolute()
                for p in (self.node_executable, self.playwright_package, self.browsers_path)
            )
            or type(self.enabled) is not bool
            or not 1 <= self.timeout_seconds <= 30
        ):
            raise ValueError("browser_local_deployment_invalid")


@dataclass(slots=True, repr=False)
class _NativeChromium:
    process: asyncio.subprocess.Process
    identity: str
    timeout: float
    node_digest: bytes
    chromium_digest: bytes
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    sequence: int = 0
    original: dict[str, object] | None = None
    exited: bool = False

    async def call(self, operation: str) -> dict[str, Any]:
        async with self.lock:
            try:
                if (
                    operation not in {"launch", "status", "stop"}
                    or self.process.returncode is not None
                ):
                    raise ValueError
                self.sequence += 1
                assert self.process.stdin is not None and self.process.stdout is not None
                self.process.stdin.write(
                    _json(
                        {
                            "id": self.sequence,
                            "op": operation,
                            "identity": self.identity,
                            "node_digest": self.node_digest.hex(),
                            "chromium_digest": self.chromium_digest.hex(),
                        }
                    )
                    + b"\n"
                )
                async with asyncio.timeout(self.timeout):
                    await self.process.stdin.drain()
                    line = await self.process.stdout.readline()
                if not line or len(line) > 8192:
                    raise ValueError
                response = json.loads(line)
                if (
                    type(response) is not dict
                    or type(response.get("id")) is not int
                    or response.get("id") != self.sequence
                    or response.get("ok") is not True
                ):
                    raise ValueError
                result = response["result"]
                if (
                    type(result) is not dict
                    or result.get("identity") != self.identity
                    or type(result.get("pid")) is not int
                    or result["pid"] <= 0
                    or re.fullmatch(r"[0-9]{1,32}", str(result.get("start", ""))) is None
                    or re.fullmatch(r"[a-f0-9-]{36}", str(result.get("boot", ""))) is None
                    or result.get("exited") is not (operation == "stop")
                ):
                    raise ValueError
                original = {k: result[k] for k in ("identity", "pid", "start", "boot")}
                if operation == "launch":
                    if self.original is not None:
                        raise ValueError
                    endpoint = urlsplit(result["endpoint"])
                    if (
                        endpoint.scheme != "ws"
                        or endpoint.hostname != "127.0.0.1"
                        or not endpoint.port
                        or endpoint.username
                        or endpoint.password
                        or endpoint.query
                        or endpoint.fragment
                        or not endpoint.path
                    ):
                        raise ValueError
                    self.original = original
                elif original != self.original:
                    raise ValueError
                if operation == "stop":
                    self.exited = True
                return result
            except TimeoutError:
                raise BrowserReadExecutionError("timeout") from None
            except Exception:
                raise BrowserReadExecutionError("unavailable") from None

    async def dispose_helper(self) -> None:
        # EOF asks this owned helper to shut down its own browser. It never
        # supplies proof if the helper/native exit response was lost.
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            async with asyncio.timeout(3):
                await self.process.wait()
        except TimeoutError:
            pass


@dataclass(slots=True, repr=False)
class _Resource:
    claim: BrowserLeaseClaim
    session: BrowserSessionRef
    native: _NativeChromium
    reference: bytes
    live: LiveBrowser | None
    page: Any
    source: FixedSyntheticSource
    guard: Guard
    driver: Any = None
    acquire_token: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    exit_token: bytes | None = None
    terminal_at: float | None = None
    faulted: bool = False


class LocalBrowserResources:
    """Concrete BrowserProviderProofPort + BrowserResourceSubjectPort."""

    def __init__(
        self,
        deployment: LocalChromiumDeployment,
        proof_context: BrowserClaimProofContext,
        cleanup_authority: BrowserCleanupAuthorityPort,
    ) -> None:
        self.deployment, self._proof, self.cleanup_authority = (
            deployment,
            proof_context,
            cleanup_authority,
        )
        self._resources: dict[str, _Resource] = {}

    def _prune(self) -> None:
        for identity, item in tuple(self._resources.items()):
            if item.terminal_at is not None and time.monotonic() - item.terminal_at > 120:
                del self._resources[identity]

    def resource(self, reference: bytes) -> _Resource:
        for item in self._resources.values():
            if hmac.compare_digest(item.reference, reference):
                return item
        raise BrowserLeaseError("browser_local_resource_unknown")

    async def launch(
        self,
        claim: BrowserLeaseClaim,
        session: BrowserSessionRef,
        source: FixedSyntheticSource,
        *,
        guard: Guard,
        subject_guard: Guard,
    ) -> _Resource:
        deployment = self.deployment
        self._prune()
        helper = Path(__file__).with_name("managed_chromium.js")
        if (
            not deployment.enabled
            or sys.platform != "linux"
            or len(self._resources) >= 32
            or claim.provider_key != deployment.provider_key
            or any(
                os.environ.get(name)
                for name in ("DEBUG", "PWDEBUG", "SSLKEYLOGFILE", "NODE_OPTIONS")
            )
            or importlib.metadata.version("playwright") != "1.63.0"
            or hashlib.sha256(helper.read_bytes()).digest() != deployment.helper_digest
        ):
            raise BrowserReadExecutionError("unsupported")
        with deployment.node_executable.open("rb") as binary:
            if hashlib.file_digest(binary, "sha256").digest() != deployment.node_digest:
                raise BrowserReadExecutionError("unsupported")
        await guard()
        identity = self._proof.digest(
            "browser-local-instance.v1",
            _json(resource_identity(claim)) + secrets.token_bytes(32),
        ).hex()
        # No secrets/debug/preload settings are inherited by the browser helper.
        environment = {
            name: os.environ[name] for name in ("PATH", "HOME", "TMPDIR") if name in os.environ
        }
        environment["PLAYWRIGHT_BROWSERS_PATH"] = str(deployment.browsers_path)
        process = await asyncio.create_subprocess_exec(
            str(deployment.node_executable),
            str(helper),
            str(deployment.playwright_package),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=environment,
            limit=8192,
        )
        native = _NativeChromium(
            process,
            identity,
            deployment.timeout_seconds,
            deployment.node_digest,
            deployment.chromium_digest,
        )
        item: _Resource | None = None
        try:
            await guard()
            result = await native.call("launch")
            assert native.original is not None
            reference = _json({"schema": "browser.local.resource.v1", **native.original})
            # Register immediately, before any further authority check can fail.
            item = _Resource(claim, session, native, reference, None, None, source, subject_guard)
            self._resources[identity] = item
            await guard()
            from playwright.async_api import async_playwright

            driver = await asyncio.wait_for(async_playwright().start(), deployment.timeout_seconds)
            item.driver = driver
            await guard()
            browser = await driver.chromium.connect(
                result["endpoint"],
                timeout=deployment.timeout_seconds * 1000,
            )
            await guard()
            context = await asyncio.wait_for(
                browser.new_context(
                    accept_downloads=False,
                    service_workers="block",
                    java_script_enabled=False,
                ),
                deployment.timeout_seconds,
            )
            item.live = LiveBrowser(session, browser, context, driver)
            await guard()

            async def route(request: Any) -> None:
                await subject_guard()
                if (
                    request.request.url == source.manifest.site.source.origin + "/"
                    and request.request.method == "GET"
                    and request.request.resource_type == "document"
                ):
                    await request.fulfill(
                        status=200, content_type="text/html; charset=utf-8", body=source.html
                    )
                else:
                    item.faulted = True
                    await request.abort("blockedbyclient")
                await subject_guard()

            await asyncio.wait_for(context.route("**/*", route), deployment.timeout_seconds)
            await guard()
            page = await asyncio.wait_for(context.new_page(), deployment.timeout_seconds)
            item.page = page
            await guard()
            # Page routes take precedence over the adapter's later context
            # origin route, so continue_() cannot bypass exact-byte fulfillment.
            page.set_default_timeout(deployment.timeout_seconds * 1000)
            await asyncio.wait_for(page.route("**/*", route), deployment.timeout_seconds)
            await guard()
            await page.goto(source.manifest.site.source.origin + "/", wait_until="domcontentloaded")
            await guard()
            await self.check_subject(claim, reference)
            await guard()
            return item
        except BaseException as error:
            # Release is still forbidden until cleanup's independent grant and
            # native exit proof run. EOF is best-effort containment, not proof.
            if item is not None:
                item.faulted = True
            else:
                await native.dispose_helper()
            if isinstance(
                error, (BrowserReadExecutionError, BrowserOperationError, asyncio.CancelledError)
            ):
                raise
            if isinstance(error, TimeoutError):
                raise BrowserReadExecutionError("timeout") from None
            raise BrowserReadExecutionError("unavailable") from None

    async def verify(
        self,
        expected: BrowserProviderExpectation,
        evidence: bytes,
    ) -> BrowserProviderFact:
        item = next(
            (
                entry
                for entry in self._resources.values()
                if evidence in (entry.acquire_token, entry.exit_token)
            ),
            None,
        )
        if (
            item is None
            or type(evidence) is not bytes
            or resource_identity(expected.claim) != resource_identity(item.claim)
            or expected.manifest_digest != self.deployment.manifest_digest
            or expected.challenge
            != self._proof.challenge(
                expected.claim,
                self.deployment.manifest_digest,
            )
            or (expected.resource_ref is not None and expected.resource_ref != item.reference)
        ):
            raise BrowserLeaseError("browser_local_proof_invalid")
        if evidence == item.acquire_token:
            await item.guard()
            await item.native.call("status")
            await item.guard()
            if item.faulted or item.native.exited or item.live is None:
                raise BrowserLeaseError("browser_local_proof_invalid")
            outcome: ProviderOutcome = "acquired"
        else:
            if not item.native.exited or item.exit_token is None:
                raise BrowserLeaseError("browser_local_exit_unproven")
            outcome = "terminated"
        return BrowserProviderFact(
            expected.claim.provider_key,
            expected.claim.operation_id,
            expected.claim.holder_id,
            expected.claim.lease_epoch,
            expected.challenge,
            expected.manifest_digest,
            item.reference,
            outcome,
        )

    async def check_subject(self, claim: BrowserLeaseClaim, resource_ref: bytes) -> None:
        item = self.resource(resource_ref)
        if (
            resource_identity(item.claim) != resource_identity(claim)
            or not isinstance(claim.binding, BrowserBindingFact)
            or item.live is None
            or item.page is None
            or item.native.exited
            or item.faulted
        ):
            raise BrowserLeaseError("browser_local_subject_unavailable")
        await item.guard()
        if (
            item.page.is_closed()
            or item.page.url != item.source.manifest.site.source.origin + "/"
            or len(item.live.context.pages) != 1
            or item.live.context.pages[0] is not item.page
        ):
            raise BrowserLeaseError("browser_local_subject_mismatch")
        dom = item.source.rules.read
        if dom.object_type is None:
            raise BrowserLeaseError("browser_local_subject_mismatch")
        actual = await asyncio.wait_for(
            item.page.evaluate(
                """({tenant,user,objectType}) => {
              const read = s => { const ns=document.querySelectorAll(s);
                if(ns.length!==1 || !ns[0].isConnected) return null;
                const t=ns[0].textContent; return typeof t==='string' && t.length<=96 ? t : null; };
              return [read(tenant),read(user),read(objectType)];
            }""",
                {
                    "tenant": dom.tenant.selector,
                    "user": dom.user.selector,
                    "objectType": dom.object_type.selector,
                },
            ),
            self.deployment.timeout_seconds,
        )
        await item.guard()
        if (
            actual
            != [
                claim.auth.owner.tenant_id,
                claim.auth.owner.user_id,
                item.source.manifest.site.read_rule.object_type,
            ]
            or local_subject_digest(*actual) != claim.binding.subject_digest
        ):
            raise BrowserLeaseError("browser_local_subject_mismatch")

    async def terminate(self, item: _Resource) -> bytes:
        await self.cleanup_authority.check_cleanup(item.claim)
        if item.exit_token is None:
            try:
                await item.native.call("stop")
            except BrowserReadExecutionError:
                await item.native.dispose_helper()
                raise
            await self.cleanup_authority.check_cleanup(item.claim)
            if not item.native.exited:
                raise BrowserLeaseError("browser_local_exit_unproven")
            item.exit_token = secrets.token_bytes(32)
            item.terminal_at = time.monotonic()
            if item.driver is not None:
                try:
                    await asyncio.wait_for(item.driver.stop(), 3)
                except Exception:
                    pass  # Not termination evidence; native exit was already observed.
            await item.native.dispose_helper()
            item.live = item.page = item.driver = None
        return item.exit_token


class LocalBrowserReadLifecycle:
    def __init__(
        self,
        factory: LocalBrowserReadExecutionFactory,
        cleanup_authority: BrowserCleanupAuthorityPort,
    ) -> None:
        self._factory, self._authority = factory, cleanup_authority
        self._cleanup: dict[str, tuple[RunSnapshot, RunCleanup, float]] = {}
        self._cancel: dict[str, tuple[RunSnapshot, float]] = {}

    async def lookup_capture(
        self,
        run: RunSnapshot,
        checkpoint: BrowserWorkerCheckpoint,
    ) -> BrowserCaptureResolution:
        await checkpoint.refresh()
        return BrowserCaptureResolution("unsupported")

    async def send_capture(
        self,
        run: RunSnapshot,
        checkpoint: BrowserWorkerCheckpoint,
    ) -> BrowserCaptureResolution:
        await checkpoint.refresh()
        return BrowserCaptureResolution("unsupported")

    async def cleanup(self, run: RunSnapshot) -> RunCleanup:
        key = BrowserBindingKey(
            run.owner.tenant_id,
            run.owner.user_id,
            run.admission.target_system,
            run.admission.binding_id,
        )
        await self._authority.check_recovery(key)
        leases = self._factory.authority()[0]
        state = self._factory.state_for_cleanup(run)
        outcome: RunCleanup = "quarantined"
        if state is not None and state.released:
            if state.cleanup_outcome not in {"terminated", "released"}:
                raise BrowserReadExecutionError("unavailable")
            outcome = state.cleanup_outcome
        else:
            claim = state.claim if state is not None else await leases.recover_cleanup(key)
            if claim is None or not self._factory.claim_matches_run(claim, run):
                raise BrowserReadExecutionError("denied")
            await self._authority.check_cleanup(claim)
            if state is not None and state.resource is not None:
                state.cancellation.set()
                async with state.dispatch_lock:
                    try:
                        evidence = await self._factory.resources.terminate(state.resource)
                    except BrowserReadExecutionError:
                        await self._authority.check_cleanup(claim)
                        # The store retains quota when acquisition was sent and
                        # no original-process exit proof can be supplied.
                        await leases.cleanup(claim)
                        self._remember_cleanup(run, "quarantined")
                        return "quarantined"
                    await self._authority.check_cleanup(claim)
                    if await leases.cleanup(claim, evidence):
                        state.released = True
                        state.terminal_at = time.monotonic()
                        state.key = ""
                        state.observer = None
                        state.cleanup_outcome = "terminated"
                        outcome = "terminated"
            else:
                # Store proves never-sent reservations itself. A recovered
                # acquiring/unknown process cannot be rediscovered from a PID.
                if await leases.cleanup(claim):
                    outcome = "released"
                    if state is not None:
                        state.released = True
                        state.terminal_at = time.monotonic()
                        state.cleanup_outcome = "released"
                        state.key = ""
                else:
                    outcome = "quarantined"
        self._remember_cleanup(run, outcome)
        return outcome

    def _remember_cleanup(self, run: RunSnapshot, outcome: RunCleanup) -> None:
        now = time.monotonic()
        for run_id, (_, _, deadline) in tuple(self._cleanup.items()):
            if deadline <= now:
                del self._cleanup[run_id]
        if len(self._cleanup) >= 32 and run.run_id not in self._cleanup:
            raise BrowserReadExecutionError("unavailable")
        self._cleanup[run.run_id] = (run, outcome, now + 120)

    async def stop(self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint) -> None:
        fresh = await checkpoint.refresh(allow_cancel=True)
        if (
            not fresh.cancel_requested
            or fresh.admission != run.admission
            or fresh.worker_epoch != run.worker_epoch
        ):
            raise BrowserReadExecutionError("denied")
        if await self.cleanup(fresh) not in {"released", "terminated"}:
            raise BrowserReadExecutionError("unavailable")
        fresh = await checkpoint.refresh(allow_cancel=True)
        for run_id, (_, deadline) in tuple(self._cancel.items()):
            if deadline <= time.monotonic():
                del self._cancel[run_id]
        if len(self._cancel) >= 32 and fresh.run_id not in self._cancel:
            raise BrowserReadExecutionError("unavailable")
        self._cancel[fresh.run_id] = (fresh, time.monotonic() + 120)

    async def check_cancel(self, run: RunSnapshot) -> None:
        emission = self._cancel.get(run.run_id)
        if (
            emission is None
            or emission[1] <= time.monotonic()
            or emission[0].admission != run.admission
            or emission[0].worker_id != run.worker_id
            or emission[0].worker_epoch != run.worker_epoch
            or emission[0].lease_epoch != run.lease_epoch
            or not run.cancel_requested
            or not run.cancel_acknowledged
        ):
            raise BrowserReadExecutionError("denied")

    async def check_cleanup(
        self,
        transaction: object,
        run: RunSnapshot,
        outcome: RunCleanup,
    ) -> None:
        # The separately injected SQL cleanup_authorize callback runs first. This
        # local check performs no provider/DB IO while the Run transaction is held.
        emission = self._cleanup.get(run.run_id)
        if (
            emission is None
            or emission[2] <= time.monotonic()
            or emission[1] != outcome
            or emission[0].admission != run.admission
            or emission[0].provider_key != run.provider_key
            or emission[0].lease_epoch != run.lease_epoch
        ):
            raise BrowserReadExecutionError("denied")
