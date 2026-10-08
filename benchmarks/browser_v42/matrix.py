"""Four explicit browser/decision arms with frozen-fixture comparison."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, cast

from benchmarks.browser_v42.models import CaseResult, SuiteSpec, _hex, _keys, _safe_id
from benchmarks.browser_v42.report import Gate, SuiteReport, summarize_results
from benchmarks.browser_v42.runner import (
    BrowserCall,
    DecisionCall,
    OracleCall,
    RegistryTransportCollector,
    run_suite,
)

ARM_KEYS = frozenset(
    {
        ("cloud", "jev"),
        ("cloud", "local"),
        ("enterprise", "jev"),
        ("enterprise", "local"),
    }
)


@dataclass(frozen=True)
class Budgets:
    max_calls: int
    max_tokens: int
    timeout_ms: int

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> Budgets:
        _keys(raw, {"max_calls", "max_tokens", "timeout_ms"})
        values = []
        for key in ("max_calls", "max_tokens", "timeout_ms"):
            value = raw[key]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError("all budget fields must be positive integers")
            values.append(value)
        return cls(*values)


@dataclass(frozen=True)
class Transport:
    browser: str
    decision: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> Transport:
        _keys(raw, {"browser", "decision"})
        return cls(
            _safe_id(raw["browser"], "browser transport"),
            _safe_id(raw["decision"], "decision transport"),
        )


@dataclass(frozen=True)
class ArmSpec:
    browser_backend: Literal["cloud", "enterprise"]
    decision_backend: Literal["jev", "local"]
    fixture_seed: str
    fixture_digest: str
    checkpoint: str | None
    serving_version: str | None
    dtype: str | None
    budgets: Budgets | None
    transport: Transport | None

    @property
    def key(self) -> tuple[str, str]:
        return self.browser_backend, self.decision_backend

    @property
    def configured(self) -> bool:
        return all(
            (
                self.checkpoint,
                self.serving_version,
                self.dtype,
                self.budgets,
                self.transport,
            )
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> ArmSpec:
        _keys(
            raw,
            {
                "browser_backend",
                "decision_backend",
                "fixture_seed",
                "fixture_digest",
                "checkpoint",
                "serving_version",
                "dtype",
                "budgets",
                "transport",
            },
        )
        browser = raw["browser_backend"]
        decision = raw["decision_backend"]
        if (browser, decision) not in ARM_KEYS:
            raise ValueError("invalid matrix arm")
        browser = cast(Literal["cloud", "enterprise"], browser)
        decision = cast(Literal["jev", "local"], decision)
        seed = _safe_id(raw["fixture_seed"], "fixture_seed")
        digest = _hex(raw["fixture_digest"], "fixture_digest")
        fields = [
            raw[name]
            for name in (
                "checkpoint",
                "serving_version",
                "dtype",
                "budgets",
                "transport",
            )
        ]
        if any(value is None for value in fields):
            if not all(value is None for value in fields):
                raise ValueError("partial arm configuration is forbidden")
            return cls(browser, decision, seed, digest, None, None, None, None, None)
        checkpoint = _safe_id(raw["checkpoint"], "fixed checkpoint")
        serving = _safe_id(raw["serving_version"], "serving_version")
        dtype = _safe_id(raw["dtype"], "dtype")
        budgets_raw = raw["budgets"]
        transport_raw = raw["transport"]
        if not isinstance(budgets_raw, dict) or not isinstance(transport_raw, dict):
            raise ValueError("budgets and transport must be objects")
        budgets = Budgets.from_mapping(budgets_raw)
        transport = Transport.from_mapping(transport_raw)
        return cls(browser, decision, seed, digest, checkpoint, serving, dtype, budgets, transport)


@dataclass(frozen=True)
class MatrixSpec:
    fixture_seed: str
    fixture_digest: str
    arms: tuple[ArmSpec, ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object], suite: SuiteSpec) -> MatrixSpec:
        _keys(raw, {"fixture_seed", "fixture_digest", "arms"})
        arms_raw = raw["arms"]
        if not isinstance(arms_raw, list) or not all(isinstance(arm, dict) for arm in arms_raw):
            raise ValueError("arms must be objects")
        spec = cls(
            _safe_id(raw["fixture_seed"], "fixture_seed"),
            _hex(raw["fixture_digest"], "fixture_digest"),
            tuple(ArmSpec.from_mapping(arm) for arm in arms_raw),
        )
        validate_matrix_inputs(spec, suite)
        return spec


@dataclass(frozen=True)
class ArmReport:
    arm: ArmSpec
    results: tuple[CaseResult, ...]
    report: SuiteReport


@dataclass(frozen=True)
class MatrixReport:
    arms: dict[tuple[str, str], ArmReport]
    gate: Gate
    wbs_acceptance: Literal["NOT_EVALUATED"] = "NOT_EVALUATED"


def validate_matrix_inputs(spec: MatrixSpec, suite: SuiteSpec) -> None:
    if spec.fixture_seed != suite.fixture_seed or spec.fixture_digest != suite.fixture_digest:
        raise ValueError("matrix and suite fixture identity differ")
    if len(spec.arms) != 4 or {arm.key for arm in spec.arms} != ARM_KEYS:
        raise ValueError("matrix requires exactly four distinct arms")
    if any(
        arm.fixture_seed != suite.fixture_seed or arm.fixture_digest != suite.fixture_digest
        for arm in spec.arms
    ):
        raise ValueError("every arm must use the same frozen fixture")


def evaluate_arm(
    arm: ArmSpec,
    suite: SuiteSpec,
    *,
    browser: BrowserCall | None,
    decision: DecisionCall | None,
    oracle: OracleCall | None,
    collector: RegistryTransportCollector | None = None,
) -> ArmReport:
    if arm.fixture_seed != suite.fixture_seed or arm.fixture_digest != suite.fixture_digest:
        raise ValueError("arm fixture identity differs from suite")
    if arm.key not in ARM_KEYS:
        raise ValueError("invalid arm")
    if not arm.configured or collector is None or not _collector_matches_arm(collector, arm):
        results: tuple[CaseResult, ...] = ()
    else:
        results = run_suite(
            suite,
            browser=browser,
            decision=decision,
            oracle=oracle,
            collector=collector,
            required_backend=arm.browser_backend,
            required_checkpoint=arm.checkpoint,
        )
    return ArmReport(arm, results, summarize_results(results, suite))


def _collector_matches_arm(collector: RegistryTransportCollector, arm: ArmSpec) -> bool:
    budgets = arm.budgets
    transport = arm.transport
    if budgets is None or transport is None:
        return False
    browser_ok = any(
        source.backend == arm.browser_backend
        and source.fixture_digest == arm.fixture_digest
        and source.transport == transport.browser
        for source in collector.browser_registrations.values()
    )
    decision_ok = any(
        source.kind == "real_model"
        and source.checkpoint == arm.checkpoint
        and source.serving_version == arm.serving_version
        and source.dtype == arm.dtype
        and source.transport == transport.decision
        and source.max_calls == budgets.max_calls
        and source.max_tokens == budgets.max_tokens
        and source.timeout_ms == budgets.timeout_ms
        for source in collector.decision_registrations.values()
    )
    return browser_ok and decision_ok


def compare_arms(reports: tuple[ArmReport, ...], suite: SuiteSpec) -> MatrixReport:
    if len(reports) != 4 or {report.arm.key for report in reports} != ARM_KEYS:
        raise ValueError("comparison requires four distinct arms")
    candidate_by_case: dict[str, str] = {}
    seen_invocations: set[tuple[str, str]] = set()
    for arm_report in reports:
        if (
            arm_report.arm.fixture_seed != suite.fixture_seed
            or arm_report.arm.fixture_digest != suite.fixture_digest
        ):
            raise ValueError("arm fixture identity mismatch")
        if arm_report.report.fixture_digest != suite.fixture_digest:
            raise ValueError("arm report fixture digest mismatch")
        checked = summarize_results(arm_report.results, suite)
        if checked != arm_report.report:
            raise ValueError("arm report does not match its raw accounting records")
        for result in arm_report.results:
            if result.browser is None:
                continue
            if result.browser.backend != arm_report.arm.browser_backend:
                raise ValueError("browser backend differs from matrix arm")
            if result.browser.fixture_digest != suite.fixture_digest:
                raise ValueError("browser fixture digest differs from matrix suite")
            if result.browser_proof is not None and (
                arm_report.arm.transport is None
                or result.browser_proof.transport != arm_report.arm.transport.browser
            ):
                raise ValueError("browser transport differs from matrix arm")
            for proof in result.decision_proofs:
                if proof is None or proof.kind != "real_model":
                    continue
                invocation_key = (proof.source_id, proof.invocation_id)
                if invocation_key in seen_invocations:
                    raise ValueError("verified decision invocation reused across matrix arms")
                seen_invocations.add(invocation_key)
                budgets = arm_report.arm.budgets
                transport = arm_report.arm.transport
                if (
                    budgets is None
                    or transport is None
                    or proof.checkpoint != arm_report.arm.checkpoint
                    or proof.serving_version != arm_report.arm.serving_version
                    or proof.dtype != arm_report.arm.dtype
                    or proof.transport != transport.decision
                    or proof.max_calls != budgets.max_calls
                    or proof.max_tokens != budgets.max_tokens
                    or proof.timeout_ms != budgets.timeout_ms
                ):
                    raise ValueError("verified decision deployment differs from matrix arm")
            digest = result.browser.candidate_digest
            previous = candidate_by_case.setdefault(result.case_id, digest)
            if previous != digest:
                raise ValueError("candidate digest differs across matrix arms")
    if any(report.report.gate == "FAIL" for report in reports):
        gate: Gate = "FAIL"
    elif any(report.report.gate == "WAITING_ENV" for report in reports):
        gate = "WAITING_ENV"
    else:
        gate = "PASS"
    return MatrixReport({report.arm.key: report for report in reports}, gate)
