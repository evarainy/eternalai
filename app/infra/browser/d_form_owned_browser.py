"""One Run-owned Windows Job and normal OA login; no IO at import.

The host supplies audited controls and navigation. No attached-browser fallback,
cookie restoration, tracing, retry, model call, save or business submission.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from app.browser_skill.site_rules import canonical_origin, navigation_allowed
from app.infra.browser.d_form_cli import CODES
from app.infra.browser.oa_browser_login import RunBoundOACredentialVault
from app.ports.browser_read_execution import BrowserReadExecutionError
from app.ports.credential_binding import PasswordBindingCredential

_logger = logging.getLogger("uvicorn.error.d_form")
_D_FORM_LOG_STAGES = frozenset({
    "configuration", "binding", "installation", "precondition", "lease", "page", "control",
    "submit", "identity", "portal", "fill", "receipt", "cancel", "failure",
    "browser_start", "credential", "login_response",
    "management_caller", "management_binding", "management_grant", "management_read",
    "management_prepare", "management_activate", "management_operation",
    "publication_prepare", "publication_activate", "publication_read",
    "worker_1", "worker_2", "worker_3", "worker_4", "worker_5", "worker_6", "worker_7", "worker_8",
})
_D_FORM_LOG_STATUSES = frozenset({
    "started", "success", "failed", "cancelled", "pending", "unavailable", "ready", "received",
})
_D_FORM_LOG_CODES = frozenset(CODES) | frozenset({
    "unsupported", "denied", "stale", "cancelled", "timeout", "invalid_response", "unavailable",
    "error", "RECEIPT_INVALID", "EXECUTION_TIMEOUT", "AUTHORITY_LOST",
    "d_form_config_missing", "d_form_binding_unavailable",
    "browser_login_response_invalid", "browser_login_subject_mismatch",
    "browser_binding_stale", "browser_binding_inactive", "browser_binding_revision_mismatch",
    "browser_fill_stop_scope_denied", "browser_login_run_stale", "browser_login_lease_stale",
    "browser_credential_grant_invalid", "browser_credential_purpose_invalid",
    "browser_auth_expired", "browser_session_authorization_invalid",
    "browser_login_lease_unavailable", "browser_owner_or_binding_mismatch",
    "d_form_authorization_denied", "d_form_authorization_missing", "d_form_review_pending",
    "d_form_resource_scope_denied", "d_form_subject_proof_unavailable", "d_form_subject_mismatch",
    "store_error", "value_error", "os_error",
    "browser_publication_already_prepared", "browser_publication_capability_changed",
    "browser_publication_capability_unavailable", "browser_publication_conflict",
    "browser_publication_denied", "browser_publication_head_changed",
    "browser_publication_head_required", "browser_publication_inactive",
    "browser_publication_initial_invalid", "browser_publication_manifest_invalid",
    "browser_publication_not_found", "browser_publication_reference_invalid",
    "browser_publication_registry_unavailable", "browser_publication_review_denied",
    "browser_publication_review_required", "browser_publication_review_unavailable",
    "browser_publication_revision_conflict", "browser_publication_revision_invalid",
    "browser_publication_rollback_source_invalid", "browser_publication_source_denied",
    "browser_publication_storage_invalid", "browser_publication_store_unavailable",
    "browser_publication_version_conflict",
})


def log_d_form_stage(stage: str, status: str, code: object, began: float) -> None:
    """Only closed protocol facts; never exception text or original worker output."""
    safe_stage = stage if type(stage) is str and stage in _D_FORM_LOG_STAGES else "failure"
    safe_status = status if type(status) is str and status in _D_FORM_LOG_STATUSES else "failed"
    safe_code = code if type(code) is str and code in _D_FORM_LOG_CODES else "error"
    _logger.info("d_form stage=%s status=%s code=%s elapsed_ms=%d",
                 safe_stage, safe_status, safe_code, max(0, int((time.monotonic() - began) * 1000)))


Fence = Callable[[], Awaitable[None]]
BLOCK_SERVICE_WORKERS = """(() => {
 if (!navigator.serviceWorker) return;
 Object.defineProperty(ServiceWorkerContainer.prototype, 'register', {
   configurable: false, writable: false,
   value: async function() { throw new DOMException('Registration blocked', 'SecurityError'); }
 });
})()"""


@dataclass(frozen=True, slots=True, repr=False)
class OwnedOALoginContract:
    """Small trusted host input, never accepted from Chat or an HTTP command.

    Values must come from a deployment's sanitized contract. Optional supplied
    stop selectors cover captcha, MFA, SSO and warning controls; none is inferred.
    """

    login_path: str
    login_id: str
    password: str
    submit: str
    stops: Mapping[str, str] = field(repr=False)

    def __post_init__(self) -> None:
        for value in (self.login_path,):
            parsed = urlsplit(value)
            if (not value.startswith("/") or value.startswith("//") or parsed.netloc
                    or parsed.scheme or parsed.query or parsed.fragment or "\\" in value
                    or any(ord(c) < 32 for c in value) or len(value) > 2048):
                raise ValueError("browser_login_contract_invalid")
        if (not set(self.stops) <= {"captcha", "mfa", "sso", "warning"}
                or any(type(v) is not str or not 0 < len(v) <= 512 or "\x00" in v
                       for v in (self.login_id, self.password, self.submit, *self.stops.values()))):
            raise ValueError("browser_login_contract_invalid")
        # Detach host mutable input once, before any await.
        object.__setattr__(self, "stops", MappingProxyType(dict(self.stops)))


def owned_provider_digest(publication: str, source: str, chromium_sha256: str) -> bytes:
    from app.browser_skill.publication_contracts import canonical_json

    return hashlib.sha256(canonical_json({
        "mode": "FILL_STOP", "publication_digest": publication,
        "source_digest": source, "wall_seconds": 1800, "model_requests": 0,
        "browser_lifecycle": "owned_job_fill_stop_retained_v1",
        "chromium_sha256": chromium_sha256,
        "cleanup": "attachment_exit_and_exact_job_empty_before_release",
        "backend_exit": "kill_on_job_close_unsubmitted_fields_may_be_lost",
    }).encode()).digest()


def validate_owned_paths(chromium: Path, root: Path, digest: str, receipts: Path) -> None:
    workspace = Path(__file__).resolve().parents[3]
    if (not chromium.is_absolute() or not root.is_absolute() or root.is_symlink()
            or not receipts.is_absolute() or not root.is_dir() or not chromium.is_file()
            or chromium.is_symlink() or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or root.resolve().is_relative_to(workspace)
            or root.resolve().is_relative_to(receipts.resolve())
            or receipts.resolve().is_relative_to(root.resolve())):
        raise ValueError("browser_owned_configuration_invalid")


class OwnedDFormBrowser:
    """Registered in runtime state before the first resource-creating await.

    Native handles remain alive after FILL_STOP. Only explicit cleanup may
    terminate them; backend exit has the approved KILL_ON_JOB_CLOSE behavior.
    """

    def __init__(self, *, chromium: Path, chromium_sha256: str, root: Path,
                 node: Path, origin: str, contract: OwnedOALoginContract) -> None:
        self.chromium, self.binary_digest, self.root, self.node = (
            chromium, chromium_sha256, root, node,
        )
        self.origin, self.contract = canonical_origin(origin), contract
        self.job: Any = None
        self.manager: Any = None
        self.playwright: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self.native: dict[str, object] | None = None
        self.driver_identity: tuple[int, float, Path] | None = None
        self.creation_started = False
        self.driver_started = False
        self.attachment_cleanup_proven = False
        self.owned_resource_cleanup_proven = False
        self.identity_verified = False
        self.violation = False
        self.login_armed = False
        self.login_request: Any = None
        self.port = 0
        self.context_id: str | None = None
        self.target_id: str | None = None
        self.root_cdp: Any = None
        self.portal: Any = None
        self.preparation_error: str | None = None
        self.closed_job_identity: dict[str, object] | None = None
        self.current_stage = "browser_start"
        self.began = time.monotonic()

    def _stage(self, stage: str, status: str = "started", code: object = "OK") -> None:
        self.current_stage = stage
        log_d_form_stage(stage, status, code, self.began)

    async def _guard(self, fence: Fence) -> None:
        await fence()
        if self.violation:
            raise BrowserReadExecutionError("denied")
        if self.job is not None and self.job.resumed:
            self.job.inspect_live_root()
        if self.context is not None:
            if (self.page is None or self.page.is_closed()
                    or self.context.pages != [self.page]
                    or any(c is not self.context and c.pages for c in self.browser.contexts)
                    or self.page.frames != [self.page.main_frame]):
                raise BrowserReadExecutionError("denied")
            await self.check_context_targets()
            for selector in self.contract.stops.values():
                loc = self.page.locator(selector)
                for index in range(await loc.count()):
                    if await loc.nth(index).is_visible():
                        raise BrowserReadExecutionError("denied")

    async def check_context_targets(self, *, portal: bool = False) -> None:
        """CDP facts, never Playwright's default-context wrapper identity."""
        if self.context_id is None or self.target_id is None or self.root_cdp is None:
            raise BrowserReadExecutionError("denied")
        ids = (await self.root_cdp.send("Target.getBrowserContexts"))["browserContextIds"]
        if ids != [self.context_id]:
            raise BrowserReadExecutionError("denied")
        pages = [p for c in self.browser.contexts for p in c.pages]
        if not 1 <= len(pages) <= (2 if portal else 1):
            raise BrowserReadExecutionError("denied")
        for page in pages:
            session = await page.context.new_cdp_session(page)
            try:
                info = (await session.send("Target.getTargetInfo"))["targetInfo"]
                if (info.get("browserContextId") != self.context_id
                        or not portal and info.get("targetId") != self.target_id):
                    raise BrowserReadExecutionError("denied")
            finally:
                await session.detach()

    async def protect_context(self, browser: Any, context: Any) -> Any:
        """Apply native download denial and registration blocking before navigation."""
        root = await browser.new_browser_cdp_session()
        await root.send("Browser.setDownloadBehavior", {
            "behavior": "deny", "browserContextId": self.context_id, "eventsEnabled": True,
        })
        root.on("Browser.downloadWillBegin", lambda _: setattr(self, "violation", True))
        # Same mechanism used by the installed driver's serviceWorkers='block',
        # with an immutable rejection. Bypassing a SW cache alone is insufficient.
        await context.add_init_script(BLOCK_SERVICE_WORKERS)
        return root

    async def create_and_login(self, vault: RunBoundOACredentialVault, fence: Fence) -> None:
        """Exactly one private consumer and one matching main-frame login POST."""
        self._stage("browser_start")
        if os.name != "nt" or self.creation_started:
            raise BrowserReadExecutionError("denied")
        import psutil
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright

        from app.infra.browser.d_windows_query import _OwnedWindowsJob

        await fence()
        if any(os.environ.get(k) for k in ("DEBUG", "PWDEBUG", "SSLKEYLOGFILE", "NODE_OPTIONS")):
            raise BrowserReadExecutionError("denied")
        if not hmac.compare_digest(hashlib.sha256(self.chromium.read_bytes()).hexdigest(),
                                   self.binary_digest):
            raise BrowserReadExecutionError("denied")
        folder = self.root / ("run-" + uuid4().hex)
        folder.mkdir()
        profile = folder / "profile"
        profile.mkdir()
        environment = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "COMSPEC")
                       if k in os.environ}
        for key, name in (("TEMP", "temp"), ("TMP", "temp"),
                          ("LOCALAPPDATA", "local"), ("APPDATA", "app")):
            destination = folder / name
            destination.mkdir(exist_ok=True)
            environment[key] = str(destination)
        self.creation_started = True
        self.job = _OwnedWindowsJob()  # Save handle owner before native operations/awaits.
        self.job.create_job()
        self.job.create_root(self.chromium, [str(self.chromium),
            "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
            "--user-data-dir=" + str(profile), "--no-first-run", "--no-default-browser-check",
            "--disable-background-networking", "--disable-component-update", "--disable-sync",
            "--disable-extensions", "--disable-breakpad", "--disable-crash-reporter",
            "--start-maximized", "about:blank"],
            environment, folder, visible=True)
        self.job.bind_root()
        await fence()
        self.job.resume()
        deadline = time.monotonic() + 25
        active_port = profile / "DevToolsActivePort"
        while not active_port.is_file():
            await fence()
            self.job.inspect_live_root()
            if time.monotonic() >= deadline:
                raise BrowserReadExecutionError("timeout")
            await asyncio.sleep(0.1)
        lines = active_port.read_text(encoding="ascii").splitlines()
        if (active_port.is_symlink() or len(lines) != 2 or not lines[0].isdigit()
                or not 0 < int(lines[0]) <= 65535 or not lines[1].startswith("/devtools/browser/")):
            raise BrowserReadExecutionError("denied")
        self.port = int(lines[0])
        process = psutil.Process(self.job.pid)
        if not any(c.status == psutil.CONN_LISTEN and c.laddr.ip == "127.0.0.1"
                   and c.laddr.port == self.port for c in process.net_connections(kind="tcp")):
            raise BrowserReadExecutionError("denied")
        self.manager = async_playwright()  # Register driver owner before start awaits.
        self.driver_started = True
        self.playwright = await asyncio.wait_for(self.manager.start(), 15)
        transport = self.playwright._impl_obj._connection._transport
        driver = psutil.Process(transport._proc.pid)
        self.driver_identity = (driver.pid, driver.create_time(), Path(driver.exe()).resolve())
        if self.driver_identity[2] != self.node.resolve():
            raise BrowserReadExecutionError("denied")
        await fence()
        self.browser = await self.playwright.chromium.connect_over_cdp(
            "http://127.0.0.1:" + str(self.port), timeout=10000,
        )
        # Only this newly created Job's empty startup page can be replaced.
        if len(self.browser.contexts) != 1:
            raise BrowserReadExecutionError("denied")
        default = self.browser.contexts[0]
        if len(default.pages) != 1 or default.pages[0].url != "about:blank":
            raise BrowserReadExecutionError("denied")
        startup = default.pages[0]
        self.root_cdp = await self.browser.new_browser_cdp_session()
        if (await self.root_cdp.send("Target.getBrowserContexts"))["browserContextIds"]:
            raise BrowserReadExecutionError("denied")
        await fence()
        created = await self.root_cdp.send(
            "Target.createBrowserContext", {"disposeOnDetach": False},
        )
        self.context_id = created.get("browserContextId")
        if type(self.context_id) is not str or not self.context_id:
            raise BrowserReadExecutionError("denied")
        await fence()
        created = await self.root_cdp.send("Target.createTarget", {
            "url": "about:blank", "browserContextId": self.context_id,
            "newWindow": True, "background": True,
        })
        self.target_id = created.get("targetId")
        if type(self.target_id) is not str or not self.target_id:
            raise BrowserReadExecutionError("denied")
        deadline = time.monotonic() + 10
        while self.page is None:
            await fence()
            found = []
            for wrapper in self.browser.contexts:
                for page in wrapper.pages:
                    if page is startup:
                        continue
                    session = await wrapper.new_cdp_session(page)
                    try:
                        info = (await session.send("Target.getTargetInfo"))["targetInfo"]
                        if (info.get("browserContextId") != self.context_id
                                or info.get("targetId") != self.target_id
                                or page.url != "about:blank"):
                            raise BrowserReadExecutionError("denied")
                        found.append(page)
                    finally:
                        await session.detach()
            if len(found) > 1:
                raise BrowserReadExecutionError("denied")
            if found:
                self.page, self.context = found[0], found[0].context
            elif time.monotonic() >= deadline:
                raise BrowserReadExecutionError("timeout")
            else:
                await asyncio.sleep(0.1)
        self.job.inspect_live_root()
        await fence()
        if startup.url != "about:blank" or startup.is_closed():
            raise BrowserReadExecutionError("denied")
        await startup.close()  # Exact owned empty startup page, no legacy browser.
        await self.check_context_targets()
        protected = await self.protect_context(self.browser, self.context)
        await protected.detach()
        anonymous_initialization = False
        initialization_lock = asyncio.Lock()

        async def route(item: Any) -> None:
            try:
                request = item.request
                parsed = urlsplit(request.url)
                if (not navigation_allowed(request.url, (self.origin,))
                        or parsed.username is not None or parsed.password is not None):
                    raise BrowserReadExecutionError("denied")
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    if request.method != "POST" or request.frame is not self.page.main_frame:
                        raise BrowserReadExecutionError("denied")
                    if parsed.path != "/api/hrm/login/checkLogin":
                        if not anonymous_initialization:
                            raise BrowserReadExecutionError("denied")
                        await self._guard(fence)
                        await fence()
                        async with initialization_lock:
                            if (not anonymous_initialization or self.violation
                                    or request.frame is not self.page.main_frame):
                                raise BrowserReadExecutionError("denied")
                            await item.continue_()
                        return
                    if not self.login_armed or self.login_request is not None:
                        raise BrowserReadExecutionError("denied")
                    await self._guard(fence)
                    await fence()
                    if (not self.login_armed or self.login_request is not None
                            or self.violation or request.frame is not self.page.main_frame):
                        raise BrowserReadExecutionError("denied")
                    self.login_request = request
                    await item.continue_()
                    return
                await fence()
                await item.continue_()
            except Exception as error:
                if not self.violation:
                    log_d_form_stage(self.current_stage, "failed",
                                     getattr(error, "code", "error"), self.began)
                self.violation = True
                await item.abort()

        await self.context.route("**/*", route)
        self.page.on("dialog", lambda _: setattr(self, "violation", True))
        self.page.on("pageerror", lambda _: setattr(self, "violation", True))
        self.page.on("download", lambda _: setattr(self, "violation", True))
        self.context.on("serviceworker", lambda _: setattr(self, "violation", True))
        self.context.on("page", lambda _: setattr(self, "violation", True))
        async def websocket(item: Any) -> None:
            self.violation = True
            await item.close()
        await self.context.route_web_socket("**/*", websocket)
        self._stage("browser_start", "success")
        self._stage("page")
        anonymous_initialization = True
        try:
            try:
                await self.page.goto(self.origin + self.contract.login_path,
                                     wait_until="domcontentloaded", timeout=15000)
            except (TimeoutError, PlaywrightTimeoutError):
                self._stage("page", "failed", "timeout")
                raise
            await self._guard(fence)
            self._stage("page", "success")
            self._stage("control")
            controls = [self.page.locator(s) for s in (
                self.contract.login_id, self.contract.password, self.contract.submit,
            )]
            async def wait_for_controls() -> None:
                for control in controls:
                    await self._guard(fence)
                    await control.wait_for(state="visible", timeout=15000)

            try:
                await asyncio.wait_for(wait_for_controls(), 15)
            except (TimeoutError, PlaywrightTimeoutError):
                self._stage("control", "failed", "OBSERVE_TIMEOUT")
                raise
            await self._guard(fence)
            for control in controls:
                count = await control.count()
                if count != 1:
                    self._stage("control", "failed",
                                "CONTROL_MISSING" if count == 0 else "CONTROL_AMBIGUOUS")
                    raise BrowserReadExecutionError("denied")
                if not await control.is_visible() or not await control.is_enabled():
                    self._stage("control", "failed", "CONTROL_CHANGED")
                    raise BrowserReadExecutionError("denied")
            if await controls[1].get_attribute("type") != "password":
                self._stage("control", "failed", "CONTROL_CHANGED")
                raise BrowserReadExecutionError("denied")
        finally:
            # Close admission before waiting for any already admitted POST.
            anonymous_initialization = False
            async with initialization_lock:
                pass
        self._stage("control", "success")

        async def consume(credential: PasswordBindingCredential) -> None:
            await self._guard(fence)
            self._stage("credential", "success")
            await controls[0].fill(credential.login_id.get_secret_value(), timeout=5000)
            await self._guard(fence)
            await controls[1].fill(credential.password.get_secret_value(), timeout=5000)
            await self._guard(fence)
            self._stage("submit")
            self.login_armed = True
            try:
                async with self.page.expect_response(
                    lambda response: self.login_request is not None
                    and response.request is self.login_request, timeout=15000,
                ) as pending:
                    await controls[2].click(timeout=5000, no_wait_after=True)
                    self._stage("submit", "success")
                    self._stage("login_response")
                response = await pending.value
                self._stage("login_response", "received")
                self._stage("identity")
                await vault.verify_login_response(page=self.page, response=response)
                await self._guard(fence)
                self.identity_verified = True
                self._stage("identity", "success")
            finally:
                self.login_armed = False
                self.login_request = None

        self._stage("credential")
        await asyncio.wait_for(vault.consume_login(consume), 45)
        await self._guard(fence)
        cdp = await self.context.new_cdp_session(self.page)
        info = (await cdp.send("Target.getTargetInfo"))["targetInfo"]
        await cdp.detach()
        if (info.get("browserContextId") != self.context_id
                or info.get("targetId") != self.target_id):
            raise BrowserReadExecutionError("denied")
        site = urlsplit(self.origin)
        self.native = {"pid": process.pid, "created": process.create_time(),
            "executable": str(self.chromium.resolve()), "profile": str(profile),
            "profile_root": str(folder), "port": self.port, "context_id": info["browserContextId"],
            "target_id": info["targetId"], "host": site.hostname, "scheme": site.scheme}
        await self.stop_automation()
        if not self.attachment_cleanup_proven:
            raise BrowserReadExecutionError("denied")

    async def prepare_portal(self, fence: Fence) -> None:
        """Reuse the approved information/menu/unique-form contract, never fill.

        Its navigation actions are independent of the later three-field receipt.
        An exact preparation failure stops the worker before any field input.
        """
        from app.infra.browser.d_form_flow import Stop
        from app.infra.browser.d_portal_flow import PortalFlow

        self._stage("portal")
        owner = self

        class FencedPortal(PortalFlow):
            async def check(self) -> None:
                await fence()
                await super().check()
                await fence()

            async def sources(self, page: Any, cdp: Any, stable: bool = True) -> Any:
                await fence()
                await owner.check_context_targets(portal=True)
                result = await super().sources(page, cdp, stable)
                owner.job.inspect_live_root()
                await fence()
                return result

        if self.native is None or not self.identity_verified or not self.attachment_cleanup_proven:
            raise BrowserReadExecutionError("denied")
        site = urlsplit(self.origin)
        self.portal = FencedPortal({
            "mode": "READ_REPORTER", "inputs": None, "local_gate": True,
            "binding": {**self.native,
                        "source_port": site.port or (443 if site.scheme == "https" else 80)},
        }, lambda *_: None, asyncio.Event())
        clean = False
        self.attachment_cleanup_proven = False
        try:
            await fence()
            await asyncio.wait_for(self.portal.connect(), 20)
            self.browser, self.context, self.page = (
                self.portal.browser, self.portal.context, self.portal.page,
            )
            self.root_cdp = self.portal.sessions[0]
            self.context.on("serviceworker", lambda _: setattr(self, "violation", True))
            protection = await self.protect_context(self.browser, self.context)
            self.portal.sessions.append(protection)
            await self.check_context_targets(portal=True)
            await asyncio.wait_for(self.portal.prepare_page(), 30)
            await fence()
            self.native = {**self.native, "target_id": self.portal.binding.target_id}
            self.target_id = self.portal.binding.target_id
        except Stop as error:
            self.preparation_error = error.code
            raise BrowserReadExecutionError("invalid_response") from None
        except TimeoutError:
            self.preparation_error = "PREP_FAILED"
            raise BrowserReadExecutionError("timeout") from None
        finally:
            if self.portal.pw is not None:
                self.playwright = self.portal.pw
                import psutil

                try:
                    transport = self.playwright._impl_obj._connection._transport
                    driver = psutil.Process(transport._proc.pid)
                    self.driver_identity = (
                        driver.pid, driver.create_time(), Path(driver.exe()).resolve(),
                    )
                    if self.driver_identity[2] != self.node.resolve():
                        self.violation = True
                except psutil.Error:
                    self.violation = True
            elif self.portal.context is not None:
                self.violation = True
            clean = await self.portal.cleanup()
            if clean:
                self.playwright = None
            await self.stop_automation()
            self.attachment_cleanup_proven = (
                clean and self.attachment_cleanup_proven and not self.violation
            )
        if not self.attachment_cleanup_proven:
            self.preparation_error = "CLEANUP_FAILED"
            raise BrowserReadExecutionError("denied")
        self._stage("portal", "success")

    async def stop_automation(self) -> None:
        """Disconnect the private Playwright driver, preserving this Job/window."""
        import psutil

        self.login_armed = False
        if self.playwright is not None:
            try:
                await asyncio.wait_for(self.playwright.stop(), 10)
                self.playwright = None
            except Exception:
                return
        if self.driver_identity is not None:
            pid, created, executable = self.driver_identity
            try:
                process = psutil.Process(pid)
                if process.create_time() == created and Path(process.exe()).resolve() == executable:
                    return
            except psutil.NoSuchProcess:
                pass
            except psutil.Error:
                return
        elif self.driver_started:
            return  # Driver creation failed before exact identity: unknown, quarantine.
        self.attachment_cleanup_proven = True

    def check_identity(self) -> None:
        if (not self.identity_verified or self.native is None or self.violation
                or not self.attachment_cleanup_proven or self.job is None):
            raise BrowserReadExecutionError("denied")
        self.job.inspect_live_root()

    async def close_owned(self) -> None:
        """Caller must independently authorize this exact Run's cleanup first."""
        await self.stop_automation()
        if not self.attachment_cleanup_proven or self.job is None:
            raise BrowserReadExecutionError("denied")
        # Synchronous bounded native close retains handles until exact emptiness
        # has been consumed. Port closure/PID ancestry is never release proof.
        self.job.stop_and_wait(time.monotonic() + 10)
        if self.job.assigned and self.job.resumed:
            self.job.inspect_empty(self.port)
        else:
            self.job.inspect_owned_creation_empty()
        self.closed_job_identity = {
            "job": self.job.job_ref,
            "root": ({"pid": self.job.pid, "created": self.job.created,
                      "executable": self.job.executable} if self.job.root is not None else None),
            "active": 0, "members": [],
        }
        if not self.job.close_handles():
            raise BrowserReadExecutionError("denied")
        self.owned_resource_cleanup_proven = True
