"""One Run-owned Windows Job and normal OA login; no IO at import.

The host supplies the run fence and navigation contract. No attached-browser
fallback, cookie restoration, tracing, retry, model call, save or business submission.
"""

from __future__ import annotations

import asyncio
import hashlib
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
_D_FORM_LOG_STAGES = frozenset(
    {
        "configuration",
        "binding",
        "installation",
        "precondition",
        "lease",
        "page",
        "control",
        "submit",
        "identity",
        "portal",
        "fill",
        "receipt",
        "cancel",
        "failure",
        "browser_start",
        "credential",
        "login_response",
        "login_aux_post",
        "credential_login_id",
        "credential_password",
        "control_login_id_wait",
        "control_password_wait",
        "control_submit_wait",
        "control_fence_lock",
        "control_fence_refresh",
        "control_fence_lease",
        "control_guard_denied",
        "portal_stop",
        "portal_entry",
        "management_caller",
        "management_binding",
        "management_grant",
        "management_read",
        "management_prepare",
        "management_activate",
        "management_operation",
        "publication_prepare",
        "publication_activate",
        "publication_read",
        "chat_confirmation",
        "cleanup_owned",
        "cleanup_release",
        "worker_1",
        "worker_2",
        "worker_3",
        "worker_4",
        "worker_5",
        "worker_6",
        "worker_7",
        "worker_8",
    }
)
_D_FORM_LOG_STATUSES = frozenset(
    {
        "started",
        "success",
        "failed",
        "cancelled",
        "pending",
        "unavailable",
        "ready",
        "received",
    }
)
_D_FORM_LOG_CODES = frozenset(CODES) | frozenset(
    {
        "unsupported",
        "denied",
        "stale",
        "cancelled",
        "timeout",
        "invalid_response",
        "unavailable",
        "error",
        "RECEIPT_INVALID",
        "EXECUTION_TIMEOUT",
        "AUTHORITY_LOST",
        "d_form_config_missing",
        "d_form_binding_unavailable",
        "browser_login_response_invalid",
        "browser_login_subject_mismatch",
        "browser_binding_stale",
        "browser_binding_inactive",
        "browser_binding_revision_mismatch",
        "browser_fill_stop_scope_denied",
        "browser_login_run_stale",
        "browser_login_lease_stale",
        "browser_credential_grant_invalid",
        "browser_credential_purpose_invalid",
        "browser_auth_expired",
        "browser_session_authorization_invalid",
        "browser_owner_or_binding_mismatch",
        "d_form_authorization_denied",
        "d_form_authorization_missing",
        "d_form_review_pending",
        "d_form_resource_scope_denied",
        "d_form_subject_proof_unavailable",
        "d_form_subject_mismatch",
        "store_error",
        "value_error",
        "os_error",
        "browser_publication_already_prepared",
        "browser_publication_capability_changed",
        "browser_publication_capability_unavailable",
        "browser_publication_conflict",
        "browser_publication_denied",
        "browser_publication_head_changed",
        "browser_publication_head_required",
        "browser_publication_inactive",
        "browser_publication_initial_invalid",
        "browser_publication_manifest_invalid",
        "browser_publication_not_found",
        "browser_publication_reference_invalid",
        "browser_publication_registry_unavailable",
        "browser_publication_review_denied",
        "browser_publication_review_required",
        "browser_publication_review_unavailable",
        "browser_publication_revision_conflict",
        "browser_publication_revision_invalid",
        "browser_publication_rollback_source_invalid",
        "browser_publication_source_denied",
        "browser_publication_storage_invalid",
        "browser_publication_store_unavailable",
        "browser_publication_version_conflict",
        # Fixed reasons of the kept owned-browser refusals; one code per refusal point.
        "ROUTE_ORIGIN_DENIED",
        "ROUTE_METHOD_OR_FRAME_DENIED",
        "ROUTE_LOGIN_NOT_ARMED",
        "ROUTE_LOGIN_DUPLICATE",
        "LOGIN_AUX_POST_ALLOWED",
        "LOGIN_AUX_POST_BLOCKED",
        "VIOLATION_ROUTE_EXCEPTION",
        "VIOLATION_UNCLASSIFIED",
        "LOGIN_STOP_VISIBLE",
        "CONTROL_PASSWORD_TYPE",
        "CDP_PORT_TIMEOUT",
        "CDP_TARGET_TIMEOUT",
        "AUTOMATION_STOP_FAILED",
        "JOB_HANDLE_CLOSE_FAILED",
        "PORTAL_ENTRY_HTTP",
        "PORTAL_ENTRY_NAV_MISMATCH",
        "AUTH_WINDOW_INSUFFICIENT",
        "INPUT_INVALID",
        # Fixed store/authority refusals reachable from one fence; never free text.
        "browser_run_stale",
        "browser_run_worker_stale",
        "browser_run_not_found",
        "browser_run_checkpoint_stale",
        "browser_lease_stale",
        "browser_lease_required",
        "browser_write_confirmation_expired",
        "browser_write_confirmation_missing",
        "browser_write_confirmation_mismatch",
        "browser_write_confirmation_unapproved",
        "browser_write_preparation_missing",
        "browser_write_send_unreserved",
        "browser_fill_stop_dispatch_stale",
        "browser_fill_stop_publication_stale",
        "browser_owner_authorization_invalid",
        "browser_authority_transaction_required",
        "browser_policy_denied",
        "browser_capability_type_invalid",
        "browser_authorization_evidence_invalid",
        "browser_authorization_unavailable",
    }
)
_D_FORM_FENCE_FAILURE_TYPES = frozenset(
    {
        "BrowserRunStoreError",
        "BrowserReadExecutionError",
        "BrowserLeaseError",
        "BrowserAuthorizationError",
        "BrowserPublicationError",
        "ProgrammingError",
        "DBAPIError",
        "OperationalError",
        "IntegrityError",
        "InterfaceError",
        "SQLAlchemyError",
        "TimeoutError",
        "ValueError",
        "TypeError",
        "OSError",
        "AssertionError",
    }
)
# Fixed Stop codes for the portal preparation failure diagnosis only.
_D_FORM_PORTAL_STOP_CODES = frozenset(CODES) - {"OK"}
# Same fixed portal suffix as the Referer in app/infra/auth/oa.py:187; it is only
# joined to this owner's approved origin, never to an HTTP client base URL.
_PORTAL_ENTRY_PATH = "/wui/index.html"
_LOGIN_POST_PATH = "/api/hrm/login/checkLogin"


def log_d_form_stage(stage: str, status: str, code: object, began: float) -> None:
    """Only closed protocol facts; never exception text or original worker output."""
    safe_stage = stage if type(stage) is str and stage in _D_FORM_LOG_STAGES else "failure"
    safe_status = status if type(status) is str and status in _D_FORM_LOG_STATUSES else "failed"
    safe_code = code if type(code) is str and code in _D_FORM_LOG_CODES else "error"
    _logger.info(
        "d_form stage=%s status=%s code=%s elapsed_ms=%d",
        safe_stage,
        safe_status,
        safe_code,
        max(0, int((time.monotonic() - began) * 1000)),
    )


def log_d_form_fence_failure(stage: str, error: BaseException, began: float) -> None:
    """One failed fence step: closed type/code/sqlstate, never exception text or values."""
    try:
        name = type(error).__name__
        code: object = getattr(error, "code", None)
        if name == "BrowserPublicationError":
            # Publication errors carry their fixed code in args, as in bootstrap.
            code = error.args[0] if len(error.args) == 1 else None
        sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
        _logger.info(
            "d_form_fence_failure stage=%s exception_type=%s error_code=%s "
            "sqlstate=%s elapsed_ms=%d",
            stage if type(stage) is str and stage in _D_FORM_LOG_STAGES else "failure",
            name if name in _D_FORM_FENCE_FAILURE_TYPES else "UnknownException",
            code if type(code) is str and code in _D_FORM_LOG_CODES else "error",
            sqlstate
            if type(sqlstate) is str
            and len(sqlstate) == 5
            and all(char in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" for char in sqlstate)
            else "none",
            max(0, int((time.monotonic() - began) * 1000)),
        )
    except Exception:
        pass  # A diagnostic failure must not replace the fence exception.


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
            if (
                not value.startswith("/")
                or value.startswith("//")
                or parsed.netloc
                or parsed.scheme
                or parsed.query
                or parsed.fragment
                or "\\" in value
                or any(ord(c) < 32 for c in value)
                or len(value) > 2048
            ):
                raise ValueError("browser_login_contract_invalid")
        if not set(self.stops) <= {"captcha", "mfa", "sso", "warning"} or any(
            type(v) is not str or not 0 < len(v) <= 512 or "\x00" in v
            for v in (self.login_id, self.password, self.submit, *self.stops.values())
        ):
            raise ValueError("browser_login_contract_invalid")
        # Detach host mutable input once, before any await.
        object.__setattr__(self, "stops", MappingProxyType(dict(self.stops)))


def owned_provider_digest(publication: str, chromium_sha256: str) -> bytes:
    from app.browser_skill.publication_contracts import canonical_json

    return hashlib.sha256(
        canonical_json(
            {
                "mode": "FILL_STOP",
                "publication_digest": publication,
                "wall_seconds": 1800,
                "model_requests": 0,
                "browser_lifecycle": "owned_job_fill_stop_retained_v1",
                "chromium_sha256": chromium_sha256,
                "cleanup": "attachment_exit_and_exact_job_empty_before_release",
                "backend_exit": "kill_on_job_close_unsubmitted_fields_may_be_lost",
            }
        ).encode()
    ).digest()


def validate_owned_paths(chromium: Path, root: Path, digest: str, receipts: Path) -> None:
    workspace = Path(__file__).resolve().parents[3]
    if (
        not chromium.is_absolute()
        or not root.is_absolute()
        or root.is_symlink()
        or not receipts.is_absolute()
        or not root.is_dir()
        or not chromium.is_file()
        or chromium.is_symlink()
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
        or root.resolve().is_relative_to(workspace)
        or root.resolve().is_relative_to(receipts.resolve())
        or receipts.resolve().is_relative_to(root.resolve())
    ):
        raise ValueError("browser_owned_configuration_invalid")


class OwnedDFormBrowser:
    """Registered in runtime state before the first resource-creating await.

    Native handles remain alive after FILL_STOP. Only explicit cleanup may
    terminate them; backend exit has the approved KILL_ON_JOB_CLOSE behavior.
    """

    def __init__(
        self,
        *,
        chromium: Path,
        root: Path,
        origin: str,
        contract: OwnedOALoginContract,
    ) -> None:
        self.chromium, self.root = chromium, root
        self.origin, self.contract = canonical_origin(origin), contract
        self.job: Any = None
        self.playwright: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self.native: dict[str, object] | None = None
        self.attachment_cleanup_proven = False
        self.owned_resource_cleanup_proven = False
        self.identity_verified = False
        self.violation = False
        self._violation_seen = asyncio.Event()
        self._first_violation_code: str | None = None
        self.login_armed = False
        self.login_request: Any = None
        self.port = 0
        self.context_id: str | None = None
        self.target_id: str | None = None
        self.portal: Any = None
        self.preparation_error: str | None = None
        self.closed_job_identity: dict[str, object] | None = None
        self.current_stage = "browser_start"
        self.began = time.monotonic()

    def _stage(self, stage: str, status: str = "started", code: object = "OK") -> None:
        self.current_stage = stage
        log_d_form_stage(stage, status, code, self.began)

    def _mark_violation(self, code: str) -> None:
        if not self.violation:
            self._first_violation_code = code
        self.violation = True
        self._violation_seen.set()

    async def _unless_violation(self, call: Awaitable[Any]) -> Any:
        """Return an established violation to the caller instead of waiting out a timeout."""
        work = asyncio.ensure_future(call)
        stop = asyncio.ensure_future(self._violation_seen.wait())
        try:
            await asyncio.wait((work, stop), return_when=asyncio.FIRST_COMPLETED)
            if not work.done():
                raise BrowserReadExecutionError("denied")
            return work.result()
        finally:
            for pending in (work, stop):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(work, stop, return_exceptions=True)

    def _violation_stop(self, *stages: str) -> None:
        # Route callbacks only record the first code; emit it once here (closed codes).
        self.preparation_error = "REQUEST_GUARD_FAILED"
        log_d_form_stage(
            "control_guard_denied",
            "failed",
            self._first_violation_code or "VIOLATION_UNCLASSIFIED",
            self.began,
        )
        for stage in stages:
            self._stage(stage, "failed", "REQUEST_GUARD_FAILED")

    async def _log_login_snapshot(self) -> None:
        ready_state = "unknown"
        matches: int | None = None
        visible: bool | None = None

        async def collect() -> None:
            nonlocal ready_state, matches, visible
            state = await self.page.evaluate("() => document.readyState")
            if type(state) is str and state in {"loading", "interactive", "complete"}:
                ready_state = state
            control = self.page.locator(self.contract.login_id)
            matches = await control.count()
            if matches == 0:
                visible = False
            elif matches == 1:
                visible = await control.is_visible()

        try:
            await asyncio.wait_for(collect(), 1)
        except Exception:
            pass  # An unavailable snapshot must preserve the original timeout.
        _logger.info(
            "d_form_control_snapshot ready_state=%s login_matches=%s login_visible=%s",
            ready_state,
            matches,
            visible,
        )

    async def _check_stops(self) -> None:
        """Configured captcha/MFA/SSO/warning controls stop before any credential use."""
        for selector in self.contract.stops.values():
            loc = self.page.locator(selector)
            for index in range(await loc.count()):
                if await loc.nth(index).is_visible():
                    self._stage("credential", "failed", "LOGIN_STOP_VISIBLE")
                    raise BrowserReadExecutionError("denied")

    async def protect_context(self, browser: Any, context: Any) -> Any:
        """Apply native download denial and registration blocking before navigation."""
        root = await browser.new_browser_cdp_session()
        await root.send(
            "Browser.setDownloadBehavior",
            {"behavior": "deny", "browserContextId": self.context_id},
        )
        # Same mechanism used by the installed driver's serviceWorkers='block',
        # with an immutable rejection. Bypassing a SW cache alone is insufficient.
        await context.add_init_script(BLOCK_SERVICE_WORKERS)
        return root

    async def create_and_login(self, vault: RunBoundOACredentialVault, fence: Fence) -> None:
        """Exactly one private consumer and one matching main-frame login POST."""
        import psutil
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright

        from app.infra.browser.d_windows_query import _OwnedWindowsJob

        self._stage("browser_start")
        await fence()
        folder = self.root / ("run-" + uuid4().hex)
        folder.mkdir()
        profile = folder / "profile"
        profile.mkdir()
        environment = {
            k: os.environ[k] for k in ("SystemRoot", "WINDIR", "COMSPEC") if k in os.environ
        }
        for key, name in (
            ("TEMP", "temp"),
            ("TMP", "temp"),
            ("LOCALAPPDATA", "local"),
            ("APPDATA", "app"),
        ):
            destination = folder / name
            destination.mkdir(exist_ok=True)
            environment[key] = str(destination)
        self.job = _OwnedWindowsJob()  # Save handle owner before native operations/awaits.
        self.job.create_job()
        self.job.create_root(
            self.chromium,
            [
                str(self.chromium),
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                "--user-data-dir=" + str(profile),
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-sync",
                "--disable-extensions",
                "--disable-breakpad",
                "--disable-crash-reporter",
                "--start-maximized",
                "about:blank",
            ],
            environment,
            folder,
            visible=True,
        )
        self.job.bind_root()
        self.job.resume()
        # Chromium writes "<port>\n<browser path>" once its CDP endpoint is listening.
        deadline = time.monotonic() + 25
        active_port = profile / "DevToolsActivePort"
        lines: list[str] = []
        while len(lines) < 2:
            if time.monotonic() >= deadline:
                self._stage("browser_start", "failed", "CDP_PORT_TIMEOUT")
                raise BrowserReadExecutionError("timeout")
            await asyncio.sleep(0.1)
            if active_port.is_file():
                lines = active_port.read_text(encoding="ascii").splitlines()
        self.port = int(lines[0])
        self.playwright = await asyncio.wait_for(async_playwright().start(), 15)
        # Exact browser endpoint: no HTTP discovery request, so a host HTTP proxy
        # never sits between the driver and this loopback port.
        self.browser = await self.playwright.chromium.connect_over_cdp(
            "ws://127.0.0.1:" + lines[0] + lines[1],
            timeout=10000,
        )
        # Replace this new Job's empty startup page with one page in a new context.
        startup = self.browser.contexts[0].pages[0]
        root = await self.browser.new_browser_cdp_session()
        created = await root.send("Target.createBrowserContext", {"disposeOnDetach": False})
        self.context_id = created["browserContextId"]
        created = await root.send(
            "Target.createTarget",
            {
                "url": "about:blank",
                "browserContextId": self.context_id,
                "newWindow": True,
                "background": False,
            },
        )
        self.target_id = created["targetId"]
        await root.detach()
        deadline = time.monotonic() + 10
        while self.page is None:
            for wrapper in self.browser.contexts:
                for page in wrapper.pages:
                    if page is startup:
                        continue
                    session = await wrapper.new_cdp_session(page)
                    try:
                        info = (await session.send("Target.getTargetInfo"))["targetInfo"]
                    finally:
                        await session.detach()
                    if info.get("targetId") == self.target_id:
                        self.page, self.context = page, wrapper
            if self.page is None:
                if time.monotonic() >= deadline:
                    self._stage("browser_start", "failed", "CDP_TARGET_TIMEOUT")
                    raise BrowserReadExecutionError("timeout")
                await asyncio.sleep(0.1)
        await startup.close()
        protected = await self.protect_context(self.browser, self.context)
        await protected.detach()
        anonymous_initialization = False
        initialization_lock = asyncio.Lock()

        async def route(item: Any) -> None:
            # Only the configured OA origin; one armed credential POST; other
            # writes are aborted. No per-request run fence.
            route_reason = "ROUTE_ORIGIN_DENIED"
            try:
                request = item.request
                parsed = urlsplit(request.url)
                if (
                    not navigation_allowed(request.url, (self.origin,))
                    or parsed.username is not None
                    or parsed.password is not None
                ):
                    raise BrowserReadExecutionError("denied")
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    route_reason = "ROUTE_METHOD_OR_FRAME_DENIED"
                    if request.method != "POST" or request.frame is not self.page.main_frame:
                        raise BrowserReadExecutionError("denied")
                    if parsed.path != _LOGIN_POST_PATH:
                        async with initialization_lock:
                            if anonymous_initialization:
                                await item.continue_()
                                log_d_form_stage(
                                    "login_aux_post",
                                    "success",
                                    "LOGIN_AUX_POST_ALLOWED",
                                    self.began,
                                )
                                return
                        await item.abort()
                        log_d_form_stage(
                            "login_aux_post", "unavailable", "LOGIN_AUX_POST_BLOCKED", self.began
                        )
                        return
                    route_reason = (
                        "ROUTE_LOGIN_NOT_ARMED" if not self.login_armed else "ROUTE_LOGIN_DUPLICATE"
                    )
                    if not self.login_armed or self.login_request is not None:
                        raise BrowserReadExecutionError("denied")
                    self.login_request = request
                await item.continue_()
            except Exception:
                if not self.violation:
                    log_d_form_stage(self.current_stage, "failed", route_reason, self.began)
                self._mark_violation("VIOLATION_ROUTE_EXCEPTION")
                await item.abort()

        async def websocket(item: Any) -> None:
            await item.close()  # Context routes do not cover WebSockets; none is opened.

        await self.context.route("**/*", route)
        await self.context.route_web_socket("**/*", websocket)
        self._stage("browser_start", "success")
        self._stage("page")
        anonymous_initialization = True
        try:
            try:
                await self._unless_violation(
                    self.page.goto(
                        self.origin + self.contract.login_path,
                        wait_until="domcontentloaded",
                        timeout=30000,
                    )
                )
            except (TimeoutError, PlaywrightTimeoutError):
                await self._log_login_snapshot()
                self._stage("page", "failed", "timeout")
                raise
            except BrowserReadExecutionError:
                if self.violation:
                    self._violation_stop("page")
                raise
            self._stage("page", "success")
            self._stage("control")
            controls = [
                self.page.locator(s)
                for s in (
                    self.contract.login_id,
                    self.contract.password,
                    self.contract.submit,
                )
            ]
            current_step = "control_login_id_wait"

            async def wait_for_controls() -> None:
                nonlocal current_step
                stages = ("control_login_id_wait", "control_password_wait", "control_submit_wait")
                for stage, control in zip(stages, controls):
                    current_step = stage
                    self._stage(stage)
                    await self._unless_violation(control.wait_for(state="visible", timeout=30000))
                    self._stage(stage, "success")

            try:
                await asyncio.wait_for(wait_for_controls(), 30)
            except (TimeoutError, PlaywrightTimeoutError):
                await self._log_login_snapshot()
                self._stage(current_step, "failed", "OBSERVE_TIMEOUT")
                self._stage("control", "failed", "OBSERVE_TIMEOUT")
                raise
            except BrowserReadExecutionError:
                if self.violation:
                    self._violation_stop(current_step, "control")
                raise
            # Credentials go only into one visible, enabled control of each kind.
            for control in controls:
                count = await control.count()
                if count != 1:
                    self._stage(
                        "control",
                        "failed",
                        "CONTROL_MISSING" if count == 0 else "CONTROL_AMBIGUOUS",
                    )
                    raise BrowserReadExecutionError("denied")
                if not await control.is_visible() or not await control.is_enabled():
                    self._stage("control", "failed", "CONTROL_CHANGED")
                    raise BrowserReadExecutionError("denied")
            if await controls[1].get_attribute("type") != "password":
                self._stage("control", "failed", "CONTROL_PASSWORD_TYPE")
                raise BrowserReadExecutionError("denied")
        finally:
            # End anonymous loading before consuming credentials; keep the route active.
            async with initialization_lock:
                anonymous_initialization = False
        self._stage("control", "success")

        async def consume(credential: PasswordBindingCredential) -> None:
            await self._check_stops()
            self._stage("credential", "success")
            self._stage("credential_login_id")
            await controls[0].fill(credential.login_id.get_secret_value(), timeout=5000)
            self._stage("credential_login_id", "success")
            self._stage("credential_password")
            await controls[1].fill(credential.password.get_secret_value(), timeout=5000)
            self._stage("credential_password", "success")
            await fence()  # One run fence before the only credential POST.
            self._stage("submit")
            self.login_armed = True
            try:
                async with self.page.expect_response(
                    lambda response: (
                        self.login_request is not None and response.request is self.login_request
                    ),
                    timeout=15000,
                ) as pending:
                    await controls[2].click(timeout=5000, no_wait_after=True)
                    self._stage("submit", "success")
                    self._stage("login_response")
                response = await pending.value
                self._stage("login_response", "received")
                self._stage("identity")
                await vault.verify_login_response(response=response)
                self.identity_verified = True
                self._stage("identity", "success")
            finally:
                self.login_armed = False
                self.login_request = None

        self._stage("credential")
        await asyncio.wait_for(vault.consume_login(consume), 45)
        process = psutil.Process(self.job.pid)
        site = urlsplit(self.origin)
        self.native = {
            "pid": process.pid,
            "created": process.create_time(),
            "executable": str(self.chromium.resolve()),
            "profile": str(profile),
            "profile_root": str(folder),
            "port": self.port,
            "context_id": self.context_id,
            "target_id": self.target_id,
            "host": site.hostname,
            "scheme": site.scheme,
        }
        await self.stop_automation()
        if not self.attachment_cleanup_proven:
            self._stage("identity", "failed", "AUTOMATION_STOP_FAILED")
            raise BrowserReadExecutionError("denied")

    async def prepare_portal(self, fence: Fence) -> None:
        """Reuse the approved information/menu/unique-form contract, never fill.

        Its navigation actions are independent of the later three-field receipt.
        An exact preparation failure stops the worker before any field input.
        """
        from app.infra.browser.d_form_flow import Stop
        from app.infra.browser.d_portal_flow import PortalFlow

        assert self.native is not None
        self._stage("portal")
        site = urlsplit(self.origin)
        self.portal = PortalFlow(
            {
                "mode": "READ_REPORTER",
                "inputs": None,
                "local_gate": True,
                "binding": {
                    **self.native,
                    "source_port": site.port or (443 if site.scheme == "https" else 80),
                },
            },
            lambda *_: None,
            asyncio.Event(),
        )
        self.attachment_cleanup_proven = False
        try:
            await fence()
            await asyncio.wait_for(self.portal.connect(), 20)
            self.browser, self.context, self.page = (
                self.portal.browser,
                self.portal.context,
                self.portal.page,
            )
            protection = await self.protect_context(self.browser, self.context)
            self.portal.sessions.append(protection)
            await asyncio.wait_for(self.enter_portal_once(), 45)
            await asyncio.wait_for(self.portal.prepare_page(), 30)
            await fence()
            self.native = {**self.native, "target_id": self.portal.binding.target_id}
            self.target_id = self.portal.binding.target_id
        except Stop as error:
            self.preparation_error = error.code
            code = error.code
            log_d_form_stage(
                "portal_stop",
                "failed",
                code if type(code) is str and code in _D_FORM_PORTAL_STOP_CODES else "PREP_FAILED",
                self.began,
            )
            raise BrowserReadExecutionError("invalid_response") from None
        except TimeoutError:
            self.preparation_error = "PREP_FAILED"
            raise BrowserReadExecutionError("timeout") from None
        finally:
            # The portal's own driver is stopped by its cleanup; nothing stays attached.
            self.attachment_cleanup_proven = await self.portal.cleanup()
        if not self.attachment_cleanup_proven:
            self.preparation_error = "CLEANUP_FAILED"
            self._stage("portal", "failed", "CLEANUP_FAILED")
            raise BrowserReadExecutionError("denied")
        self._stage("portal", "success")

    async def enter_portal_once(self) -> None:
        """One fixed same-origin portal document GET after verified login.

        Ordinary browser navigation; the context route keeps the configured origin.
        This GET is neither a fill nor a submit.
        """
        from playwright.async_api import Error as PlaywrightError

        from app.infra.browser.d_form_flow import Stop

        portal = self.portal
        portal.entry_attempted = True
        log_d_form_stage("portal_entry", "started", "OK", self.began)
        try:
            response = await portal.page.goto(
                self.origin + _PORTAL_ENTRY_PATH,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            if response is None or response.status >= 400:
                log_d_form_stage("portal_entry", "failed", "PORTAL_ENTRY_HTTP", self.began)
                raise Stop("PREP_FAILED")
            await portal.sources(portal.page, portal.cdp)
        except PlaywrightError:
            log_d_form_stage("portal_entry", "failed", "PORTAL_ENTRY_NAV_MISMATCH", self.began)
            raise Stop(portal.violation or "PREP_FAILED") from None
        finally:
            portal.entry_settled = True
        log_d_form_stage("portal_entry", "success", "OK", self.began)

    async def stop_automation(self) -> None:
        """Disconnect the private Playwright driver, preserving this Job/window."""
        self.login_armed = False
        if self.playwright is not None:
            try:
                await asyncio.wait_for(self.playwright.stop(), 10)
            except Exception:
                return  # Attachment state unknown: the caller quarantines the lease.
            self.playwright = None
        self.attachment_cleanup_proven = True

    async def close_owned(self) -> None:
        """Caller must independently authorize this exact Run's cleanup first."""
        await self.stop_automation()
        if self.job is not None:
            # Terminate the whole private Job and wait for it to empty.
            self.job.stop_and_wait(time.monotonic() + 10)
            self.closed_job_identity = {"job": self.job.job_ref}
            if not self.job.close_handles():
                self._stage("cleanup_owned", "failed", "JOB_HANDLE_CLOSE_FAILED")
                raise BrowserReadExecutionError("denied")
        self.owned_resource_cleanup_proven = True
