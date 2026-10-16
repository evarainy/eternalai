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
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.async_api import BrowserContext, CDPSession, Frame, Page

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
        "control_login_id_guard",
        "control_login_id_wait",
        "control_password_guard",
        "control_password_wait",
        "control_submit_guard",
        "control_submit_wait",
        "control_fence_lock",
        "control_fence_refresh",
        "control_fence_publication",
        "control_fence_reservation",
        "control_fence_lease",
        "control_guard_fence",
        "control_guard_job",
        "control_guard_contexts",
        "control_guard_attach",
        "control_guard_target",
        "control_guard_detach",
        "control_guard_stops_count",
        "control_guard_stops_visible",
        "control_guard_denied",
        "control_guard_pageerror",
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
        "process_inventory",
        "process_python_base",
        "process_console_host",
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
        "browser_login_lease_unavailable",
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
        "ROUTE_REQUEST_INVALID",
        "ROUTE_ORIGIN_DENIED",
        "ROUTE_METHOD_OR_FRAME_DENIED",
        "ROUTE_NON_LOGIN_POST",
        "LOGIN_AUX_POST_ALLOWED",
        "LOGIN_AUX_POST_BLOCKED",
        "ROUTE_AUX_ABORT_FAILED",
        "ROUTE_LOGIN_NOT_ARMED",
        "ROUTE_LOGIN_DUPLICATE",
        "ROUTE_GUARD_FAILED",
        "ROUTE_FENCE_FAILED",
        "ROUTE_INITIALIZATION_CLOSED",
        "ROUTE_LOGIN_STATE_CHANGED",
        "ROUTE_CONTINUE_FAILED",
        "VIOLATION_PAGEERROR_NON_ANONYMOUS",
        "VIOLATION_ROUTE_EXCEPTION",
        "VIOLATION_DIALOG",
        "VIOLATION_DOWNLOAD",
        "VIOLATION_SERVICEWORKER",
        "VIOLATION_NEW_PAGE",
        "VIOLATION_WEBSOCKET",
        "VIOLATION_UNCLASSIFIED",
        "PAGEERROR_AFTER_AUX_BLOCK",
        "PAGEERROR_NO_PRIOR_AUX_BLOCK",
        "GUARD_FENCE_ERROR",
        "GUARD_JOB_ERROR",
        "GUARD_PAGE_MISSING",
        "GUARD_PAGE_CLOSED",
        "GUARD_CONTEXT_PAGES_CHANGED",
        "GUARD_OTHER_CONTEXT_PAGES",
        "GUARD_FRAME_SET_CHANGED",
        "GUARD_STRUCTURE_CHECK_ERROR",
        "GUARD_CDP_FACTS_MISSING",
        "GUARD_CDP_CONTEXT_QUERY_ERROR",
        "GUARD_CDP_CONTEXT_SET_CHANGED",
        "GUARD_CDP_PAGES_ERROR",
        "GUARD_CDP_PAGE_COUNT_CHANGED",
        "GUARD_CDP_ATTACH_ERROR",
        "GUARD_CDP_TARGET_QUERY_ERROR",
        "GUARD_CDP_TARGET_CONTEXT_CHANGED",
        "GUARD_CDP_TARGET_ID_CHANGED",
        "GUARD_CDP_DETACH_ERROR",
        "GUARD_STOP_LOCATOR_ERROR",
        "GUARD_STOP_COUNT_ERROR",
        "GUARD_STOP_VISIBILITY_ERROR",
        "GUARD_STOP_VISIBLE",
        "GUARD_UNCLASSIFIED",
        "PORTAL_ENTRY_REDIRECT",
        "PORTAL_ENTRY_HTTP",
        "PORTAL_ENTRY_NAV_MISMATCH",
        "PORTAL_ENTRY_NEW_DOCUMENT",
        "PORTAL_ENTRY_TARGET_CHANGED",
        "PORTAL_ENTRY_UNCONFIRMED_DOCUMENT",
        "AUTH_WINDOW_INSUFFICIENT",
        "ROUTE_LOCAL_CHECK_FAILED",
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
_D_FORM_ROUTE_RESOURCE_TYPES = frozenset(
    {
        "document",
        "stylesheet",
        "image",
        "media",
        "font",
        "script",
        "texttrack",
        "xhr",
        "fetch",
        "eventsource",
        "websocket",
        "manifest",
        "other",
    }
)
# Fixed Stop codes for the portal preparation failure diagnosis only.
_D_FORM_PORTAL_STOP_CODES = frozenset(CODES) - {"OK"}
# Same fixed portal suffix as the Referer in app/infra/auth/oa.py:187; it is only
# joined to this owner's approved origin, never to an HTTP client base URL.
_PORTAL_ENTRY_PATH = "/wui/index.html"


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
LocalCheck = Callable[[], None]
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


def owned_provider_digest(publication: str, source: str, chromium_sha256: str) -> bytes:
    from app.browser_skill.publication_contracts import canonical_json

    return hashlib.sha256(
        canonical_json(
            {
                "mode": "FILL_STOP",
                "publication_digest": publication,
                "source_digest": source,
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
        chromium_sha256: str,
        root: Path,
        node: Path,
        origin: str,
        contract: OwnedOALoginContract,
    ) -> None:
        self.chromium, self.binary_digest, self.root, self.node = (
            chromium,
            chromium_sha256,
            root,
            node,
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
        self._violation_seen = asyncio.Event()
        self._first_violation_code: str | None = None
        self._password_guard_denial_emitted = False
        # Temporary demo: window from login navigation until portal cleanup ends
        # (or an earlier failure); the FILL_STOP worker keeps its own guards.
        self._login_relaxed = False
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

    def _violation_stop(self, *stages: str, snapshot: Callable[[], None] | None = None) -> None:
        # Event callbacks only record the first code; emit it once here (closed codes).
        self.preparation_error = "REQUEST_GUARD_FAILED"
        if not self._password_guard_denial_emitted:
            self._password_guard_denial_emitted = True
            try:
                log_d_form_stage(
                    "control_guard_denied",
                    "failed",
                    self._first_violation_code or "VIOLATION_UNCLASSIFIED",
                    self.began,
                )
            except Exception:
                pass  # Diagnostic logging must not replace the original guard failure.
        if snapshot is not None:
            try:
                snapshot()
            except Exception:
                pass  # Diagnostic logging must not replace the original guard failure.
        for stage in stages:
            self._stage(stage, "failed", "REQUEST_GUARD_FAILED")

    def _passive_violation(self, code: str) -> None:
        # Temporary demo: passive events are not fatal inside the login window.
        if not self._login_relaxed:
            self._mark_violation(code)

    def _emit_password_guard_denial(self, code: str) -> None:
        if (
            self.current_stage
            in {"credential", "credential_login_id", "credential_password", "identity", "portal"}
            and not self._password_guard_denial_emitted
        ):
            self._password_guard_denial_emitted = True
            try:
                log_d_form_stage("control_guard_denied", "failed", code, self.began)
            except Exception:
                # Diagnostic logging must not replace the original guard failure.
                pass

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

    @contextmanager
    def _control_trace(self, stage: str, enabled: bool) -> Iterator[None]:
        if enabled:
            log_d_form_stage(stage, "started", "OK", self.began)
        yield
        if enabled:
            log_d_form_stage(stage, "success", "OK", self.began)

    async def _guard(self, fence: Fence, *, trace_control: bool = False) -> None:
        try:
            with self._control_trace("control_guard_fence", trace_control):
                await fence()
        except Exception:
            self._emit_password_guard_denial("GUARD_FENCE_ERROR")
            raise
        if self.violation:
            self._emit_password_guard_denial(self._first_violation_code or "VIOLATION_UNCLASSIFIED")
            raise BrowserReadExecutionError("denied")
        try:
            if self.job is not None and self.job.resumed:
                with self._control_trace("control_guard_job", trace_control):
                    self.job.inspect_live_root()
        except Exception:
            self._emit_password_guard_denial("GUARD_JOB_ERROR")
            raise
        if self.context is not None:
            # Temporary demo login window: extra routed same-context pages and frames
            # do not deny; the bound page and unrouted other contexts stay checked.
            relaxed = self._login_relaxed
            try:
                if self.page is None:
                    structure_code = "GUARD_PAGE_MISSING"
                elif self.page.is_closed():
                    structure_code = "GUARD_PAGE_CLOSED"
                elif (
                    self.page not in self.context.pages
                    if relaxed
                    else self.context.pages != [self.page]
                ):
                    structure_code = "GUARD_CONTEXT_PAGES_CHANGED"
                elif any(c is not self.context and c.pages for c in self.browser.contexts):
                    structure_code = "GUARD_OTHER_CONTEXT_PAGES"
                elif not relaxed and self.page.frames != [self.page.main_frame]:
                    structure_code = "GUARD_FRAME_SET_CHANGED"
                else:
                    structure_code = None
            except Exception:
                self._emit_password_guard_denial("GUARD_STRUCTURE_CHECK_ERROR")
                raise
            if structure_code is not None:
                self._emit_password_guard_denial(structure_code)
                raise BrowserReadExecutionError("denied")
            try:
                await self.check_context_targets(trace_control=trace_control)
            except Exception:
                self._emit_password_guard_denial("GUARD_UNCLASSIFIED")
                raise
            stop_step = "GUARD_STOP_LOCATOR_ERROR"
            try:
                for selector in self.contract.stops.values():
                    stop_step = "GUARD_STOP_LOCATOR_ERROR"
                    loc = self.page.locator(selector)
                    stop_step = "GUARD_STOP_COUNT_ERROR"
                    with self._control_trace("control_guard_stops_count", trace_control):
                        count = await loc.count()
                    for index in range(count):
                        stop_step = "GUARD_STOP_VISIBILITY_ERROR"
                        with self._control_trace("control_guard_stops_visible", trace_control):
                            visible = await loc.nth(index).is_visible()
                        if visible:
                            stop_step = "GUARD_STOP_VISIBLE"
                            raise BrowserReadExecutionError("denied")
                    stop_step = "GUARD_STOP_LOCATOR_ERROR"
            except Exception:
                self._emit_password_guard_denial(stop_step)
                raise

    async def check_context_targets(
        self,
        *,
        portal: bool = False,
        trace_control: bool = False,
    ) -> None:
        """CDP facts, never Playwright's default-context wrapper identity."""
        step = "GUARD_CDP_FACTS_MISSING"
        try:
            if self.context_id is None or self.target_id is None or self.root_cdp is None:
                raise BrowserReadExecutionError("denied")
            step = "GUARD_CDP_CONTEXT_QUERY_ERROR"
            with self._control_trace("control_guard_contexts", trace_control):
                ids = (await self.root_cdp.send("Target.getBrowserContexts"))["browserContextIds"]
            if ids != [self.context_id]:
                step = "GUARD_CDP_CONTEXT_SET_CHANGED"
                raise BrowserReadExecutionError("denied")
            step = "GUARD_CDP_PAGES_ERROR"
            pages = [p for c in self.browser.contexts for p in c.pages]
            if (
                self._login_relaxed
                and self.page in pages
                and all(p.context is self.context for p in pages)
            ):
                pages = [self.page]  # Temporary demo window: only the bound target.
            if not 1 <= len(pages) <= (2 if portal else 1):
                step = "GUARD_CDP_PAGE_COUNT_CHANGED"
                raise BrowserReadExecutionError("denied")
            for page in pages:
                step = "GUARD_CDP_ATTACH_ERROR"
                with self._control_trace("control_guard_attach", trace_control):
                    session = await page.context.new_cdp_session(page)
                try:
                    step = "GUARD_CDP_TARGET_QUERY_ERROR"
                    with self._control_trace("control_guard_target", trace_control):
                        info = (await session.send("Target.getTargetInfo"))["targetInfo"]
                    if info.get("browserContextId") != self.context_id:
                        step = "GUARD_CDP_TARGET_CONTEXT_CHANGED"
                        raise BrowserReadExecutionError("denied")
                    if not portal and info.get("targetId") != self.target_id:
                        step = "GUARD_CDP_TARGET_ID_CHANGED"
                        raise BrowserReadExecutionError("denied")
                finally:
                    previous_step = step
                    step = "GUARD_CDP_DETACH_ERROR"
                    with self._control_trace("control_guard_detach", trace_control):
                        await session.detach()
                    step = previous_step
        except Exception:
            self._emit_password_guard_denial(step)
            raise

    async def protect_context(self, browser: Any, context: Any) -> Any:
        """Apply native download denial and registration blocking before navigation."""
        root = await browser.new_browser_cdp_session()
        await root.send(
            "Browser.setDownloadBehavior",
            {
                "behavior": "deny",
                "browserContextId": self.context_id,
                "eventsEnabled": True,
            },
        )
        root.on("Browser.downloadWillBegin", lambda _: self._mark_violation("VIOLATION_DOWNLOAD"))
        # Same mechanism used by the installed driver's serviceWorkers='block',
        # with an immutable rejection. Bypassing a SW cache alone is insufficient.
        await context.add_init_script(BLOCK_SERVICE_WORKERS)
        return root

    async def create_and_login(
        self,
        vault: RunBoundOACredentialVault,
        fence: Fence,
        *,
        control_fence: Fence | None = None,
        local_check: LocalCheck | None = None,
    ) -> None:
        try:
            await self._create_and_login(
                vault,
                fence,
                control_fence=control_fence,
                local_check=local_check,
            )
        except BaseException:
            # Failure or cancellation ends the demo window; success continues to portal.
            self._login_relaxed = False
            raise

    async def _create_and_login(
        self,
        vault: RunBoundOACredentialVault,
        fence: Fence,
        *,
        control_fence: Fence | None = None,
        local_check: LocalCheck | None = None,
    ) -> None:
        """Exactly one private consumer and one matching main-frame login POST.

        With ``local_check`` (approved): startup polls run a full fence before and
        after the loop and local facts inside it; same-origin main-frame
        non-navigation GET/HEAD loads take the local check only. Start, login,
        identity, navigation and every action keep the full fence.
        """

        async def poll_check() -> None:
            if local_check is None:
                await fence()
            else:
                local_check()

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
        if not hmac.compare_digest(
            hashlib.sha256(self.chromium.read_bytes()).hexdigest(), self.binary_digest
        ):
            raise BrowserReadExecutionError("denied")
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
        self.creation_started = True
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
        await fence()
        self.job.resume()
        deadline = time.monotonic() + 25
        active_port = profile / "DevToolsActivePort"
        while not active_port.is_file():
            await poll_check()
            self.job.inspect_live_root()
            if time.monotonic() >= deadline:
                raise BrowserReadExecutionError("timeout")
            await asyncio.sleep(0.1)
        lines = active_port.read_text(encoding="ascii").splitlines()
        if (
            active_port.is_symlink()
            or len(lines) != 2
            or not lines[0].isdigit()
            or not 0 < int(lines[0]) <= 65535
            or not lines[1].startswith("/devtools/browser/")
        ):
            raise BrowserReadExecutionError("denied")
        self.port = int(lines[0])
        process = psutil.Process(self.job.pid)
        if not any(
            c.status == psutil.CONN_LISTEN
            and c.laddr.ip == "127.0.0.1"
            and c.laddr.port == self.port
            for c in process.net_connections(kind="tcp")
        ):
            raise BrowserReadExecutionError("denied")
        self.manager = async_playwright()  # Register driver owner before start awaits.
        self.driver_started = True
        self.playwright = await asyncio.wait_for(self.manager.start(), 15)
        transport = self.playwright._impl_obj._connection._transport
        driver = psutil.Process(transport._proc.pid)
        self.driver_identity = (driver.pid, driver.create_time(), Path(driver.exe()).resolve())
        if self.driver_identity[2] != self.node.resolve():
            raise BrowserReadExecutionError("denied")
        await fence()  # Full check after the port poll, before the first CDP connect.
        self.browser = await self.playwright.chromium.connect_over_cdp(
            "http://127.0.0.1:" + str(self.port),
            timeout=10000,
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
            "Target.createBrowserContext",
            {"disposeOnDetach": False},
        )
        self.context_id = created.get("browserContextId")
        if type(self.context_id) is not str or not self.context_id:
            raise BrowserReadExecutionError("denied")
        await fence()
        created = await self.root_cdp.send(
            "Target.createTarget",
            {
                "url": "about:blank",
                "browserContextId": self.context_id,
                "newWindow": True,
                "background": False,
            },
        )
        self.target_id = created.get("targetId")
        if type(self.target_id) is not str or not self.target_id:
            raise BrowserReadExecutionError("denied")
        deadline = time.monotonic() + 10
        while self.page is None:
            await poll_check()
            found = []
            for wrapper in self.browser.contexts:
                for page in wrapper.pages:
                    if page is startup:
                        continue
                    session = await wrapper.new_cdp_session(page)
                    try:
                        info = (await session.send("Target.getTargetInfo"))["targetInfo"]
                        if (
                            info.get("browserContextId") != self.context_id
                            or info.get("targetId") != self.target_id
                            or page.url != "about:blank"
                        ):
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
        route_pending: dict[int, float] = {}
        route_finished = 0
        route_max_ms = 0
        page_errors = request_failed = http_errors = route_aborted = websocket_blocked = 0
        aux_block_requested_count = 0
        first_nonanonymous_pageerror_logged = False

        def on_page_error(_: Any) -> None:
            nonlocal page_errors, first_nonanonymous_pageerror_logged
            page_errors += 1
            # Demo candidate: page script errors are counted and diagnosed only; they
            # do not mark a violation. All other guards still deny independently.
            if not anonymous_initialization:
                if not first_nonanonymous_pageerror_logged:
                    first_nonanonymous_pageerror_logged = True
                    # Ordering fact only: whether an aux POST abort had started before.
                    try:
                        log_d_form_stage(
                            "control_guard_pageerror",
                            "unavailable",
                            "PAGEERROR_AFTER_AUX_BLOCK"
                            if aux_block_requested_count > 0
                            else "PAGEERROR_NO_PRIOR_AUX_BLOCK",
                            self.began,
                        )
                    except Exception:
                        # Non-fatal diagnostic logging must not raise into the event handler.
                        pass

        def on_request_failed(_: Any) -> None:
            nonlocal request_failed
            request_failed += 1

        def on_response(response: Any) -> None:
            nonlocal http_errors
            if response.status >= 400:
                http_errors += 1

        def log_route_snapshot() -> None:
            now = time.monotonic()
            oldest_ms = max(
                (int((now - started) * 1000) for started in route_pending.values()), default=0
            )
            _logger.info(
                "d_form_route_snapshot finished=%d pending=%d "
                "oldest_pending_ms=%d handler_max_ms=%d",
                route_finished,
                len(route_pending),
                max(0, oldest_ms),
                route_max_ms,
            )
            _logger.info(
                "d_form_page_snapshot page_errors=%d request_failed=%d "
                "http_errors=%d route_aborted=%d websocket_blocked=%d",
                page_errors,
                request_failed,
                http_errors,
                route_aborted,
                websocket_blocked,
            )

        def local_resource(request: Any) -> bool:
            # Approved: same-origin (checked by the caller) main-frame non-navigation
            # GET/HEAD loads take the local check only; without one, only the earlier
            # four static types skip the remote fence. Navigations, OPTIONS, POST and
            # any unknown shape keep the original fenced path.
            try:
                return (
                    request.method in {"GET", "HEAD"}
                    and (
                        local_check is not None
                        or request.resource_type in {"script", "stylesheet", "image", "font"}
                    )
                    and not request.is_navigation_request()
                    and not self.violation
                    and self.page is not None
                    and request.frame is self.page.main_frame
                )
            except Exception:
                return False

        def log_route_request(item: Any) -> None:
            resource, method, navigation = "other", "OTHER", False
            try:
                request = item.request
                value = request.resource_type
                resource = (
                    value
                    if type(value) is str and value in _D_FORM_ROUTE_RESOURCE_TYPES
                    else "other"
                )
                value = request.method
                method = (
                    value
                    if type(value) is str and value in {"GET", "HEAD", "POST", "OPTIONS"}
                    else "OTHER"
                )
                navigation = request.is_navigation_request() is True
            except Exception:
                pass  # Closed defaults; the original route failure stays intact.
            _logger.info(
                "d_form_route_failure resource_type=%s method=%s navigation=%s",
                resource,
                method,
                navigation,
            )

        async def route(item: Any) -> None:
            nonlocal route_finished, route_max_ms, route_aborted, aux_block_requested_count
            route_id = id(item)
            route_started = time.monotonic()
            route_pending[route_id] = route_started
            route_reason = "ROUTE_REQUEST_INVALID"
            try:
                request = item.request
                parsed = urlsplit(request.url)
                route_reason = "ROUTE_ORIGIN_DENIED"
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
                    if parsed.path != "/api/hrm/login/checkLogin":
                        route_reason = "ROUTE_NON_LOGIN_POST"
                        if not anonymous_initialization:
                            route_reason = "ROUTE_AUX_ABORT_FAILED"
                            aux_block_requested_count += 1
                            await item.abort()
                            route_aborted += 1
                            log_d_form_stage(
                                "login_aux_post",
                                "unavailable",
                                "LOGIN_AUX_POST_BLOCKED",
                                self.began,
                            )
                            return
                        route_reason = "ROUTE_GUARD_FAILED"
                        await self._guard(fence)
                        route_reason = "ROUTE_FENCE_FAILED"
                        await fence()
                        async with initialization_lock:
                            route_reason = "ROUTE_INITIALIZATION_CLOSED"
                            if (
                                not anonymous_initialization
                                or self.violation
                                or request.frame is not self.page.main_frame
                            ):
                                raise BrowserReadExecutionError("denied")
                            route_reason = "ROUTE_CONTINUE_FAILED"
                            await item.continue_()
                            log_d_form_stage(
                                "login_aux_post",
                                "success",
                                "LOGIN_AUX_POST_ALLOWED",
                                self.began,
                            )
                        return
                    route_reason = (
                        "ROUTE_LOGIN_NOT_ARMED" if not self.login_armed else "ROUTE_LOGIN_DUPLICATE"
                    )
                    if not self.login_armed or self.login_request is not None:
                        raise BrowserReadExecutionError("denied")
                    route_reason = "ROUTE_GUARD_FAILED"
                    await self._guard(fence)
                    route_reason = "ROUTE_FENCE_FAILED"
                    await fence()
                    route_reason = "ROUTE_LOGIN_STATE_CHANGED"
                    if (
                        not self.login_armed
                        or self.login_request is not None
                        or self.violation
                        or request.frame is not self.page.main_frame
                    ):
                        raise BrowserReadExecutionError("denied")
                    self.login_request = request
                    route_reason = "ROUTE_CONTINUE_FAILED"
                    await item.continue_()
                    return
                if local_resource(request):
                    route_reason = "ROUTE_LOCAL_CHECK_FAILED"
                    if local_check is not None:
                        local_check()
                    route_reason = "ROUTE_CONTINUE_FAILED"
                    await item.continue_()
                    return
                route_reason = "ROUTE_FENCE_FAILED"
                await fence()
                route_reason = "ROUTE_CONTINUE_FAILED"
                await item.continue_()
            except Exception as error:
                route_aborted += 1
                if not self.violation:
                    log_d_form_stage(self.current_stage, "failed", route_reason, self.began)
                    log_route_snapshot()
                    log_d_form_stage(
                        self.current_stage, "failed", getattr(error, "code", "error"), self.began
                    )
                    log_route_request(item)
                self._mark_violation("VIOLATION_ROUTE_EXCEPTION")
                await item.abort()
            finally:
                route_pending.pop(route_id, None)
                route_finished += 1
                route_max_ms = max(
                    route_max_ms, max(0, int((time.monotonic() - route_started) * 1000))
                )

        # Temporary demo login window opens before the first event source; route
        # exceptions still mark violations. Ends after portal cleanup or on failure.
        self._login_relaxed = True
        await self.context.route("**/*", route)
        self.page.on("dialog", lambda _: self._passive_violation("VIOLATION_DIALOG"))
        self.page.on("pageerror", on_page_error)
        self.page.on("requestfailed", on_request_failed)
        self.page.on("response", on_response)
        self.page.on("download", lambda _: self._passive_violation("VIOLATION_DOWNLOAD"))
        self.context.on(
            "serviceworker", lambda _: self._passive_violation("VIOLATION_SERVICEWORKER")
        )
        self.context.on("page", lambda _: self._passive_violation("VIOLATION_NEW_PAGE"))

        async def websocket(item: Any) -> None:
            nonlocal websocket_blocked
            websocket_blocked += 1
            # Demo candidate: during login a WebSocket is still closed and counted,
            # but does not poison the Run; after the login window ones deny.
            if not anonymous_initialization:
                self._passive_violation("VIOLATION_WEBSOCKET")
            await item.close()

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
                log_route_snapshot()
                await self._log_login_snapshot()
                self._stage("page", "failed", "timeout")
                raise
            except BrowserReadExecutionError:
                if self.violation:
                    self._violation_stop("page", snapshot=log_route_snapshot)
                raise
            await self._guard(fence)
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
            current_step = "control_login_id_guard"

            async def wait_for_controls() -> None:
                nonlocal current_step
                stages = (
                    ("control_login_id_guard", "control_login_id_wait"),
                    ("control_password_guard", "control_password_wait"),
                    ("control_submit_guard", "control_submit_wait"),
                )
                for index, control in enumerate(controls):
                    guard_stage, wait_stage = stages[index]
                    current_step = guard_stage
                    self._stage(guard_stage)
                    guard_fence = (control_fence or fence) if index == 0 else fence
                    await self._guard(guard_fence, trace_control=index == 0)
                    self._stage(guard_stage, "success")
                    current_step = wait_stage
                    self._stage(wait_stage)
                    await self._unless_violation(control.wait_for(state="visible", timeout=30000))
                    self._stage(wait_stage, "success")

            try:
                await asyncio.wait_for(wait_for_controls(), 30)
            except (TimeoutError, PlaywrightTimeoutError):
                log_route_snapshot()
                await self._log_login_snapshot()
                self._stage(current_step, "failed", "OBSERVE_TIMEOUT")
                self._stage("control", "failed", "OBSERVE_TIMEOUT")
                raise
            except BrowserReadExecutionError:
                if self.violation:
                    self._violation_stop(current_step, "control", snapshot=log_route_snapshot)
                raise
            await self._guard(fence)
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
                self._stage("control", "failed", "CONTROL_CHANGED")
                raise BrowserReadExecutionError("denied")
        finally:
            # End anonymous loading before consuming credentials; keep route fences active.
            anonymous_initialization = False
            async with initialization_lock:
                pass
        self._stage("control", "success")

        async def consume(credential: PasswordBindingCredential) -> None:
            await self._guard(fence)
            self._stage("credential", "success")
            self._stage("credential_login_id")
            await controls[0].fill(credential.login_id.get_secret_value(), timeout=5000)
            self._stage("credential_login_id", "success")
            await self._guard(fence)
            self._stage("credential_password")
            await controls[1].fill(credential.password.get_secret_value(), timeout=5000)
            self._stage("credential_password", "success")
            await self._guard(fence)
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
                await vault.verify_login_response(page=self.page, response=response)
                await self._guard(fence)
                self.identity_verified = True
                self._stage("identity", "success")
            finally:
                self.login_armed = False
                self.login_request = None

        self._stage("credential")
        try:
            await asyncio.wait_for(vault.consume_login(consume), 45)
        except Exception:
            try:
                log_route_snapshot()  # Counts only, once; never values, URLs or text.
            except Exception:
                pass  # Diagnostic logging must not replace the original credential failure.
            raise
        await self._guard(fence)
        cdp = await self.context.new_cdp_session(self.page)
        info = (await cdp.send("Target.getTargetInfo"))["targetInfo"]
        await cdp.detach()
        if (
            info.get("browserContextId") != self.context_id
            or info.get("targetId") != self.target_id
        ):
            raise BrowserReadExecutionError("denied")
        site = urlsplit(self.origin)
        self.native = {
            "pid": process.pid,
            "created": process.create_time(),
            "executable": str(self.chromium.resolve()),
            "profile": str(profile),
            "profile_root": str(folder),
            "port": self.port,
            "context_id": info["browserContextId"],
            "target_id": info["targetId"],
            "host": site.hostname,
            "scheme": site.scheme,
        }
        await self.stop_automation()
        if not self.attachment_cleanup_proven:
            raise BrowserReadExecutionError("denied")

    async def prepare_portal(self, fence: Fence) -> None:
        try:
            await self._prepare_portal(fence)
        finally:
            # Temporary demo window ends after portal cleanup, success or failure.
            self._login_relaxed = False

    async def _prepare_portal(self, fence: Fence) -> None:
        """Reuse the approved information/menu/unique-form contract, never fill.

        Its navigation actions are independent of the later three-field receipt.
        An exact preparation failure stops the worker before any field input.
        """
        from app.infra.browser.d_form_flow import FrameTree, Stop
        from app.infra.browser.d_portal_flow import PortalFlow, TargetInfo

        self._stage("portal")
        owner = self

        class FencedPortal(PortalFlow):
            def page_changed(
                self, code: str, frame: Frame | None = None, page: Page | None = None
            ) -> None:
                # Temporary demo window: child frame navigations and new same-context
                # pages do not deny; the bound main frame, close and origins still do.
                if owner._login_relaxed and (
                    code == "PAGE_CHILD_FRAME_NAV"
                    or (
                        code == "PAGE_CONTEXT_EVENT"
                        and page is not None
                        and page.context is self.context
                    )
                ):
                    if self.menu_pending and page is not None:
                        self.watch(page)
                    return
                super().page_changed(code, frame=frame, page=page)

            def flag(self, code: str) -> None:
                # Temporary demo window: a passive JS dialog is never accepted or
                # dismissed, but does not deny; visible DOM modals still stop.
                if not (owner._login_relaxed and code == "UNEXPECTED_DIALOG"):
                    super().flag(code)

            async def relax_sets(self) -> None:
                # Temporary demo window: re-baseline the same-context page/frame sets;
                # origins, password/modal stops and target identity stay checked.
                if (
                    owner._login_relaxed
                    and self.page is not None
                    and not self.page.is_closed()
                    and self.page in cast(BrowserContext, self.context).pages
                ):
                    # connect() sets page/context/cdp together before prepare_portal().
                    self.pages = set(cast(BrowserContext, self.context).pages)
                    tree = cast(
                        FrameTree,
                        (await cast(CDPSession, self.cdp).send("Page.getFrameTree"))[
                            "frameTree"
                        ],
                    )
                    self.frame_ids = {item["frame"]["id"] for item in self.walk(tree)}

            async def check(self) -> None:
                await fence()
                await self.relax_sets()
                await super().check()
                await fence()

            async def sources(
                self, page: Page, cdp: CDPSession, stable: bool = True
            ) -> tuple[TargetInfo, FrameTree]:
                await fence()
                await owner.check_context_targets(portal=True)
                if stable:
                    await self.relax_sets()
                result = await super().sources(page, cdp, stable)
                owner.job.inspect_live_root()
                await fence()
                return result

        if self.native is None or not self.identity_verified or not self.attachment_cleanup_proven:
            raise BrowserReadExecutionError("denied")
        site = urlsplit(self.origin)
        self.portal = FencedPortal(
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
        clean = entered = False
        self.attachment_cleanup_proven = False
        try:
            await fence()
            await asyncio.wait_for(self.portal.connect(), 20)
            self.browser, self.context, self.page = (
                self.portal.browser,
                self.portal.context,
                self.portal.page,
            )
            self.root_cdp = self.portal.sessions[0]
            self.context.on(
                "serviceworker", lambda _: self._passive_violation("VIOLATION_SERVICEWORKER")
            )
            protection = await self.protect_context(self.browser, self.context)
            self.portal.sessions.append(protection)
            await self.check_context_targets(portal=True)
            await asyncio.wait_for(self.enter_portal_once(fence), 45)
            entered = True
            await asyncio.wait_for(self.portal.prepare_page(), 30)
            await fence()
            self.native = {**self.native, "target_id": self.portal.binding.target_id}
            self.target_id = self.portal.binding.target_id
        except Stop as error:
            self.preparation_error = error.code
            try:
                # A first document rejection after a successful entry, closed codes only.
                reason = self.portal.entry_document_reason
                if entered and type(reason) is str and reason in _D_FORM_LOG_CODES:
                    log_d_form_stage("portal_entry", "failed", reason, self.began)
                code = error.code
                log_d_form_stage(
                    "portal_stop",
                    "failed",
                    code
                    if type(code) is str and code in _D_FORM_PORTAL_STOP_CODES
                    else "PREP_FAILED",
                    self.began,
                )
            except Exception:
                # Diagnostic logging must not replace the original portal failure.
                pass
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
                        driver.pid,
                        driver.create_time(),
                        Path(driver.exe()).resolve(),
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

    async def enter_portal_once(self, fence: Fence) -> None:
        """One fixed same-origin portal document GET after verified login.

        A temporary context-wide gate admits only the original main frame's GET
        of the fixed target, once; every other navigation is rejected before it
        is sent, and non-navigation resources keep the original route. Redirects,
        other statuses and mismatches fail closed without retry or fallback. This
        GET is neither a fill nor a submit; the original LOGIN_REQUIRED stays intact.
        Only CDP facts of the original main frame classify its changes: the released
        GET's commit becomes the one confirmed document, and only protocol-reported
        same-document changes keeping the fragment-free target are accepted on it.
        """
        from playwright.async_api import Error as PlaywrightError

        from app.infra.browser.d_form_flow import (
            DEMO_EXTRA_GUARDS_DISABLED,
            Stop,
            need,
            origin,
        )

        portal = self.portal
        need(not portal.entry_attempted and not portal.menu_pending, "PREP_FAILED")
        portal.entry_attempted = True
        target = self.origin + _PORTAL_ENTRY_PATH
        parsed = urlsplit(target)
        binding = portal.binding
        need(
            parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and parsed.path == _PORTAL_ENTRY_PATH
            and origin(target) == (binding.scheme, binding.host, binding.source_port)
            and origin(target) in portal.allowed,
            "CONFIG_FAILED",
        )
        # Structure and stop conditions only; the login page's own password box
        # is not judged here, and no sources() call precedes this entry.
        await self._guard(fence)
        page = portal.page
        need(portal.violation is None, portal.violation or "PAGE_CHANGED")
        need(not portal.cancel.is_set(), "CANCELLED")
        # Temporary demo window: same-context page/child-frame sets are not compared.
        relaxed = self._login_relaxed
        need(
            not portal.menu_pending
            and page is self.page
            and not page.is_closed()
            and (
                relaxed
                or set(portal.context.pages) == portal.pages
                and page.frames == [page.main_frame]
            )
            and portal.http_route is not None
            and portal.binding.target_id == self.target_id,
            "PAGE_CHANGED",
        )
        frame = page.main_frame
        portal.entry_frame, portal.entry_target = frame, target
        # This attempt's own permit and callbacks; closing it ends both.
        closed = asyncio.Event()
        idle = asyncio.Event()
        idle.set()
        active = 0

        def log_entry(status: str, code: str) -> None:
            try:
                log_d_form_stage("portal_entry", status, code, self.began)
            except Exception:
                pass  # Diagnostic logging must not replace the entry outcome.

        if DEMO_EXTRA_GUARDS_DISABLED:
            # Use ordinary browser navigation for the approved demo. Do not
            # install the one-document permit, fetch/fulfill gate or CDP loader
            # observers. Existing context routes still enforce allowed origins.
            log_entry("started", "OK")
            try:
                await fence()
                response = await page.goto(
                    target,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                if response is None or response.status >= 400:
                    log_entry("failed", "PORTAL_ENTRY_HTTP")
                    raise Stop("PREP_FAILED")
                await self._guard(fence)
                await portal.sources(page, portal.cdp, stable=False)
            except PlaywrightError:
                log_entry("failed", "PORTAL_ENTRY_NAV_MISMATCH")
                raise Stop(portal.violation or "PREP_FAILED") from None
            finally:
                portal.entry_settled = True
            log_entry("success", "OK")
            return

        def permitted() -> bool:
            return (
                portal.entry_open
                and not closed.is_set()
                and not portal.cancel.is_set()
                and not self.violation
                and portal.violation is None
            )

        async def gated(call: Callable[[], Awaitable[Any]]) -> Any:
            # The permit is re-read in the same step that dispatches the call, and
            # closing it ends the wait instead of letting a stale callback resume.
            async def step() -> Any:
                if not permitted():
                    raise BrowserReadExecutionError("denied")
                return await call()

            work = asyncio.ensure_future(step())
            stop = asyncio.ensure_future(closed.wait())
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

        async def entry_route(item: Any) -> None:
            nonlocal active
            active += 1
            idle.clear()
            reason = "PORTAL_ENTRY_NAV_MISMATCH"
            delegated = fulfilled = False
            response: Any = None
            try:
                request = item.request
                if not request.is_navigation_request():
                    delegated = True  # Non-navigation resources keep the original guard.
                    await portal.http_route(item)
                    return
                # Every navigation in this context is checked here before it is sent.
                if (
                    not permitted()
                    or portal.entry_request is not None
                    or request.frame is not frame
                    or request.method != "GET"
                    or request.redirected_from is not None
                    or request.url != target
                ):
                    raise BrowserReadExecutionError("denied")
                portal.entry_request = request  # Consumed before the first await.
                await gated(fence)
                if not permitted() or origin(request.url) not in portal.allowed:
                    raise BrowserReadExecutionError("denied")
                reason = "PORTAL_ENTRY_HTTP"
                response = await gated(
                    lambda: item.fetch(max_redirects=0, max_retries=0, timeout=20000)
                )
                if 300 <= response.status < 400:
                    reason = "PORTAL_ENTRY_REDIRECT"
                if response.status != 200:
                    raise BrowserReadExecutionError("denied")
                reason = "PORTAL_ENTRY_NAV_MISMATCH"
                if response.url != target:
                    raise BrowserReadExecutionError("denied")
                await gated(fence)
                if not permitted():
                    raise BrowserReadExecutionError("denied")
                portal.entry_released = True
                # Unchanged pass-through; status, headers and body are never read here.
                await item.fulfill(response=response)
                fulfilled = True
            except Exception:
                if portal.entry_reason is None:
                    portal.entry_reason = reason
            finally:
                try:
                    if not delegated and not fulfilled:
                        try:
                            await item.abort()
                        except Exception:
                            pass  # Already resolved or closed; the entry still fails.
                    if response is not None:
                        try:
                            await response.dispose()
                        except Exception:
                            pass
                finally:
                    active -= 1
                    if active == 0:
                        idle.set()

        log_entry("started", "OK")
        # Original main frameId/loaderId before the permit; its CDP document events
        # are registered in the Flow handlers, so the existing cleanup removes them.
        baseline = False
        try:
            first = (await portal.cdp.send("Page.getFrameTree"))["frameTree"]
            portal.entry_frame_id = first["frame"]["id"]
            portal.entry_loader_before = first["frame"]["loaderId"]
            portal.event(portal.cdp, "Page.frameNavigated", portal.document_committed)
            portal.event(portal.cdp, "Page.navigatedWithinDocument", portal.document_changed)
            await portal.cdp.send("Page.enable")
            second = (await portal.cdp.send("Page.getFrameTree"))["frameTree"]
            baseline = (
                type(portal.entry_frame_id) is str
                and type(portal.entry_loader_before) is str
                and portal.entry_frame_id in portal.frame_ids
                and (relaxed or not first.get("childFrames") and not second.get("childFrames"))
                and second["frame"].get("id") == portal.entry_frame_id
                and second["frame"].get("loaderId") == portal.entry_loader_before
                and second["frame"].get("url") == first["frame"].get("url")
            )
        except Exception:
            baseline = False
        if not baseline or portal.violation is not None:
            log_entry("failed", portal.entry_document_reason or "PORTAL_ENTRY_UNCONFIRMED_DOCUMENT")
            raise Stop(portal.violation or "PREP_FAILED")
        portal.entry_controlled = True
        failed = cleanup_failed = False
        try:
            try:
                await portal.context.route("**/*", entry_route)
                portal.entry_open = True
                await page.goto(target, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightError:
                failed = True  # Never followed, retried or replaced by another entry.
        finally:
            # Close first: no fetch or fulfill can start after this point.
            portal.entry_open = False
            closed.set()
            # Remove only this exact handler (the original Flow route stays), then
            # require this attempt's in-flight callbacks to finish: unroute alone
            # does not prove that they ended.
            for settle, bound in (
                (lambda: portal.context.unroute("**/*", entry_route), 3),
                (idle.wait, 5),
            ):
                try:
                    await asyncio.wait_for(settle(), bound)
                except asyncio.CancelledError:
                    portal.helper_cleanup_failed = True
                    raise
                except Exception:
                    portal.helper_cleanup_failed = True
                    cleanup_failed = True
        # The network permit is already closed. This same-session CDP read is
        # answered after every earlier document event of this frame was handled;
        # only then does commit confirmation end (later commits are new documents).
        main: dict[str, Any] = {}
        children: Any = None
        try:
            document = (await asyncio.wait_for(portal.cdp.send("Page.getFrameTree"), 5))[
                "frameTree"
            ]
            main, children = dict(document["frame"]), document.get("childFrames")
        except Exception:
            failed = True
        finally:
            portal.entry_settled = True
        if (
            failed
            or portal.entry_reason is not None
            or portal.entry_document_reason is not None
            or portal.entry_request is None
            or not portal.entry_released
            or portal.entry_navigations != 1
            or portal.entry_loader is None
            or main.get("id") != portal.entry_frame_id
            or main.get("loaderId") != portal.entry_loader
            or main.get("url") != target
            or (children and not relaxed)
            or portal.violation is not None
            or page.is_closed()
            or page.main_frame is not frame
            or frame.url.partition("#")[0] != target
            or not relaxed
            and (page.frames != [frame] or set(portal.context.pages) != portal.pages)
        ):
            log_entry(
                "failed",
                portal.entry_reason
                or portal.entry_document_reason
                or (
                    "PORTAL_ENTRY_UNCONFIRMED_DOCUMENT"
                    if portal.entry_released
                    else "PORTAL_ENTRY_NAV_MISMATCH"
                ),
            )
            need(not cleanup_failed, "CLEANUP_FAILED")
            raise Stop(portal.violation or "PREP_FAILED")
        need(not cleanup_failed, "CLEANUP_FAILED")
        await portal.sources(page, portal.cdp, stable=True)
        log_entry("success", "OK")

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
        if (
            not self.identity_verified
            or self.native is None
            or self.violation
            or not self.attachment_cleanup_proven
            or self.job is None
        ):
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
            "root": (
                {
                    "pid": self.job.pid,
                    "created": self.job.created,
                    "executable": self.job.executable,
                }
                if self.job.root is not None
                else None
            ),
            "active": 0,
            "members": [],
        }
        if not self.job.close_handles():
            raise BrowserReadExecutionError("denied")
        self.owned_resource_cleanup_proven = True
