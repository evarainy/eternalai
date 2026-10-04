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
from typing import Protocol

from jsonschema import Draft202012Validator
from referencing import Registry

from app.browser_skill.executor import BrowserExecutor, DecisionContextFactory
from app.browser_skill.models import (
    ActionCommand,
    BrowserOperationError,
    BrowserSessionRef,
    BrowserSkill,
    ConfirmedBusinessKey,
    DispatchPermit,
    ExecutionContext,
    ParameterPurpose,
    ParameterRef,
    ReadSpec,
    ScopeBinding,
    SealedParameter,
)
from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.browser_skill.site_rules import RegisteredQueryReadRule
from app.browser_skill.verifier import IndependentVerifier, ReadSpecResolver, failure
from app.infra.browser.playwright_dom_rules import RegisteredDOMRules
from app.infra.browser.playwright_observer import PlaywrightObserver
from app.infra.browser.playwright_web_adapter import (
    BusinessRegistry,
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

    async def _record_failure_diagnostic(
        self, run: RunSnapshot, diagnostic: tuple[str, int, str] | None,
    ) -> None:
        if self._record_diagnostic is None or diagnostic is None:
            return
        stage, elapsed_ms, code = diagnostic
        try:
            # Existing Trace routing metadata is separate from these attributes.
            # No DOM, identity, input or exception text enters the diagnostic.
            await asyncio.wait_for(self._record_diagnostic(run, {
                "browser_read_stage": stage,
                "stage_elapsed_ms": elapsed_ms,
                "browser_failure_code": code,
            }), timeout=1.0)
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
            execution = await self._factory.open(
                run, manifest, private_input, checkpoint,
            )
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
            )
            web = PlaywrightWebAdapter(
                registry=execution.registry, observer=execution.observer, site=execution.site,
                rules=execution.rules,
                executions=(
                    RegisteredExecution(
                        execution.session, context, execution.confirmed_key,
                        project_output=execution.project_output,
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
            outcome = await executor.run(execution.session, context)
            await self._record_failure_diagnostic(run, executor._last_failure_diagnostic)
            await checkpoint.refresh(allow_cancel=True)
            effect: RunEffect = "not_sent"
            if any(r.state == "possibly_sent" for r in outcome.receipts) or (
                outcome.failure is not None and outcome.failure.dispatch_state == "possibly_sent"
            ):
                effect = "unknown"
            elif any(r.state == "acknowledged" for r in outcome.receipts):
                effect = "acknowledged"
            verified = outcome.verification
            if verified is None or verified.status != "verified":
                return BrowserReadOutcome(
                    effect, verified.status if verified else None,
                    dispatch_failure_code=outcome.failure.code if outcome.failure else None,
                )
            if verified.evidence_digest is None:
                raise BrowserReadExecutionError("invalid_response")
            await self._publications.assert_current(run.owner, manifest)
            run = await checkpoint.refresh()
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
            return protected
        except BrowserReadExecutionError as error:
            if attempted:
                return BrowserReadOutcome("unknown", None, dispatch_failure_code=error.code)
            raise
        except BrowserOperationError as error:
            return BrowserReadOutcome(
                "unknown" if attempted or error.failure.dispatch_state == "possibly_sent"
                else "not_sent",
                None, dispatch_failure_code=error.failure.code,
            )
        except Exception:
            if attempted:
                return BrowserReadOutcome("unknown", None, dispatch_failure_code="invalid_response")
            raise BrowserReadExecutionError("invalid_response") from None
        finally:
            if web is not None and execution is not None:
                web._discard_private_result(execution.session)

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
    ) -> ExecutionContext:
        async def fence() -> None:
            try:
                run = await checkpoint.refresh()
                await self._publications.assert_current(run.owner, manifest)
                await checkpoint.refresh()
            except Exception:
                original.cancellation.set()
                raise failure("denied") from None

        async def current_binding(session: BrowserSessionRef) -> ScopeBinding:
            await fence()
            binding = await original.current_binding(session)
            await fence()
            return binding

        async def authorize(
            session: BrowserSessionRef, skill: BrowserSkill,
            subject: ActionCommand | ReadSpec, binding: ScopeBinding,
        ) -> None:
            await fence()
            await original.authorize(session, skill, subject, binding)
            await fence()

        async def resolve_parameter(
            ref: ParameterRef, purpose: ParameterPurpose, binding: ScopeBinding,
            skill_digest: str, step_id: str,
        ) -> SealedParameter:
            await fence()
            if purpose == "expected_field":
                raise failure("denied")
            sealed = await original.resolve_parameter(ref, purpose, binding, skill_digest, step_id)
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
            await fence()
            async with original.dispatch_barrier(session, command, binding) as permit:
                await fence()
                yield permit
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
