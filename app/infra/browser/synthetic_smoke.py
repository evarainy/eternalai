"""Opt-in, single-fixture model smoke; operator evidence is injected, never inferred.

This is not browser E2E or a public source registration service. The executable
has no approved registry or tokenizer evidence yet and therefore fails closed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx2
from pydantic import SecretStr

from app.browser_skill.models import (
    DecisionCallContext,
    DecisionCandidate,
    DecisionRequest,
    DecisionSource,
    FrameHop,
    ModelManifest,
    ScopeStamp,
    TargetRef,
)
from app.infra.browser.decision_adapters import TypeSafeCodec
from app.infra.browser.systemone_http import DecisionDeployment, DecisionHTTPProvider

ORIGIN = "https://api.typesafe.ai"
PROTOCOL = "typesafe.systemone.v1"
CASE_ID = "neutral_button_choice_v1"
TIMEOUT_SECONDS = 20.0
MAX_CALLS = 9
MAX_INPUT_TOKENS = 1024
MAX_TOTAL_INPUT_TOKENS = MAX_CALLS * MAX_INPUT_TOKENS


def known_request() -> DecisionRequest:
    """One immutable neutral case; accepts no text, URL, file or command input."""
    scope = ScopeStamp(
        page_id="neutral_page",
        page_epoch=1,
        frame_path=(FrameHop(frame_id="main", frame_epoch=1),),
        region_id="buttons",
        region_digest=hashlib.sha256(b"neutral_buttons_v1").hexdigest(),
    )
    return DecisionRequest(
        request_id=CASE_ID,
        scope=scope,
        criteria=("Select the button named Open",),
        candidates=tuple(
            DecisionCandidate(
                ref=TargetRef(target_id=target, candidate_epoch=1, scope=scope),
                role="button",
                name=name,
            )
            for target, name in (("open_button", "Open"), ("close_button", "Close"))
        ),
    )


def fixture_digest() -> str:
    return hashlib.sha256(known_request().model_dump_json().encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class TokenEvidence:
    """Independent frozen count for this exact wire input and pinned evaluation.

    The operator must verify how the count was obtained before pinning its digest.
    Neither response usage nor a local byte/character estimate is such evidence.
    """

    protocol: str
    request_model: str
    deployment_model: str
    manifest_digest: str
    wire_digest: str
    counter_revision: str
    input_tokens: int

    def digest(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class TrustedCounter(Protocol):
    def count(self, body: bytes, manifest: ModelManifest) -> TokenEvidence:
        """Return independently verifiable evidence; never infer from usage."""
        ...


@dataclass(frozen=True, slots=True)
class SmokeRegistration:
    """Trusted operator assembly only; not accepted from CLI, env or request data."""

    deployment: DecisionDeployment
    manifest: ModelManifest
    source: DecisionSource
    protocol: str
    case_id: str
    wire_digest: str
    token_evidence_digest: str


class SmokeQuota:
    """Process-local hard budget; reservations are permanent, including failures."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls = 0
        self._input_tokens = 0

    def reserve(self, input_tokens: int) -> bool:
        if type(input_tokens) is not int or not 0 < input_tokens <= MAX_INPUT_TOKENS:
            return False
        with self._lock:
            if (
                self._calls >= MAX_CALLS
                or self._input_tokens + input_tokens > MAX_TOTAL_INPUT_TOKENS
            ):
                return False
            self._calls += 1
            self._input_tokens += input_tokens
            return True

    def snapshot(self) -> tuple[int, int]:
        with self._lock:
            return self._calls, self._input_tokens


_PROCESS_QUOTA = SmokeQuota()


@dataclass(frozen=True, slots=True)
class SmokeOutcome:
    status: Literal["DISABLED", "WAITING_ENV", "REJECTED", "PASS", "FAIL"]
    code: str


def protected_client(key: SecretStr) -> httpx2.AsyncClient:
    """Only fixed-origin, bounded, no-redirect/no-proxy, zero-retry HTTP assembly."""
    return httpx2.AsyncClient(
        base_url=ORIGIN,
        trust_env=False,
        follow_redirects=False,
        timeout=httpx2.Timeout(TIMEOUT_SECONDS),
        transport=httpx2.AsyncHTTPTransport(retries=0, trust_env=False),
        headers={"Authorization": "Bearer " + key.get_secret_value()},
    )


@dataclass(slots=True)
class SyntheticSmoke:
    """Narrow internal DI seam; the caller supplies trusted frozen operator objects."""

    registration: SmokeRegistration | None = None
    counter: TrustedCounter | None = field(default=None, repr=False)
    quota: SmokeQuota = field(default_factory=lambda: _PROCESS_QUOTA, repr=False)
    client_factory: Callable[[SecretStr], httpx2.AsyncClient] = field(
        default=protected_client,
        repr=False,
    )

    def _prepare(self) -> tuple[DecisionRequest, TokenEvidence] | SmokeOutcome:
        registration = self.registration
        if registration is None or self.counter is None:
            return SmokeOutcome("WAITING_ENV", "operator_evidence_required")
        request = known_request()
        deployment, manifest, source = (
            registration.deployment,
            registration.manifest,
            registration.source,
        )
        try:
            source_origin = urlsplit(source.origin)
        except ValueError:
            return SmokeOutcome("REJECTED", "registration_mismatch")
        if (
            deployment.endpoint_origin != ORIGIN
            or deployment.disposition != "cloud_synthetic"
            or deployment.manifest_digest != manifest.manifest_digest
            or registration.protocol != PROTOCOL
            or registration.case_id != CASE_ID
            or source.fixture_digest != fixture_digest()
            or source.source_id != CASE_ID
            or source_origin.scheme != "https"
            or not source_origin.hostname
            or source_origin.username is not None
            or source_origin.password is not None
            or bool(source_origin.path or source_origin.query or source_origin.fragment)
            or source.origin != f"https://{source_origin.netloc}"
            or deployment.registered_sources != (source,)
        ):
            return SmokeOutcome("REJECTED", "registration_mismatch")
        context = self._context(request)
        body = TypeSafeCodec().encode(request, context)
        wire_digest = hashlib.sha256(body).hexdigest()
        if wire_digest != registration.wire_digest:
            return SmokeOutcome("REJECTED", "wire_mismatch")
        try:
            evidence = self.counter.count(body, manifest)
            valid = (
                isinstance(evidence, TokenEvidence)
                and evidence.protocol == PROTOCOL
                and evidence.request_model == manifest.request_model
                and evidence.deployment_model == manifest.deployment_model
                and evidence.manifest_digest == manifest.manifest_digest
                and evidence.wire_digest == wire_digest
                and bool(evidence.counter_revision)
                and type(evidence.input_tokens) is int
                and evidence.input_tokens > 0
                and evidence.digest() == registration.token_evidence_digest
            )
        except Exception:
            return SmokeOutcome("WAITING_ENV", "counter_evidence_unavailable")
        if not valid:
            return SmokeOutcome("REJECTED", "counter_evidence_mismatch")
        return request, evidence

    def _context(self, request: DecisionRequest) -> DecisionCallContext:
        assert self.registration is not None
        return DecisionCallContext(
            deadline_monotonic=time.monotonic() + TIMEOUT_SECONDS,
            manifest=self.registration.manifest,
            source=self.registration.source,
            current_targets=lambda: tuple(candidate.ref for candidate in request.candidates),
        )

    async def run(
        self,
        *,
        enabled: bool = False,
        secret_input: Callable[[], SecretStr],
    ) -> SmokeOutcome:
        if enabled is not True:
            return SmokeOutcome("DISABLED", "explicit_enable_required")
        prepared = self._prepare()
        if isinstance(prepared, SmokeOutcome):
            return prepared
        request, evidence = prepared
        if not self.quota.reserve(evidence.input_tokens):
            return SmokeOutcome("REJECTED", "hard_budget")
        # No refund: even local cancellation or a failed send consumes its reservation.
        try:
            key = secret_input()
            if not isinstance(key, SecretStr) or not key.get_secret_value():
                return SmokeOutcome("REJECTED", "secret_input_invalid")
            assert self.registration is not None
            async with self.client_factory(key) as client:
                provider = DecisionHTTPProvider(
                    client, TypeSafeCodec(), self.registration.deployment
                )
                result = await provider.decide(request, self._context(request))
            if result.error is not None:
                return SmokeOutcome("FAIL", result.error)
            if result.status != "selected" or result.selected != request.candidates[0].ref:
                return SmokeOutcome("FAIL", "unexpected_selection")
            return SmokeOutcome("PASS", "neutral_choice_verified")
        except asyncio.CancelledError:
            raise
        except Exception:
            # Deliberately expose no exception message, URL, header or response body.
            return SmokeOutcome("FAIL", "local_or_transport_failure")
