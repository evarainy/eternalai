"""Pipe adapter for one zero-LLM FILL_STOP inside the existing durable Run worker.

Native browser bindings and approved values stay in local memory/stdin. The
supervisor owns its worker/driver and a new Job; explicit close releases the window.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import signal
import subprocess
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast
from urllib.parse import urlsplit
from uuid import uuid4

from app.browser_skill.models import BrowserOwner
from app.browser_skill.publication_contracts import BrowserPublicationManifest, canonical_json
from app.infra.browser.d_form_cli import CODES, cancel_run, validate
from app.infra.browser.d_form_manifest import FIELDS
from app.infra.browser.d_form_owned_browser import (
    OwnedDFormBrowser,
    OwnedOALoginContract,
    log_d_form_fence_failure,
    log_d_form_stage,
    owned_provider_digest,
)
from app.infra.browser.oa_browser_login import RunBoundOACredentialVault
from app.infra.persistence.browser.crypto import BrowserClaimProofContext
from app.infra.persistence.browser.payload_crypto import BrowserPayloadCipher
from app.ports.browser_fill_stop import FillStopChecks, FillStopCode, FillStopReport
from app.ports.browser_read_execution import (
    BrowserCaptureResolution,
    BrowserReadExecutionError,
    BrowserReadOutcome,
    BrowserWorkerCheckpoint,
)
from app.ports.browser_run_store import RunCleanup, RunSnapshot
from app.ports.browser_store import (
    BrowserLeaseClaim,
    BrowserLeaseError,
    BrowserLeaseStorePort,
    BrowserProviderExpectation,
    BrowserProviderFact,
)
from app.ports.credential_vault import BrowserAuthFact, BrowserBindingFact

# Approved bound of one D execution. The Run, lease and HumanGate all end at the
# verified EternalAI session expiry, so a start needs this window plus settlement.
_D_WINDOW_SECONDS = 300
_D_SETTLE_SECONDS = 30


@dataclass(frozen=True, slots=True, repr=False)
class DFormTarget:
    """Host-resolved exact Run/subject/target; never deserialized from HTTP."""

    owner: BrowserOwner
    task_id: str
    run_id: str
    binding: BrowserBindingFact
    native: Mapping[str, object] = field(repr=False)


class DFormTargetAuthority(Protocol):
    async def resolve_binding(self, run: RunSnapshot) -> BrowserBindingFact:
        """Read only current binding facts; never create or attach a resource."""
        ...


@dataclass(frozen=True, slots=True, repr=False)
class DFormOptions:
    python: Path
    receipts_root: Path
    targets: DFormTargetAuthority
    proof_context: BrowserClaimProofContext
    provider_key: str = "d_form_fill_stop"
    chromium: Path | None = field(default=None, repr=False)
    chromium_sha256: str | None = field(default=None, repr=False)
    owned_runs_root: Path | None = field(default=None, repr=False)
    login_contract: OwnedOALoginContract | None = field(default=None, repr=False)


@dataclass(slots=True, repr=False)
class _State:
    run: RunSnapshot
    binding: BrowserBindingFact
    resource_ref: bytes | None
    receipt_path: Path
    event_name: str
    began: float = field(default_factory=time.monotonic)
    target: DFormTarget | None = None
    claim: BrowserLeaseClaim | None = None
    owned_browser: OwnedDFormBrowser | None = None
    fence_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    lease_quarantined: bool = False
    attachment_cleanup_proven: bool = False
    owned_resource_cleanup_proven: bool = False
    process: asyncio.subprocess.Process | None = None
    owned: dict[int, tuple[float, Path]] = field(default_factory=dict)
    stages: dict[int, Mapping[str, object]] = field(default_factory=dict)
    output_invalid: bool = False
    connected: bool = False
    input_sent: bool = False
    cleanup_failure_logged: bool = False
    cancelled: bool = False
    stopped: bool = False
    cleanup_proven: bool = False
    released: bool = False
    acquired_evidence: bytes | None = None
    release_evidence: bytes | None = None
    report: FillStopReport | None = None
    outcome: BrowserReadOutcome | None = None


class DFormRuntime:
    """No scheduling loop; execute is invoked once by BrowserReadWorker."""

    def __init__(self, manifest: BrowserPublicationManifest, options: DFormOptions) -> None:
        if (
            options.chromium is None
            or options.chromium_sha256 is None
            or options.owned_runs_root is None
        ):
            raise ValueError("browser_fill_stop_installation_invalid")
        self.manifest, self.options = manifest, options
        self.manifest_digest = owned_provider_digest(
            manifest.digest,
            options.chromium_sha256,
        )
        self._states: dict[str, _State] = {}
        self._leases: BrowserLeaseStorePort | None = None
        self._cipher: BrowserPayloadCipher | None = None
        self._result_key: bytes | None = None
        self._assert_publication: Callable[[RunSnapshot], Awaitable[None]] | None = None
        self._cleanup_authorize: Callable[[RunSnapshot], Awaitable[None]] | None = None
        self._login_vault: Callable[[RunSnapshot], RunBoundOACredentialVault] | None = None

    def install_login_vault(
        self,
        factory: Callable[[RunSnapshot], RunBoundOACredentialVault],
    ) -> None:
        """Private host seam; no resource, credential read or login at installation."""
        self._login_vault = factory

    async def check_login_lease(self, run: RunSnapshot) -> None:
        """Mandatory trusted pre-login check; renew checks exact claim in DB.

        This does not authorize a resource or manufacture an acquired subject.
        The updated revision remains in the sole runtime owner for later fences.
        """
        state = self._state(run)
        async with state.fence_lock:
            await self._renew_login_claim(state, run)

    async def _renew_login_claim(self, state: _State, run: RunSnapshot) -> None:
        if self._leases is None or state.claim is None or state.released or state.cancelled:
            raise BrowserLeaseError("browser_login_lease_stale")
        state.claim = await self._leases.renew(state.claim, ttl_seconds=120)

    def install(
        self,
        *,
        cipher: BrowserPayloadCipher,
        result_key: bytes,
        publication: Callable[[RunSnapshot], Awaitable[None]],
    ) -> None:
        self._cipher, self._result_key, self._assert_publication = cipher, result_key, publication

    def install_leases(self, leases: BrowserLeaseStorePort) -> None:
        self._leases = leases

    def _state(self, run: RunSnapshot) -> _State:
        state = self._states.get(run.run_id)
        if (
            state is None
            or state.run.admission != run.admission
            or state.run.worker_epoch != run.worker_epoch
        ):
            raise BrowserReadExecutionError("stale")
        return state

    def _inventory(self, state: _State) -> None:
        """Remember the worker's descendants so exit can be awaited; no allowlist."""
        import psutil

        process = state.process
        if process is None or process.returncode is not None:
            return
        for child in psutil.Process(process.pid).children(recursive=True):
            try:
                state.owned[child.pid] = (child.create_time(), Path(child.exe()).resolve())
            except psutil.NoSuchProcess:
                continue

    def _exited(self, state: _State) -> bool:
        import psutil

        if state.process is not None and state.process.returncode is None:
            return False
        for pid, (created, executable) in state.owned.items():
            try:
                process = psutil.Process(pid)
                if process.create_time() == created and Path(process.exe()).resolve() == executable:
                    return False
            except psutil.NoSuchProcess:
                continue
            except psutil.Error:
                return False
        return True

    async def _output(self, state: _State) -> None:
        assert state.process is not None and state.process.stdout is not None
        try:
            count = 0
            while line := await state.process.stdout.readline():
                count += 1
                item = (
                    validate(line.decode("utf-8", errors="strict")) if len(line) <= 2048 else None
                )
                if count > 32 or item is None:
                    state.output_invalid = True
                    continue
                log_d_form_stage(
                    f"worker_{item['stage']}",
                    "success" if item["status"] == "PASS" else "failed",
                    item["error_code"],
                    state.began,
                )
                state.stages[item["stage"]] = item
                if item["stage"] == 3 and item["status"] == "PASS":
                    state.connected = True
        except Exception:
            state.output_invalid = True

    async def _fence(
        self,
        state: _State,
        checkpoint: BrowserWorkerCheckpoint,
        *,
        trace_control: bool = False,
    ) -> None:
        step = "control_fence_lock"

        def trace(stage: str, status: str) -> None:
            nonlocal step
            step = stage
            if trace_control:
                log_d_form_stage(stage, status, "OK", state.began)

        try:
            trace("control_fence_lock", "started")
            async with state.fence_lock:
                trace("control_fence_lock", "success")
                trace("control_fence_refresh", "started")
                state.run = await checkpoint.refresh()
                trace("control_fence_refresh", "success")
                assert self._leases is not None
                if state.claim is not None:
                    trace("control_fence_lease", "started")
                    state.claim = await self._leases.renew(state.claim, ttl_seconds=120)
                    trace("control_fence_lease", "success")
        except Exception as error:
            log_d_form_fence_failure(step, error, state.began)
            raise

    async def _signal(self, state: _State) -> None:
        state.cancelled = True
        if state.process is None or state.process.returncode is not None:
            return
        if os.name == "nt":
            cancel_run(state.event_name)
        else:
            state.process.send_signal(signal.SIGINT)

    async def _stop(self, state: _State) -> None:
        import psutil

        if state.process is not None and state.process.stdin is not None and not state.input_sent:
            state.process.stdin.close()
        await self._signal(state)
        if state.owned_browser is not None:
            await state.owned_browser.stop_automation()
        deadline = time.monotonic() + 40
        # D's event/30-second supervisor barrier owns any helper reclamation.
        # This adapter never force-kills an unknown process or attached Chrome.
        while (
            state.process is not None
            and state.process.returncode is None
            and time.monotonic() < deadline
        ):
            try:
                self._inventory(state)
                await self._signal(state)
            except (psutil.Error, BrowserReadExecutionError):
                state.output_invalid = True
            await asyncio.sleep(0.1)
        state.stopped = self._exited(state)

    def _report(self, state: _State, code: FillStopCode | None = None) -> FillStopReport:
        """Project a closed receipt; never store or forward the original mapping."""
        checks = FillStopChecks()
        attempts = completed = 0
        verification: Literal["verified", "mismatch", "incomplete", "unknown"] = "unknown"
        status: Literal["filled_stopped", "failed", "cancelled", "unknown"] = "unknown"
        error: FillStopCode = code or "RECEIPT_INVALID"
        try:
            path = state.receipt_path
            if path.is_symlink() or path.stat().st_size > 16384:
                raise ValueError
            raw = json.loads(path.read_text(encoding="utf-8"))
            if (
                type(raw) is not dict
                or raw.get("mode") != "FILL_STOP"
                or raw.get("model_requests") != 0
                or type(raw.get("model_requests")) is not int
                or raw.get("submit_actions") != 0
                or type(raw.get("submit_actions")) is not int
                or raw.get("recording_enabled") is not False
                or raw.get("reporter_displayed") is not False
                or raw.get("browser_left_open") is not True
                or raw.get("error_code") not in CODES
            ):
                raise ValueError
            attempts, completed = raw["action_attempts"], raw["actions_completed"]
            if (
                type(attempts) is not int
                or type(completed) is not int
                or not 0 <= completed <= attempts <= 4
            ):
                raise ValueError
            values = raw["checks"]
            if type(values) is not dict or not set(values) <= {
                "CATEGORY",
                "POSITION",
                "DESCRIPTION",
            }:
                raise ValueError
            if any(type(value) is not bool for value in values.values()):
                raise ValueError
            checks = FillStopChecks(
                category=values.get("CATEGORY"),
                position=values.get("POSITION"),
                description=values.get("DESCRIPTION"),
            )
            executed = raw["executed_actions"]
            counts = raw["event_counts"]
            required = {"CATEGORY_SELECT", "POSITION_INPUT", "DESCRIPTION_INPUT"}
            if (
                type(executed) is not list
                or type(counts) is not dict
                or len(executed) != completed
                or len(set(executed)) != completed
                or not set(executed) <= required | {"CATEGORY_OPEN"}
                or set(counts) != set(executed)
                or any(type(v) is not int or not 1 <= v <= 100 for v in counts.values())
            ):
                raise ValueError
            verification = "mismatch" if False in checks.model_dump().values() else "incomplete"
            error = code or cast(FillStopCode, raw["error_code"])
            status = "cancelled" if error == "CANCELLED" else "failed"
            if (
                code is None
                and raw["passed"] is True
                and error == "OK"
                and state.process is not None
                and state.process.returncode == 0
                and not state.output_invalid
                and state.connected
                and state.acquired_evidence is not None
                and state.attachment_cleanup_proven
                and all(
                    state.stages.get(i, {}).get("status") == "PASS" for i in (1, 3, 4, 5, 6, 7, 8)
                )
                and all(v is True for v in checks.model_dump().values())
                and required <= set(executed)
                and attempts == completed
            ):
                verification, status = "verified", "filled_stopped"
            elif error == "OK":
                error = "RECEIPT_INVALID"
        except (OSError, ValueError, KeyError, TypeError):
            error = code or "RECEIPT_INVALID"
            checks, attempts, completed = FillStopChecks(), 0, 0
            verification, status = "unknown", "unknown"
        if (
            state.output_invalid
            or state.process is None
            or state.process.returncode is None
            or 8 not in state.stages
        ):
            verification, status = "unknown", "unknown"
        return FillStopReport(
            execution_status=status,
            verification=verification,
            field_checks=checks,
            actions_attempted=attempts,
            actions_completed=completed,
            seconds=min(7200.0, max(0.0, time.monotonic() - state.began)),
            error_code=error,
            cleanup_confirmed=(
                state.attachment_cleanup_proven and state.owned_resource_cleanup_proven
            ),
        )

    async def execute(
        self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint
    ) -> BrowserReadOutcome:
        began = time.monotonic()
        current_stage = "precondition"
        log_d_form_stage(current_stage, "started", "OK", began)
        if run.run_id in self._states:
            log_d_form_stage(current_stage, "failed", "denied", began)
            raise BrowserReadExecutionError("denied")
        assert self._cipher is not None and self._result_key is not None
        assert self._leases is not None and self._assert_publication is not None
        assert self._login_vault is not None and self.options.login_contract is not None
        import psutil

        current_stage = "binding"
        log_d_form_stage(current_stage, "started", "OK", began)
        try:
            binding = await self.options.targets.resolve_binding(run)
        except asyncio.CancelledError:
            log_d_form_stage("cancel", "cancelled", "CANCELLED", began)
            raise
        except Exception as error:
            log_d_form_stage(current_stage, "failed", getattr(error, "code", "error"), began)
            raise
        log_d_form_stage(current_stage, "success", "OK", began)
        state = _State(
            run,
            binding,
            None,
            self.options.receipts_root / (uuid4().hex + ".json"),
            "Local\\EAI_D_CANCEL_" + uuid4().hex,
        )
        self._states[run.run_id] = state
        output: asyncio.Task[None] | None = None
        failure: FillStopCode | None = None
        try:
            # Before any lease/resource: the verified session's own expiry bounds
            # this Run. Never extend it; ask for a new login instead.
            remaining = (run.admission.auth_expires_at - datetime.now(UTC)).total_seconds()
            if remaining < _D_WINDOW_SECONDS + _D_SETTLE_SECONDS:
                log_d_form_stage("precondition", "failed", "AUTH_WINDOW_INSUFFICIENT", began)
                state.report = FillStopReport(
                    execution_status="failed",
                    verification="incomplete",
                    field_checks=FillStopChecks(),
                    actions_attempted=0,
                    actions_completed=0,
                    seconds=min(7200.0, max(0.0, time.monotonic() - state.began)),
                    error_code="LOGIN_REQUIRED",
                    cleanup_confirmed=False,
                )
                await checkpoint.record_fill_stop(state.report)
                return BrowserReadOutcome("not_sent", "incomplete", dispatch_failure_code="denied")
            private = self._cipher.decrypt_input(run.admission)
            arguments = private.get("arguments")
            if (
                type(arguments) is not dict
                or set(arguments) != set(FIELDS)
                or any(
                    type(v) is not str or not 0 < len(v.strip()) <= 500 or "\x00" in v
                    for v in arguments.values()
                )
                or len(arguments["category"]) > 100
                or len(arguments["category"].splitlines()) != 1
            ):
                log_d_form_stage("precondition", "failed", "INPUT_INVALID", began)
                raise BrowserReadExecutionError("denied")
            current_stage = "lease"
            log_d_form_stage(current_stage, "started", "OK", began)
            state.claim = await self._leases.reserve(
                BrowserAuthFact(
                    run.owner,
                    None,
                    run.admission.auth_fingerprint,
                    run.admission.auth_expires_at,
                    run.run_id,
                    "verified-session-v1",
                ),
                binding,
                self.options.provider_key,
                ttl_seconds=120,
            )
            state.run = await checkpoint.attach_lease(
                provider_key=self.options.provider_key,
                provider_manifest_digest=self.manifest_digest,
                lease_epoch=state.claim.lease_epoch,
            )
            state.run = await checkpoint.start_execution()
            state.run = await checkpoint.reserve_fill_stop()
            state.claim = await self._leases.start_acquisition(state.claim)
            await self._assert_publication(state.run)
            await self._fence(state, checkpoint)
            assert self.options.chromium is not None and self.options.owned_runs_root is not None
            log_d_form_stage(current_stage, "success", "OK", began)
            current_stage = "page"
            state.owned_browser = OwnedDFormBrowser(
                chromium=self.options.chromium,
                root=self.options.owned_runs_root,
                origin=self.manifest.site.source.origin,
                contract=self.options.login_contract,
            )

            async def fence() -> None:
                await self._fence(state, checkpoint, trace_control=True)

            await state.owned_browser.create_and_login(self._login_vault(state.run), fence)
            current_stage = "portal"
            await state.owned_browser.prepare_portal(fence)
            native = cast(dict[str, object], state.owned_browser.native)
            state.target = DFormTarget(run.owner, run.task_id, run.run_id, binding, dict(native))
            state.resource_ref = canonical_json(
                {
                    "run": run.run_id,
                    "task": run.task_id,
                    "owner": run.owner.model_dump(mode="json"),
                    "native": dict(native),
                    "job": state.owned_browser.job.job_ref,
                    "binding_id": binding.binding_id,
                    "binding_revision": binding.binding_revision,
                    "lease_epoch": state.claim.lease_epoch,
                    "operation_id": state.claim.operation_id,
                    "holder_id": state.claim.holder_id,
                    "chromium_sha256": self.options.chromium_sha256,
                    "ownership": "created_by_this_runtime",
                }
            ).encode()
            await fence()
            state.acquired_evidence = self.options.proof_context.digest(
                "d-form-acquired.v1",
                state.resource_ref,
            )
            state.claim = await self._leases.record_acquired(state.claim, state.acquired_evidence)
            await fence()
            origin = urlsplit(self.manifest.site.source.origin)
            request = {
                "mode": "FILL_STOP",
                "binding": {
                    **native,
                    "source_port": origin.port or (443 if origin.scheme == "https" else 80),
                },
                "inputs": arguments,
                "local_gate": False,
            }
            payload = canonical_json(request).encode("utf-8")
            env = {
                key: os.environ[key]
                for key in ("SystemRoot", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP")
                if key in os.environ
            }
            env.update(
                PYTHON_DOTENV_DISABLED="1", OTEL_SDK_DISABLED="true", ANONYMIZED_TELEMETRY="false"
            )
            current_stage = "fill"
            log_d_form_stage(current_stage, "started", "OK", began)
            state.process = await asyncio.create_subprocess_exec(
                str(self.options.python),
                "-B",
                str(Path(__file__).with_name("d_form_cli.py")),
                "--receipt",
                str(state.receipt_path),
                "--cancel-event",
                state.event_name,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
                limit=2048,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW"),
            )
            root = psutil.Process(state.process.pid)
            state.owned[root.pid] = (root.create_time(), Path(root.exe()).resolve())
            output = asyncio.create_task(self._output(state))
            await self._fence(state, checkpoint)
            # One HumanGate reservation barrier immediately before the only input.
            await checkpoint.assert_fill_stop_reservation()
            # The server-verified Chat confirmation already covers these exact
            # inputs. Standalone D callers retain their native coordination gate.
            log_d_form_stage("chat_confirmation", "success", "OK", began)
            assert state.process.stdin is not None
            state.input_sent = True
            state.process.stdin.write(payload)
            await state.process.stdin.drain()
            state.process.stdin.close()
            while state.process.returncode is None:
                if time.monotonic() - state.began > _D_WINDOW_SECONDS:
                    failure = "EXECUTION_TIMEOUT"
                    await self._stop(state)
                    break
                if state.output_invalid:
                    failure = "RECEIPT_INVALID"
                    await self._stop(state)
                    break
                await self._fence(state, checkpoint)
                self._inventory(state)
                await asyncio.sleep(0.5)
            if state.process.returncode is not None:
                await state.process.wait()
            if output is not None:
                await asyncio.wait_for(asyncio.shield(output), 2)
            # Acquisition can finish between the last heartbeat and process exit.
            if state.connected and state.acquired_evidence is None:
                await self._fence(state, checkpoint)
            state.stopped = self._exited(state)
            state.attachment_cleanup_proven = (
                state.stopped
                and not state.output_invalid
                and state.stages.get(7, {}).get("status") == "PASS"
                and state.process.returncode in (0, 2)
            )
            current_stage = "receipt"
            log_d_form_stage(current_stage, "started", "OK", began)
            state.report = self._report(state, failure)
            log_d_form_stage(
                current_stage,
                "success" if state.report.verification == "verified" else "failed",
                state.report.error_code,
                began,
            )
            await checkpoint.record_fill_stop(state.report)
            state.run = await checkpoint.refresh(allow_cancel=True)
            if state.report.verification != "verified":
                effect: Literal["not_sent", "acknowledged", "unknown"] = (
                    "unknown"
                    if (
                        state.report.execution_status == "unknown"
                        or state.report.actions_attempted != state.report.actions_completed
                    )
                    else ("acknowledged" if state.report.actions_completed else "not_sent")
                )
                return BrowserReadOutcome(
                    effect,
                    "mismatch" if state.report.verification == "mismatch" else "incomplete",
                    dispatch_failure_code="timeout"
                    if failure == "EXECUTION_TIMEOUT"
                    else "invalid_response",
                )
            value = {
                "mode": "FILL_STOP",
                "execution_status": "filled_stopped",
                "field_checks": state.report.field_checks.model_dump(),
                "actions_completed": state.report.actions_completed,
                "submit_actions": 0,
                "model_requests": 0,
                "seconds": state.report.seconds,
            }
            evidence = self.options.proof_context.digest(
                "d-form-verification.v1",
                canonical_json(
                    {
                        "resource": hashlib.sha256(state.resource_ref).hexdigest(),
                        "report": state.report.model_dump(mode="json"),
                        "stages": state.stages,
                        "worker_epoch": state.run.worker_epoch,
                        "lease_epoch": state.run.lease_epoch,
                    }
                ).encode(),
            )
            digest = hmac.digest(
                self._result_key,
                b"d-form-result-v1\x00"
                + run.run_id.encode()
                + b"\x00"
                + canonical_json(value).encode(),
                hashlib.sha256,
            )
            envelope = self._cipher.encrypt_result(
                run.admission, value, evidence_digest=evidence, result_digest=digest
            )
            state.outcome = BrowserReadOutcome(
                "acknowledged", "verified", envelope, digest, evidence
            )
            return state.outcome
        except asyncio.CancelledError:
            log_d_form_stage("cancel", "cancelled", "CANCELLED", began)
            await self._stop(state)
            raise
        except (BrowserReadExecutionError, BrowserLeaseError, psutil.Error) as error:
            failed_stage = (
                state.owned_browser.current_stage
                if (state.owned_browser is not None and current_stage in {"page", "portal"})
                else current_stage
            )
            log_d_form_stage(failed_stage, "failed", getattr(error, "code", "error"), began)
            await self._stop(state)
            if not state.input_sent:
                if (
                    state.owned_browser is not None
                    and state.owned_browser.preparation_error in CODES
                ):
                    state.report = FillStopReport(
                        execution_status="failed",
                        verification="incomplete",
                        field_checks=FillStopChecks(),
                        actions_attempted=0,
                        actions_completed=0,
                        seconds=min(7200.0, max(0.0, time.monotonic() - state.began)),
                        error_code=cast(FillStopCode, state.owned_browser.preparation_error),
                        cleanup_confirmed=False,
                    )
                    await checkpoint.record_fill_stop(state.report)
                    return BrowserReadOutcome(
                        "not_sent", "incomplete", dispatch_failure_code="invalid_response"
                    )
                raise BrowserReadExecutionError("denied") from None
            if (
                output is not None
                and state.process is not None
                and state.process.returncode is not None
            ):
                try:
                    await asyncio.wait_for(asyncio.shield(output), 2)
                except Exception:
                    state.output_invalid = True
            state.attachment_cleanup_proven = (
                self._exited(state)
                and not state.output_invalid
                and state.stages.get(7, {}).get("status") == "PASS"
                and state.process is not None
                and state.process.returncode in (0, 2)
            )
            cancelled = isinstance(error, BrowserReadExecutionError) and error.code == "cancelled"
            state.report = self._report(state, "CANCELLED" if cancelled else "AUTHORITY_LOST")
            if cancelled:
                await checkpoint.record_fill_stop(state.report)
                cancel_effect: Literal["not_sent", "acknowledged", "unknown"] = (
                    "unknown"
                    if (
                        state.report.execution_status == "unknown"
                        or state.report.actions_attempted != state.report.actions_completed
                    )
                    else ("acknowledged" if state.report.actions_completed else "not_sent")
                )
                return BrowserReadOutcome(
                    cancel_effect, "incomplete", dispatch_failure_code="cancelled"
                )
            return BrowserReadOutcome("unknown", None, dispatch_failure_code="denied")
        except Exception as error:
            failed_stage = (
                state.owned_browser.current_stage
                if (state.owned_browser is not None and current_stage in {"page", "portal"})
                else current_stage
            )
            log_d_form_stage(failed_stage, "failed", getattr(error, "code", "error"), began)
            await self._stop(state)
            return BrowserReadOutcome(
                "unknown" if state.input_sent else "not_sent",
                None,
                dispatch_failure_code="invalid_response",
            )
        finally:
            if state.process is not None and state.process.stdin is not None:
                state.process.stdin.close()
            if output is not None and not output.done():
                output.cancel()
                await asyncio.gather(output, return_exceptions=True)

    async def check_verified_candidate(self, candidate: RunSnapshot) -> None:
        state = self._state(candidate)
        outcome = state.outcome
        if (
            outcome is None
            or candidate.verification != "verified"
            or state.report is None
            or state.report.verification != "verified"
            or not state.attachment_cleanup_proven
            or candidate.worker_id != state.run.worker_id
            or candidate.provider_key != self.options.provider_key
            or candidate.lease_epoch != state.run.lease_epoch
            or candidate.provider_manifest_digest != self.manifest_digest
            or candidate.protected_result != outcome.result
            or candidate.result_digest != outcome.result_digest
            or candidate.verification_evidence_digest != outcome.evidence_digest
        ):
            raise BrowserReadExecutionError("denied")

    def verified_persisted(self, run: RunSnapshot) -> None:
        self._state(run).outcome = None

    async def lookup_capture(
        self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint
    ) -> BrowserCaptureResolution:
        return BrowserCaptureResolution("unsupported")

    async def send_capture(
        self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint
    ) -> BrowserCaptureResolution:
        return BrowserCaptureResolution("unsupported")

    async def stop(self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint) -> None:
        await checkpoint.refresh(allow_cancel=True)
        if run.run_id not in self._states and run.lease_epoch is None:
            return
        await self._stop(self._state(run))

    async def cleanup(self, run: RunSnapshot) -> RunCleanup:
        state = self._states.get(run.run_id)
        if state is None:
            return "released" if run.lease_epoch is None else "quarantined"
        async with state.cleanup_lock:
            if state.run.admission != run.admission:
                raise BrowserReadExecutionError("stale")
            if (
                run.status in {"completed", "failed", "cancelled"}
                and state.owned_browser is not None
                and state.claim is not None
            ):
                if state.process is not None and state.process.returncode is None:
                    await self._stop(state)
                return await self._close_owned_state(run, state)
            return await self._cleanup_state(run, state)

    async def _cleanup_state(self, run: RunSnapshot, state: _State) -> RunCleanup:
        if state.run.admission != run.admission or self._leases is None:
            raise BrowserReadExecutionError("stale")
        if state.released:
            return "released"
        if state.process is not None and state.process.returncode is None:
            await self._stop(state)
        state.attachment_cleanup_proven = (
            self._exited(state)
            and not state.output_invalid
            and (state.owned_browser is None or state.owned_browser.attachment_cleanup_proven)
            and (
                state.process is None
                or not state.input_sent
                or state.stages.get(7, {}).get("status") == "PASS"
                and state.process.returncode in (0, 2)
            )
        )
        state.cleanup_proven = (
            state.attachment_cleanup_proven and state.owned_resource_cleanup_proven
        )
        if state.claim is None:
            absent = state.owned_browser is None and state.process is None
            return "released" if absent else "quarantined"
        if state.owned_browser is None:
            # Lease store permits this only for a committed never-sent reservation.
            if await self._leases.cleanup(state.claim):
                state.released = True
                state.cleanup_proven = True
                return "released"
        if not state.cleanup_proven or state.resource_ref is None:
            if not state.lease_quarantined:
                state.claim = await self._leases.quarantine(state.claim)
                state.lease_quarantined = True
            return "quarantined"
        state.release_evidence = self.options.proof_context.digest(
            "d-form-released.v1", state.resource_ref
        )
        if not await self._leases.cleanup(state.claim, state.release_evidence):
            return "quarantined"
        state.released = True
        return "released"

    def install_cleanup_authority(
        self,
        authorize: Callable[[RunSnapshot], Awaitable[None]],
    ) -> None:
        self._cleanup_authorize = authorize

    async def close_owned_browser(self, run: RunSnapshot) -> RunCleanup:
        """Explicit terminal cleanup, independent of the old business session."""
        state = self._state(run)
        async with state.cleanup_lock:
            return await self._close_owned_state(run, state)

    async def _close_owned_state(self, run: RunSnapshot, state: _State) -> RunCleanup:
        """Share exact-Run close/proof/release between terminal and manual cleanup."""
        if state.run.admission != run.admission:
            raise BrowserReadExecutionError("stale")
        if state.released:
            return "released"
        if (
            run.status not in {"completed", "failed", "cancelled"}
            or self._cleanup_authorize is None
            or state.owned_browser is None
            or state.claim is None
            or not self._exited(state)
        ):
            raise BrowserReadExecutionError("denied")
        stage = "cleanup_owned"
        try:
            await self._cleanup_authorize(run)
            if not state.owned_resource_cleanup_proven:
                await state.owned_browser.close_owned()
                state.owned_resource_cleanup_proven = (
                    state.owned_browser.owned_resource_cleanup_proven
                )
                log_d_form_stage(stage, "success", "OK", state.began)
            if state.resource_ref is None:
                # Actual Job/root close proof, no fabricated native/context record.
                state.resource_ref = canonical_json(
                    {
                        "run": run.run_id,
                        "task": run.task_id,
                        "owner": run.owner.model_dump(mode="json"),
                        "binding_revision": state.binding.binding_revision,
                        "closed_creation": state.owned_browser.closed_job_identity,
                    }
                ).encode()
            await self._cleanup_authorize(run)
            stage = "cleanup_release"
            result = await self._cleanup_state(run, state)
            if result == "released":
                log_d_form_stage(stage, "success", "OK", state.began)
            elif not state.cleanup_failure_logged:
                log_d_form_stage(stage, "failed", "unavailable", state.began)
                state.cleanup_failure_logged = True
            return result
        except Exception:
            if not state.cleanup_failure_logged:
                log_d_form_stage(stage, "failed", "error", state.began)
                state.cleanup_failure_logged = True
            raise

    async def check_cancel(self, run: RunSnapshot) -> None:
        state = self._state(run)
        if (
            not state.cancelled
            or not self._exited(state)
            or state.owned_browser is not None
            and not state.owned_browser.attachment_cleanup_proven
        ):
            raise BrowserReadExecutionError("denied")

    def _claim_state(self, claim: BrowserLeaseClaim, resource: bytes | None) -> _State:
        state = self._states.get(claim.auth.authorization_run_id or "")
        if (
            state is None
            or state.claim is None
            or state.run.owner != claim.auth.owner
            or state.claim != claim
            or resource is not None
            and resource != state.resource_ref
        ):
            raise BrowserLeaseError("browser_fill_stop_claim_mismatch")
        return state

    async def check_subject(self, claim: BrowserLeaseClaim, resource_ref: bytes) -> None:
        state = self._claim_state(claim, resource_ref)
        if state.owned_browser is None or state.target is None:
            raise BrowserLeaseError("browser_fill_stop_subject_unknown")

    async def verify(
        self, expected: BrowserProviderExpectation, evidence: bytes
    ) -> BrowserProviderFact:
        state = self._claim_state(expected.claim, expected.resource_ref)
        if (
            expected.manifest_digest != self.manifest_digest
            or expected.challenge
            != self.options.proof_context.challenge(expected.claim, self.manifest_digest)
        ):
            raise BrowserLeaseError("browser_fill_stop_proof_mismatch")
        outcome: Literal["acquired", "released"]
        if state.acquired_evidence is not None and hmac.compare_digest(
            evidence, state.acquired_evidence
        ):
            # Acquired evidence is assigned only after resource_ref at the acquire point.
            await self.check_subject(expected.claim, cast(bytes, state.resource_ref))
            outcome = "acquired"
        elif (
            state.release_evidence is not None
            and hmac.compare_digest(evidence, state.release_evidence)
            and state.cleanup_proven
            and self._exited(state)
        ):
            outcome = "released"
        else:
            raise BrowserLeaseError("browser_fill_stop_proof_unknown")
        return BrowserProviderFact(
            expected.claim.provider_key,
            expected.claim.operation_id,
            expected.claim.holder_id,
            expected.claim.lease_epoch,
            expected.challenge,
            self.manifest_digest,
            cast(bytes, state.resource_ref),
            outcome,
        )
