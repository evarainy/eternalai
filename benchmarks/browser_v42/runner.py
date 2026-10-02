"""Injected, source-accounted case execution without service construction."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from benchmarks.browser_v42.models import (
    AttemptEvidence,
    BrowserEvidence,
    BrowserProof,
    BrowserReceipt,
    CaseResult,
    CaseSpec,
    DecisionInput,
    DecisionProof,
    DecisionReceipt,
    OracleEvidence,
    RegisteredBrowser,
    RegisteredDecision,
    SuiteSpec,
    TrajectoryEvent,
    _digest,
    _hex,
)

BrowserCall = Callable[[CaseSpec], BrowserEvidence]
DecisionCall = Callable[[DecisionInput, int], AttemptEvidence]
OracleCall = Callable[[CaseSpec, BrowserEvidence, tuple[AttemptEvidence, ...]], OracleEvidence]
BrowserReceiptReader = Callable[[str], BrowserReceipt | None]
DecisionReceiptReader = Callable[[str], DecisionReceipt | None]


class SourceCollector(Protocol):
    """Trusted composition entry; readers must come from registry/transport.

    A model response, test callback, or its claimed source label cannot supply
    these independent receipts. Without this collector, results are source
    checkpoints only and never enter real browser/model denominators.
    """

    def verify_browser(
        self,
        case: CaseSpec,
        suite: SuiteSpec,
        evidence: BrowserEvidence,
    ) -> BrowserProof | None: ...

    def verify_decision(
        self,
        case: CaseSpec,
        request: DecisionInput,
        attempt_number: int,
        evidence: AttemptEvidence,
    ) -> DecisionProof | None: ...


class RegistryTransportCollector:
    """Cross-check frozen registrations against independently collected receipts.

    The integration owner must inject readers backed by the actual registered
    adapters' transport journals. This predicate cannot establish authenticity
    of an arbitrary Python callback supplied in its place.
    """

    def __init__(
        self,
        browser_registrations: Mapping[str, RegisteredBrowser],
        decision_registrations: Mapping[str, RegisteredDecision],
        browser_receipt: BrowserReceiptReader,
        decision_receipt: DecisionReceiptReader,
    ) -> None:
        self.browser_registrations = dict(browser_registrations)
        self.decision_registrations = dict(decision_registrations)
        self.browser_receipt = browser_receipt
        self.decision_receipt = decision_receipt

    def verify_browser(
        self,
        case: CaseSpec,
        suite: SuiteSpec,
        evidence: BrowserEvidence,
    ) -> BrowserProof | None:
        receipt = self.browser_receipt(evidence.source_evidence_digest)
        if receipt is None:
            return None
        registered = self.browser_registrations.get(receipt.source_id)
        if registered is None or evidence.source != "real_browser":
            return None
        if (
            receipt.receipt_digest != evidence.source_evidence_digest
            or registered.source_id != receipt.source_id
            or registered.backend != receipt.backend
            or registered.transport != receipt.transport
            or registered.fixture_digest != suite.fixture_digest
            or receipt.fixture_digest != evidence.fixture_digest
            or receipt.backend != evidence.backend
            or receipt.observation_digest != evidence.observation_digest
            or receipt.candidate_digest != evidence.candidate_digest
        ):
            return None
        return BrowserProof(
            receipt.source_id,
            receipt.receipt_digest,
            receipt.backend,
            receipt.transport,
            receipt.fixture_digest,
            receipt.observation_digest,
            receipt.candidate_digest,
        )

    def verify_decision(
        self,
        case: CaseSpec,
        request: DecisionInput,
        attempt_number: int,
        evidence: AttemptEvidence,
    ) -> DecisionProof | None:
        receipt = self.decision_receipt(evidence.invocation_id)
        if receipt is None:
            return None
        registered = self.decision_registrations.get(receipt.source_id)
        if registered is None or evidence.source != registered.kind:
            return None
        try:
            _hex(receipt.wire_request_digest, "wire request digest")
        except ValueError:
            return None
        if (
            receipt.invocation_id != evidence.invocation_id
            or receipt.receipt_digest != evidence.source_evidence_digest
            or receipt.attempt_number != attempt_number
            or receipt.fixture_digest != request.fixture_digest
            or receipt.request_digest != request.request_digest
            or receipt.request_digest != evidence.request_digest
            or receipt.response_digest != evidence.response_digest
            or receipt.response_deployment != registered.checkpoint
            or receipt.response_deployment != evidence.deployment_pin
            or receipt.manifest_digest != registered.manifest_digest
            or receipt.serving_version != registered.serving_version
            or receipt.dtype != registered.dtype
            or receipt.transport != registered.transport
            or not attempt_number <= receipt.reserved_calls <= registered.max_calls
            or receipt.max_tokens != registered.max_tokens
            or receipt.timeout_ms != registered.timeout_ms
        ):
            return None
        return DecisionProof(
            receipt.source_id,
            registered.kind,
            receipt.receipt_digest,
            receipt.invocation_id,
            receipt.request_digest,
            receipt.wire_request_digest,
            receipt.response_digest,
            registered.checkpoint,
            registered.manifest_digest,
            registered.serving_version,
            registered.dtype,
            registered.transport,
            registered.max_calls,
            registered.max_tokens,
            registered.timeout_ms,
        )


def record_event(
    case_id: str,
    evidence: BrowserEvidence | AttemptEvidence | OracleEvidence,
    *,
    attempt_number: int | None = None,
) -> TrajectoryEvent:
    """Record only a digest of approved typed metadata, never a raw wire value."""
    if isinstance(evidence, BrowserEvidence):
        phase = "browser"
        metadata: object = (
            evidence.source,
            evidence.backend,
            evidence.fixture_digest,
            evidence.observation_digest,
            evidence.candidate_digest,
            evidence.source_evidence_digest,
        )
    elif isinstance(evidence, AttemptEvidence):
        phase = "decision"
        metadata = (
            evidence.source,
            evidence.invocation_id,
            evidence.source_evidence_digest,
            evidence.request_digest,
            evidence.response_digest,
            evidence.outcome,
            evidence.grounded,
            evidence.deployment_pin,
            evidence.duration_ms,
        )
    elif isinstance(evidence, OracleEvidence):
        phase = "oracle"
        metadata = (
            evidence.initial_correct,
            evidence.business_success,
            tuple(sorted(evidence.hazards)),
            evidence.source_evidence_digest,
        )
    else:
        raise TypeError("unsupported trajectory evidence")
    return TrajectoryEvent(case_id, phase, attempt_number, _digest(metadata))  # type: ignore[arg-type]


def run_case(
    case: CaseSpec,
    suite: SuiteSpec,
    *,
    browser: BrowserCall | None,
    decision: DecisionCall | None,
    oracle: OracleCall | None,
    collector: SourceCollector | None = None,
    required_backend: str | None = None,
    required_checkpoint: str | None = None,
    max_attempts: int = 1,
) -> CaseResult:
    """Execute one case, recording only bounded evidence produced by the three calls.

    Missing configuration is WAITING_ENV only before any call. Any exception or
    invalid evidence after dispatch is a real FAIL, without exception text in
    the report. A retry never replaces the first attempt.
    """
    if case not in suite.cases:
        raise ValueError("case not in frozen suite")
    if max_attempts < 1 or max_attempts > 5:
        raise ValueError("max_attempts outside bounded range")
    if browser is None or decision is None or oracle is None:
        return CaseResult(case.case_id, "WAITING_ENV", failure_code="MISSING_INJECTION")

    browser_evidence: BrowserEvidence | None = None
    browser_proof: BrowserProof | None = None
    attempts: list[AttemptEvidence] = []
    decision_proofs: list[DecisionProof | None] = []
    events: list[TrajectoryEvent] = []
    try:
        browser_evidence = browser(case)
        if not isinstance(browser_evidence, BrowserEvidence):
            raise ValueError("invalid browser evidence")
        if browser_evidence.fixture_digest != suite.fixture_digest:
            raise ValueError("fixture digest differs from frozen suite")
        if required_backend is not None and browser_evidence.backend != required_backend:
            raise ValueError("browser backend differs from selected matrix arm")
        browser_proof = (
            collector.verify_browser(case, suite, browser_evidence)
            if collector is not None
            else None
        )
        events.append(record_event(case.case_id, browser_evidence))
        decision_input = DecisionInput(
            observation_digest=browser_evidence.observation_digest,
            candidate_digest=browser_evidence.candidate_digest,
            fixture_digest=suite.fixture_digest,
        )
        for attempt_number in range(1, max_attempts + 1):
            attempt = decision(decision_input, attempt_number)
            if not isinstance(attempt, AttemptEvidence):
                raise ValueError("invalid decision evidence")
            attempts.append(attempt)
            decision_proofs.append(None)
            if attempt.request_digest != decision_input.request_digest:
                raise ValueError("decision evidence request digest mismatch")
            if required_checkpoint is not None and attempt.source == "real_model":
                if attempt.deployment_pin != required_checkpoint:
                    raise ValueError("model checkpoint differs from selected matrix arm")
            if collector is not None:
                decision_proofs[-1] = collector.verify_decision(
                    case,
                    decision_input,
                    attempt_number,
                    attempt,
                )
            events.append(record_event(case.case_id, attempt, attempt_number=attempt_number))
            if not attempt.retry_requested:
                break
        else:
            return CaseResult(
                case.case_id,
                "FAIL",
                browser_evidence,
                tuple(attempts),
                failure_code="RETRY_LIMIT",
                events=tuple(events),
                browser_proof=browser_proof,
                decision_proofs=tuple(decision_proofs),
            )
        oracle_evidence = oracle(case, browser_evidence, tuple(attempts))
        if not isinstance(oracle_evidence, OracleEvidence):
            raise ValueError("invalid oracle evidence")
        events.append(record_event(case.case_id, oracle_evidence))
        if oracle_evidence.business_success and not oracle_evidence.hazards:
            return CaseResult(
                case.case_id,
                "PASS",
                browser_evidence,
                tuple(attempts),
                oracle_evidence,
                events=tuple(events),
                browser_proof=browser_proof,
                decision_proofs=tuple(decision_proofs),
            )
        return CaseResult(
            case.case_id,
            "FAIL",
            browser_evidence,
            tuple(attempts),
            oracle_evidence,
            failure_code="ORACLE_REJECTED",
            events=tuple(events),
            browser_proof=browser_proof,
            decision_proofs=tuple(decision_proofs),
        )
    except Exception:
        # Exception messages can contain DOM or wire values. The stage code is
        # sufficient for accounting; callers retain external diagnostic logs.
        code = (
            "BROWSER_ERROR"
            if browser_evidence is None
            else (
                "DECISION_ERROR"
                if not attempts or attempts[-1].retry_requested
                else "EVIDENCE_OR_ORACLE_ERROR"
            )
        )
        return CaseResult(
            case.case_id,
            "FAIL",
            browser_evidence,
            tuple(attempts),
            failure_code=code,
            events=tuple(events),
            browser_proof=browser_proof,
            decision_proofs=tuple(decision_proofs),
        )


def run_suite(
    suite: SuiteSpec,
    *,
    browser: BrowserCall | None,
    decision: DecisionCall | None,
    oracle: OracleCall | None,
    collector: SourceCollector | None = None,
    required_backend: str | None = None,
    required_checkpoint: str | None = None,
) -> tuple[CaseResult, ...]:
    return tuple(
        run_case(
            case,
            suite,
            browser=browser,
            decision=decision,
            oracle=oracle,
            collector=collector,
            required_backend=required_backend,
            required_checkpoint=required_checkpoint,
        )
        for case in suite.cases
        if case.dataset != "calibration"
    )
