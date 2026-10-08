"""Existing Playwright Executor/IndependentVerifier bridge for durable READ_ONLY Runs.

Composition must supply a registered real execution factory and an authoritative
provider lifecycle implementation. There is deliberately no synthetic fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Protocol, get_args

from jsonschema import Draft202012Validator
from referencing import Registry

from app.browser_skill.executor import BrowserExecutor, DecisionContextFactory, ReadAwaitStage
from app.browser_skill.models import (
    ActionCommand,
    BrowserOperationError,
    BrowserSessionRef,
    BrowserSkill,
    ConfirmedBusinessKey,
    DecisionError,
    DecisionResult,
    DecisionStatus,
    DispatchPermit,
    ExecutionContext,
    ParameterPurpose,
    ParameterRef,
    ReadSpec,
    ScopeBinding,
    SealedParameter,
)
from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.browser_skill.runtime import _BrowserReadDeferred
from app.browser_skill.site_rules import RegisteredQueryReadRule
from app.browser_skill.verifier import IndependentVerifier, ReadSpecResolver, failure
from app.infra.browser.playwright_dom_rules import RegisteredDOMRules
from app.infra.browser.playwright_observer import PlaywrightObserver
from app.infra.browser.playwright_web_adapter import (
    BusinessRegistry,
    OperationAuthority,
    PlaywrightWebAdapter,
    RegisteredExecution,
)
from app.infra.persistence.browser.payload_crypto import BrowserPayloadCipher
from app.ports.browser import DecisionProvider, SiteAdapter
from app.ports.browser_publication_store import BrowserPublicationStorePort
from app.ports.browser_read_execution import (
    BrowserCaptureResolution,
    BrowserReadExecutionError,
    BrowserReadOutcome,
    BrowserWorkerCheckpoint,
)
from app.ports.browser_run_store import RunCleanup, RunEffect, RunSnapshot


class BrowserOutputProjector(Protocol):
    def __call__(self, fields: Mapping[str, str]) -> Mapping[str, object]:
        """Frozen registered projection only; never log or retain private fields."""
        ...


@dataclass(frozen=True, slots=True, repr=False)
class RegisteredReadExecution:
    session: BrowserSessionRef
    context: ExecutionContext
    confirmed_key: ConfirmedBusinessKey
    registry: BusinessRegistry
    observer: PlaywrightObserver
    rules: tuple[RegisteredDOMRules, ...]
    site: SiteAdapter
    decision: DecisionProvider
    resolver: ReadSpecResolver
    decision_contexts: DecisionContextFactory
    project_output: BrowserOutputProjector
    bind_publication_guard: Callable[
        [Callable[[RunSnapshot], Awaitable[None]]], ExecutionContext
    ] | None = None
    bind_operation_authority: Callable[[ExecutionContext], OperationAuthority] | None = None
    authority_diagnostic: Callable[[], dict[str, int]] | None = None
    decision_http_diagnostic: Callable[[], dict[str, str | int]] | None = None


class BrowserReadExecutionFactory(Protocol):
    async def open(
        self, run: RunSnapshot, manifest: BrowserPublicationManifest,
        private_input: Mapping[str, object], checkpoint: BrowserWorkerCheckpoint,
    ) -> RegisteredReadExecution:
        """Acquire/restore the actual exact-owner browser and verify live SUBJECT.

        Fence each provider await; attach the proven lease through checkpoint.
        Restoration without a validated profile never grants business authority.
        All context callbacks must enforce current binding/lease/subject and the
        real dispatch barrier. The bridge adds Run/publication fences to them.
        The projector, DOM rules and source must be registered to this manifest.
        Query detail confirmation must resolve the explicit protected
        arguments.business_key; never infer it from a model choice or DOM row.
        """
        ...


class BrowserReadLifecycle(Protocol):
    async def lookup_capture(
        self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint,
    ) -> BrowserCaptureResolution: ...

    async def send_capture(
        self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint,
    ) -> BrowserCaptureResolution: ...

    async def stop(self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint) -> None: ...

    async def cleanup(self, run: RunSnapshot) -> RunCleanup: ...


@dataclass(frozen=True, slots=True, repr=False)
class _VerifiedEmission:
    run: RunSnapshot
    outcome: BrowserReadOutcome
    deadline: float


class VerifiedBrowserReadExecution:
    def __init__(
        self, factory: BrowserReadExecutionFactory, lifecycle: BrowserReadLifecycle,
        publications: BrowserPublicationStorePort, cipher: BrowserPayloadCipher,
        *, result_digest_key: bytes,
        record_diagnostic: (
            Callable[[RunSnapshot, dict[str, str | int]], Awaitable[None]] | None
        ) = None,
    ) -> None:
        if type(result_digest_key) is not bytes or len(result_digest_key) != 32:
            raise ValueError("browser_result_digest_key_invalid")
        self._factory, self._lifecycle = factory, lifecycle
        self._publications, self._cipher = publications, cipher
        self._result_key = result_digest_key
        self._record_diagnostic = record_diagnostic
        self._emissions: dict[str, _VerifiedEmission] = {}
        self._observe_only_manifest: BrowserPublicationManifest | None = None
        self._observe_only_receipt: tuple[RunSnapshot, int] | None = None

    def _install_fixed_observe_only(self, manifest: BrowserPublicationManifest) -> None:
        """Trusted one-off installation; a version label alone cannot enable stopping."""
        from app.infra.browser.fixed_synthetic_seed import (
            build_fixed_synthetic_observe_only_source,
            observe_attempt_id_from_version,
        )
        from app.infra.browser.synthetic_configuration import synthetic_jev_manifest

        expected = build_fixed_synthetic_observe_only_source(
            synthetic_jev_manifest(),
            attempt_id=observe_attempt_id_from_version(manifest.skill.version),
        ).manifest
        if manifest != expected or self._observe_only_manifest is not None:
            raise ValueError("browser_observe_only_configuration_invalid")
        self._observe_only_manifest = expected

    def _consume_observe_only_receipt(self, run: RunSnapshot) -> int | None:
        receipt = self._observe_only_receipt
        if receipt is None:
            return None
        observed, elapsed_ms = receipt
        if (run.owner != observed.owner or run.task_id != observed.task_id
                or run.run_id != observed.run_id or run.lease_epoch != observed.lease_epoch
                or run.admission.publication_digest != observed.admission.publication_digest):
            raise ValueError("browser_observe_only_receipt_invalid")
        self._observe_only_receipt = None
        return elapsed_ms

    async def _record_failure_diagnostic(
        self, run: RunSnapshot, diagnostic: tuple[str, int, str] | None,
        *, web: PlaywrightWebAdapter | None = None, session_ref: str | None = None,
        decision: DecisionResult | None = None,
        decision_diagnostic: Mapping[str, str] | None = None,
        http_diagnostic: Mapping[str, str | int] | None = None,
        stage_diagnostic: Mapping[str, str | int] | None = None,
        bridge_diagnostic: Mapping[str, str | int] | None = None,
        verified: bool = False, refresh_calls: int | None = None,
        acquisition_ms: int | None = None,
        read_result: str = "failed", model_calls: int | None = None,
    ) -> None:
        if self._record_diagnostic is None:
            return
        try:
            # Existing Trace routing metadata is separate from these attributes.
            # No DOM, identity, input or exception text enters the diagnostic.
            attributes: dict[str, str | int] = {
                "browser_read_outcome": "verified" if verified else read_result,
                "browser_completion_scope": "adapter_execution",
                "browser_timing_clock": "monotonic_relative_ms",
                "browser_stage_parent": "worker_adapter_execution",
                "browser_stage_offsets_origin": "adapter_execution",
                "read_stage_offsets_origin": "read_bridge_executor",
            }
            if type(model_calls) is int and 0 <= model_calls <= 10000:
                attributes["read_model_calls"] = model_calls
            if type(refresh_calls) is int and 0 <= refresh_calls <= 10000:
                attributes["worker_refresh_calls"] = refresh_calls
            if type(acquisition_ms) is int and 0 <= acquisition_ms <= 300000:
                attributes["read_acquisition_ms"] = acquisition_ms
            if stage_diagnostic is not None:
                for stage in get_args(ReadAwaitStage):
                    name = "read_total_" + stage + "_ms"
                    value = stage_diagnostic.get(name)
                    if type(value) is int and 0 <= value <= 300000:
                        attributes[name] = value
                    for suffix in ("start_ms", "end_ms", "parent", "result"):
                        name = "read_" + stage + "_" + suffix
                        value = stage_diagnostic.get(name)
                        if ((type(value) is int and 0 <= value <= 300000)
                                or (suffix == "parent" and value == "read_bridge_executor")
                                or (suffix == "result" and value in {
                                    "ok", "failed", "cancelled", "stopped",
                                })):
                            attributes[name] = value
            if bridge_diagnostic is not None:
                attributes.update(bridge_diagnostic)
            if web is not None and session_ref is not None:
                attributes.update(web._execution_diagnostic(session_ref))
            if http_diagnostic is not None:
                status = http_diagnostic.get("http_status")
                if status == "unknown" or (type(status) is int and 100 <= status <= 599):
                    attributes["decision_http_status"] = status
                headers = http_diagnostic.get("http_headers_received")
                if type(headers) is bool:
                    attributes["decision_http_headers_received"] = headers
                category = http_diagnostic.get("http_exception")
                if type(category) is str and category in {
                    "none",
                    "timeout",
                    "transport",
                    "cancelled",
                }:
                    attributes["decision_http_exception"] = category
                kind = http_diagnostic.get("http_exception_kind")
                if type(kind) is str and kind in {
                    "connect_timeout", "read_timeout", "write_timeout", "pool_timeout", "timeout",
                    "connect_error", "read_error", "write_error", "close_error", "proxy_error",
                    "local_protocol_error", "remote_protocol_error", "unsupported_protocol",
                    "decoding_error", "http_error",
                }:
                    attributes["decision_http_exception_kind"] = kind
                phase = http_diagnostic.get("http_timeout_phase")
                if type(phase) is str and phase in {
                    "connect", "read", "write", "pool", "unknown", "none",
                }:
                    attributes["decision_http_timeout_phase"] = phase
            if diagnostic is not None:
                stage, elapsed_ms, code = diagnostic
                attributes.update({
                    "browser_read_stage": stage,
                    "stage_elapsed_ms": elapsed_ms,
                    "browser_failure_code": code,
                })
                if stage in {"target_observation", "target_candidates"} and (
                    web is not None and session_ref is not None
                ):
                    attributes.update(web._observation_diagnostic(session_ref))
            if decision is not None or decision_diagnostic is not None:
                # Project only exact existing Literal values, including when a
                # nonselection has no BrowserFailure/stage timing to record.
                reason_values = tuple(
                    value
                    for branch in get_args(DecisionResult.model_fields["reason"].annotation)
                    for value in get_args(branch)
                    if type(value) is str
                )
                allowed = {
                    "status": get_args(DecisionStatus),
                    "error": get_args(DecisionError),
                    "reason": reason_values,
                }
                for name, values in allowed.items():
                    value = (
                        decision_diagnostic.get(name) if decision_diagnostic is not None
                        else getattr(decision, name)
                    )
                    if type(value) is str and value in values:
                        attributes["decision_" + name] = value
            if attributes:
                await asyncio.wait_for(self._record_diagnostic(run, attributes), timeout=1.0)
        except Exception:
            # Trace failure cannot overwrite the original execution failure.
            logging.getLogger(__name__).warning("browser_read_diagnostic_trace_unavailable")

    async def execute(
        self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint,
    ) -> BrowserReadOutcome:
        if (
            run.verification == "verified" or run.phase != "acquiring"
            or run.effect != "not_sent"
        ):
            raise BrowserReadExecutionError("denied")
        web: PlaywrightWebAdapter | None = None
        execution: RegisteredReadExecution | None = None
        attempted = False
        executor: BrowserExecutor | None = None
        outcome = None
        acquisition_ms: int | None = None
        read_result = "failed"
        origin = phase_started = time.monotonic()
        phase: str | None = "bootstrap"
        bridge_diagnostic: dict[str, str | int] = {}

        def mark(next_phase: str | None, result: str = "ok") -> None:
            nonlocal phase, phase_started
            now = time.monotonic()
            if phase is not None:
                prefix = "read_bridge_" + phase
                bridge_diagnostic.update({
                    prefix + "_start_ms": max(0, min(300000, int((phase_started - origin) * 1000))),
                    prefix + "_end_ms": max(0, min(300000, int((now - origin) * 1000))),
                    prefix + "_duration_ms": max(0, min(300000, int((now - phase_started) * 1000))),
                    prefix + "_parent": "adapter_execution", prefix + "_result": result,
                })
            phase, phase_started = next_phase, now

        try:
            run = await checkpoint.refresh()
            if (
                run.phase != "acquiring" or run.effect != "not_sent"
                or run.verification == "verified"
            ):
                raise BrowserReadExecutionError("denied")
            publication = await self._publications.get_frozen(
                run.owner, run.admission.publication_digest.hex(),
            )
            run = await checkpoint.refresh()
            if (
                run.phase != "acquiring" or run.effect != "not_sent"
                or run.verification == "verified"
            ):
                raise BrowserReadExecutionError("denied")
            if publication is None:
                raise BrowserReadExecutionError("denied")
            manifest = publication.manifest
            observe_only = self._observe_only_manifest is not None and (
                manifest == self._observe_only_manifest
            )
            mark("input")
            private_input = self._cipher.decrypt_input(run.admission)
            query_business_key: str | None = None
            if isinstance(manifest.site.read_rule, RegisteredQueryReadRule):
                arguments = private_input.get("arguments")
                schema = manifest.capability.input_schema
                if (
                    private_input.get("schema_version") != "browser.request.input.v2"
                    or private_input.get("capability_id") != manifest.capability.capability_id
                    or not isinstance(arguments, dict)
                    or type(arguments.get("business_key")) is not str
                    or not arguments["business_key"].strip()
                    or len(arguments["business_key"].encode("utf-8")) > 512
                    or manifest.site.read_rule.key_ref.name != "business_key"
                    or schema.get("type") != "object"
                    or schema.get("additionalProperties") is not False
                    or "business_key" not in schema.get("required", [])
                    or "business_key" not in schema.get("properties", {})
                ):
                    raise BrowserReadExecutionError("denied")
                self._check_local_schema(schema)
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema, registry=Registry()).validate(arguments)
                query_business_key = arguments["business_key"]
            mark("acquisition")
            execution = await self._factory.open(
                run, manifest, private_input, checkpoint,
            )
            acquisition_ms = min(300000, max(0, int(
                (time.monotonic() - phase_started) * 1000
            )))
            mark("context_setup")
            run = await checkpoint.refresh()
            binding = execution.context.expected_binding
            if (
                binding.owner != run.owner or binding.binding_id != run.admission.binding_id
                or binding.binding_revision != run.admission.binding_revision
                or binding.lease_epoch != run.lease_epoch
                or binding.authorization_revision is not None
                or binding.authorization_run_id != run.run_id
                or binding.evidence_version != "verified-session-v1"
                or execution.session.binding != binding
                or execution.context.skill != manifest.skill
                or execution.context.source != manifest.site.source
                or execution.context.navigation_origins != manifest.site.navigation_origins
                or execution.site.bootstrap(manifest.skill) != manifest.site_plan()
            ):
                raise BrowserReadExecutionError("denied")
            context = self._fenced_context(
                execution.context, manifest, checkpoint, business_key=query_business_key,
                bind_publication_guard=execution.bind_publication_guard,
            )
            operation_authority = None
            if execution.bind_operation_authority is not None:
                if execution.bind_publication_guard is None:
                    raise failure("denied")
                operation_authority = execution.bind_operation_authority(context)
            web = PlaywrightWebAdapter(
                registry=execution.registry, observer=execution.observer, site=execution.site,
                rules=execution.rules,
                executions=(
                    RegisteredExecution(
                        execution.session, context, execution.confirmed_key,
                        project_output=execution.project_output,
                        stop_after_observe=observe_only,
                        operation_authority=operation_authority,
                        authority_diagnostic=execution.authority_diagnostic,
                    ),
                ),
            )
            # Construct the verifier here; the factory cannot substitute an
            # executor completion flag or a model-supplied verification result.
            verifier = IndependentVerifier(web, execution.site, execution.resolver)
            executor = BrowserExecutor(
                web, execution.decision, verifier, execution.site,
                execution.resolver, execution.decision_contexts,
            )
            await checkpoint.start_execution()
            attempted = True
            mark("executor")
            outcome = await executor.run(execution.session, context)
            mark("post_execution", "verified" if outcome.verification is not None
                 and outcome.verification.status == "verified" else "failed")
            await checkpoint.refresh(allow_cancel=True)
            observed_ms = web._observe_only_completed.get(execution.session.session_ref)
            if (observe_only and observed_ms is not None and outcome.verification is None
                    and not outcome.receipts and outcome.failure is not None
                    and outcome.failure.code == "cancelled"
                    and outcome.failure.dispatch_state == "not_sent"
                    and context.cancellation.is_set()):
                self._observe_only_receipt = (run, observed_ms)
            effect: RunEffect = "not_sent"
            if any(r.state == "possibly_sent" for r in outcome.receipts) or (
                outcome.failure is not None and outcome.failure.dispatch_state == "possibly_sent"
            ):
                effect = "unknown"
            elif any(r.state == "acknowledged" for r in outcome.receipts):
                effect = "acknowledged"
            verified = outcome.verification
            if verified is None or verified.status != "verified":
                if outcome.failure is not None and outcome.failure.code == "cancelled":
                    read_result = "cancelled"
                return BrowserReadOutcome(
                    effect, verified.status if verified else None,
                    dispatch_failure_code=outcome.failure.code if outcome.failure else None,
                )
            if verified.evidence_digest is None:
                raise BrowserReadExecutionError("invalid_response")
            mark("publication")
            await self._publications.assert_current(run.owner, manifest)
            run = await checkpoint.refresh()
            mark("output_emission")
            evidence_digest = bytes.fromhex(verified.evidence_digest)
            projector = execution.project_output

            def protect(fields: dict[str, str]) -> BrowserReadOutcome:
                if tuple(fields) != manifest.output.field_ids:
                    raise BrowserReadExecutionError("invalid_response")
                value = dict(projector(fields))
                schema = json.loads(manifest.output.output_schema_json)
                self._check_local_schema(schema)
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema, registry=Registry()).validate(value)
                encoded = json.dumps(
                    value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
                if len(encoded) > manifest.output.maximum_bytes:
                    raise BrowserReadExecutionError("unsupported")
                digest = hmac.digest(
                    self._result_key,
                    b"browser-read-result-v1\x00" + run.run_id.encode("ascii") + b"\x00" + encoded,
                    hashlib.sha256,
                )
                envelope = self._cipher.encrypt_result(
                    run.admission, value, evidence_digest=evidence_digest, result_digest=digest,
                )
                return BrowserReadOutcome(effect, "verified", envelope, digest, evidence_digest)

            protected = web._consume_verified_result(execution.session, context, verified, protect)
            self._prune_emissions()
            if len(self._emissions) >= 128 and run.run_id not in self._emissions:
                raise BrowserReadExecutionError("unavailable")
            self._emissions[run.run_id] = _VerifiedEmission(run, protected, time.monotonic() + 120)
            read_result = "verified"
            return protected
        except _BrowserReadDeferred:
            if execution is None and not attempted:
                read_result = "deferred"
                raise
            # No deferred retry is permitted after a resource was opened.
            return BrowserReadOutcome("unknown", None, dispatch_failure_code="unavailable")
        except BrowserReadExecutionError as error:
            if error.code == "cancelled":
                read_result = "cancelled"
            if attempted:
                return BrowserReadOutcome("unknown", None, dispatch_failure_code=error.code)
            raise
        except BrowserOperationError as error:
            if error.failure.code == "cancelled":
                read_result = "cancelled"
            return BrowserReadOutcome(
                "unknown" if attempted or error.failure.dispatch_state == "possibly_sent"
                else "not_sent",
                None, dispatch_failure_code=error.failure.code,
            )
        except asyncio.CancelledError:
            read_result = "cancelled"
            raise
        except Exception:
            if attempted:
                return BrowserReadOutcome("unknown", None, dispatch_failure_code="invalid_response")
            raise BrowserReadExecutionError("invalid_response") from None
        finally:
            if web is not None and execution is not None:
                web._discard_private_result(execution.session)
            mark(None, "ok" if read_result == "verified" else read_result)
            adapter_ms = max(0, min(300000, int((time.monotonic() - origin) * 1000)))
            bridge_diagnostic.update({
                "read_adapter_start_ms": 0,
                "read_adapter_end_ms": adapter_ms,
                "read_adapter_duration_ms": adapter_ms,
                "read_adapter_result": read_result,
            })
            # One end-of-attempt summary, after every adapter success condition.
            # Durable verification/finalization remains owned by worker/store.
            await self._record_failure_diagnostic(
                run,
                executor._last_failure_diagnostic if executor is not None else None,
                web=web,
                session_ref=execution.session.session_ref if execution is not None else None,
                decision=outcome.decisions[-1]
                if outcome is not None and outcome.decisions
                else None,
                decision_diagnostic=executor._last_decision_diagnostic
                if executor is not None
                else None,
                http_diagnostic=(
                    execution.decision_http_diagnostic()
                    if execution is not None and execution.decision_http_diagnostic is not None
                    else None
                ),
                stage_diagnostic=executor._last_stage_diagnostic if executor is not None else None,
                bridge_diagnostic=bridge_diagnostic,
                verified=read_result == "verified",
                refresh_calls=getattr(checkpoint, "refresh_calls", None),
                acquisition_ms=acquisition_ms,
                read_result=read_result,
                model_calls=outcome.model_calls if outcome is not None else None,
            )

    def _prune_emissions(self) -> None:
        now = time.monotonic()
        for run_id in tuple(self._emissions):
            if self._emissions[run_id].deadline <= now:
                del self._emissions[run_id]

    def _check_emission(self, candidate: RunSnapshot) -> None:
        self._prune_emissions()
        emission = self._emissions.get(candidate.run_id)
        if emission is None:
            raise BrowserReadExecutionError("denied")
        run, outcome = emission.run, emission.outcome
        if (
            candidate.admission != run.admission
            or candidate.worker_id != run.worker_id or candidate.worker_epoch != run.worker_epoch
            or candidate.provider_key != run.provider_key
            or candidate.provider_manifest_digest != run.provider_manifest_digest
            or candidate.lease_epoch != run.lease_epoch
            or candidate.verification != "verified"
            or candidate.protected_result != outcome.result
            or candidate.result_digest != outcome.result_digest
            or candidate.verification_evidence_digest != outcome.evidence_digest
        ):
            raise BrowserReadExecutionError("denied")

    async def check_verified_candidate(self, candidate: RunSnapshot) -> None:
        """Bounded local authority callback inside the store transaction.

        Matching ciphertext alone never creates this evidence. Only the actual
        independent read + fresh authority + frozen projection path emits it.
        Do not consume during a transaction that could still roll back.
        """
        self._check_emission(candidate)

    def verified_persisted(self, run: RunSnapshot) -> None:
        self._check_emission(run)
        del self._emissions[run.run_id]

    @staticmethod
    def _check_local_schema(value: object) -> None:
        # Canonical Capability schemas use local $defs. Never permit external
        # references; validation also uses an empty registry without retrieval.
        if isinstance(value, dict):
            for key in ("$ref", "$dynamicRef"):
                if key in value and (
                    not isinstance(value[key], str) or not value[key].startswith("#/")
                ):
                    raise BrowserReadExecutionError("unsupported")
            for child in value.values():
                VerifiedBrowserReadExecution._check_local_schema(child)
        elif isinstance(value, list):
            for child in value:
                VerifiedBrowserReadExecution._check_local_schema(child)

    def _fenced_context(
        self, original: ExecutionContext, manifest: BrowserPublicationManifest,
        checkpoint: BrowserWorkerCheckpoint, *, business_key: str | None = None,
        bind_publication_guard: Callable[
            [Callable[[RunSnapshot], Awaitable[None]]], ExecutionContext
        ] | None = None,
    ) -> ExecutionContext:
        async def publication_guard(run: RunSnapshot) -> None:
            if (run.admission.publication_digest.hex() != manifest.digest
                    or run.owner != original.expected_binding.owner):
                raise failure("denied")
            await self._publications.assert_current(run.owner, manifest)

        # A registered concrete context can compose publication checks into its
        # DB-only fences. Generic factories retain the full bridge wrappers.
        composed = bind_publication_guard is not None
        if bind_publication_guard is not None:
            bound = bind_publication_guard(publication_guard)
            if bound is not original:
                raise failure("denied")

        async def fence() -> None:
            try:
                run = await checkpoint.refresh()
                await self._publications.assert_current(run.owner, manifest)
                await checkpoint.refresh()
            except Exception:
                original.cancellation.set()
                raise failure("denied") from None

        async def current_binding(session: BrowserSessionRef) -> ScopeBinding:
            if not composed:
                await fence()
            binding = await original.current_binding(session)
            if not composed:
                await fence()
            return binding

        async def authorize(
            session: BrowserSessionRef, skill: BrowserSkill,
            subject: ActionCommand | ReadSpec, binding: ScopeBinding,
        ) -> None:
            if not composed:
                await fence()
            await original.authorize(session, skill, subject, binding)
            if not composed:
                await fence()

        async def resolve_parameter(
            ref: ParameterRef, purpose: ParameterPurpose, binding: ScopeBinding,
            skill_digest: str, step_id: str,
        ) -> SealedParameter:
            if not composed:
                await fence()
            if purpose == "expected_field":
                raise failure("denied")
            sealed = await original.resolve_parameter(ref, purpose, binding, skill_digest, step_id)
            if not composed:
                await fence()
            if purpose == "business_key" and (
                business_key is None or ref != manifest.site.read_rule.key_ref
                or not sealed.consume(
                    lambda value: type(value) is str and value == business_key,
                    ref=ref, purpose=purpose, binding=binding,
                    skill_digest=skill_digest, step_id=step_id,
                )
            ):
                raise failure("denied")
            return sealed

        @asynccontextmanager
        async def barrier(
            session: BrowserSessionRef, command: ActionCommand, binding: ScopeBinding,
        ) -> AsyncIterator[DispatchPermit]:
            if not composed:
                await fence()
            async with original.dispatch_barrier(session, command, binding) as permit:
                if not composed:
                    await fence()
                yield permit
            if not composed:
                await fence()

        return replace(
            original, current_binding=current_binding,
            authorize=authorize, dispatch_barrier=barrier,
            resolve_parameter=(resolve_parameter
                               if isinstance(manifest.site.read_rule, RegisteredQueryReadRule)
                               else original.resolve_parameter),
        )

    async def lookup_capture(
        self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint,
    ) -> BrowserCaptureResolution:
        if run.capture_operation_id is None or run.verification != "verified":
            raise BrowserReadExecutionError("denied")
        await checkpoint.refresh()
        result = await self._lifecycle.lookup_capture(run, checkpoint)
        await checkpoint.refresh()
        return result

    async def send_capture(
        self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint,
    ) -> BrowserCaptureResolution:
        if run.capture_status != "sent" or run.capture_operation_id is None:
            raise BrowserReadExecutionError("denied")
        await checkpoint.refresh()
        result = await self._lifecycle.send_capture(run, checkpoint)
        await checkpoint.refresh()
        return result

    async def stop(self, run: RunSnapshot, checkpoint: BrowserWorkerCheckpoint) -> None:
        if not run.cancel_requested:
            raise BrowserReadExecutionError("denied")
        await checkpoint.refresh(allow_cancel=True)
        await self._lifecycle.stop(run, checkpoint)
        await checkpoint.refresh(allow_cancel=True)

    async def cleanup(self, run: RunSnapshot) -> RunCleanup:
        async with asyncio.timeout(15):
            return await self._lifecycle.cleanup(run)
