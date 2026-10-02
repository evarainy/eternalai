"""Strict, value-free benchmark specifications and evidence records."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, cast

Dataset = Literal[
    "business", "smoke", "model_positive", "model_negative", "internal_login", "calibration"
]
CaseStatus = Literal["PASS", "FAIL", "WAITING_ENV"]
SourceKind = Literal["real_model", "deterministic", "fake", "mock", "bypassed", "fault_injection"]
BrowserKind = Literal["real_browser", "fake_browser", "mock_browser"]
Hazard = Literal["wrong_object", "leak", "unauthorized", "duplicate_send"]

FLOWS: dict[str, str] = {
    "E01_query_todos_owner_inbox": "query_todos",
    "E02_query_todos_crosspage_key": "query_todos",
    "E03_check_messages_conversation_unread": "check_messages",
    "E04_open_todo_confirmed_key": "open_todo",
    "E05_search_contact_same_name_cross_department": "search_contact",
}
PUBLIC_SKILLS = frozenset(FLOWS.values())
DATASETS = frozenset(
    {
        "business",
        "smoke",
        "model_positive",
        "model_negative",
        "internal_login",
        "calibration",
    }
)
SOURCES = frozenset({"real_model", "deterministic", "fake", "mock", "bypassed", "fault_injection"})
HAZARDS = frozenset({"wrong_object", "leak", "unauthorized", "duplicate_send"})
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}\Z")


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _keys(value: Mapping[str, object], required: set[str]) -> None:
    if set(value) != required:
        raise ValueError(f"schema keys mismatch: {sorted(set(value) ^ required)}")


def _safe_id(value: object, name: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


def _hex(value: object, name: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    dataset: Dataset
    flow: str | None
    skill: str
    locale: Literal["zh-CN"]
    parameter_ref: str
    parameter_digest: str
    expected: Literal["business", "target", "abstain"]
    critical: bool

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> CaseSpec:
        _keys(
            raw,
            {
                "case_id",
                "dataset",
                "flow",
                "skill",
                "locale",
                "parameter_ref",
                "parameter_digest",
                "expected",
                "critical",
            },
        )
        case_id = _safe_id(raw["case_id"], "case_id")
        dataset = raw["dataset"]
        flow = raw["flow"]
        skill = _safe_id(raw["skill"], "skill")
        locale = raw["locale"]
        parameter_ref = _safe_id(raw["parameter_ref"], "parameter_ref")
        parameter_digest = _hex(raw["parameter_digest"], "parameter_digest")
        expected = raw["expected"]
        critical = raw["critical"]
        if (
            not isinstance(dataset, str)
            or dataset not in DATASETS
            or not isinstance(expected, str)
            or expected not in {"business", "target", "abstain"}
        ):
            raise ValueError("invalid dataset or expected kind")
        if type(critical) is not bool:
            raise ValueError("critical must be boolean")
        if locale != "zh-CN":
            raise ValueError("benchmark cases require frozen Chinese locale")
        if dataset == "internal_login":
            if flow is not None or skill != "login_assist" or expected != "business":
                raise ValueError("invalid internal login case")
        elif dataset == "calibration":
            if flow is not None or skill not in PUBLIC_SKILLS:
                raise ValueError("invalid calibration case")
        elif not isinstance(flow, str) or flow not in FLOWS or skill != FLOWS[flow]:
            raise ValueError("flow and skill mismatch")
        if dataset in {"business", "smoke"} and expected != "business":
            raise ValueError("business case must use business expectation")
        if dataset == "model_positive" and expected != "target":
            raise ValueError("positive model case must expect target")
        if dataset == "model_negative" and expected != "abstain":
            raise ValueError("negative model case must expect abstention")
        if dataset != "model_negative" and critical:
            raise ValueError("only model negatives may be critical")
        return cls(
            case_id,
            cast(Dataset, dataset),
            flow,
            skill,
            "zh-CN",
            parameter_ref,
            parameter_digest,
            cast(Literal["business", "target", "abstain"], expected),
            critical,
        )


@dataclass(frozen=True)
class SuiteSpec:
    schema_version: int
    fixture_seed: str
    fixture_digest: str
    cases: tuple[CaseSpec, ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> SuiteSpec:
        _keys(raw, {"schema_version", "fixture_seed", "fixture_digest", "cases"})
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise ValueError("unsupported scenario schema")
        seed = _safe_id(raw["fixture_seed"], "fixture_seed")
        digest = _hex(raw["fixture_digest"], "fixture_digest")
        case_rows = raw["cases"]
        if not isinstance(case_rows, list) or not all(isinstance(c, dict) for c in case_rows):
            raise ValueError("cases must be objects")
        cases = tuple(CaseSpec.from_mapping(c) for c in case_rows)
        if len({c.case_id for c in cases}) != len(cases):
            raise ValueError("duplicate case_id")
        if len({c.parameter_ref for c in cases}) != len(cases):
            raise ValueError("parameters cannot be reused across datasets")
        if any(c.parameter_digest != _digest([seed, c.parameter_ref]) for c in cases):
            raise ValueError("parameter digest does not match frozen synthetic reference")
        if digest != _digest({"seed": seed, "cases": case_rows}):
            raise ValueError("fixture digest mismatch")
        counts = {
            (dataset, flow): sum(c.dataset == dataset and c.flow == flow for c in cases)
            for dataset in ("business", "smoke", "model_positive", "model_negative")
            for flow in FLOWS
        }
        for flow in FLOWS:
            if counts["business", flow] != 20 or counts["model_positive", flow] != 20:
                raise ValueError("each flow needs 20 business and 20 independent model positives")
            if counts["model_negative", flow] != 8:
                raise ValueError("each flow needs 8 independent model negatives")
            if counts["smoke", flow] != (3 if flow.startswith(("E01", "E03", "E04")) else 0):
                raise ValueError("smoke must be E01/E03/E04 x3")
        if sum(c.dataset == "internal_login" for c in cases) <= 0:
            raise ValueError("internal login requires N > 0")
        if sum(c.dataset == "calibration" for c in cases) <= 0:
            raise ValueError("calibration must be a separate dataset")
        return cls(1, seed, digest, cases)


def load_suite(path: Path | None = None) -> SuiteSpec:
    path = path or Path(__file__).with_name("scenarios.json")
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("scenario manifest must be an object")
    return SuiteSpec.from_mapping(raw)


@dataclass(frozen=True)
class BrowserEvidence:
    source: BrowserKind
    backend: Literal["cloud", "enterprise"]
    fixture_digest: str
    observation_digest: str
    candidate_digest: str
    source_evidence_digest: str

    def __post_init__(self) -> None:
        for name in (
            "fixture_digest",
            "observation_digest",
            "candidate_digest",
            "source_evidence_digest",
        ):
            _hex(getattr(self, name), name)
        if self.source not in (
            "real_browser",
            "fake_browser",
            "mock_browser",
        ) or self.backend not in ("cloud", "enterprise"):
            raise ValueError("invalid browser source")


@dataclass(frozen=True)
class DecisionInput:
    observation_digest: str
    candidate_digest: str
    fixture_digest: str
    task_kind: Literal["select_target"] = "select_target"

    @property
    def request_digest(self) -> str:
        return _digest(self.__dict__)


@dataclass(frozen=True)
class AttemptEvidence:
    source: SourceKind
    invocation_id: str
    source_evidence_digest: str
    request_digest: str
    response_digest: str | None
    outcome: Literal["selected", "abstained", "ambiguous", "unsupported", "error"]
    grounded: bool
    deployment_pin: str | None
    retry_requested: bool = False
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError("invalid decision source")
        _safe_id(self.invocation_id, "invocation_id")
        _hex(self.source_evidence_digest, "source_evidence_digest")
        _hex(self.request_digest, "request_digest")
        if self.response_digest is not None:
            _hex(self.response_digest, "response_digest")
        if self.outcome not in ("selected", "abstained", "ambiguous", "unsupported", "error"):
            raise ValueError("invalid decision outcome")
        if self.outcome == "error" and self.response_digest is not None:
            raise ValueError("error cannot claim a response")
        if self.outcome != "error" and self.response_digest is None:
            raise ValueError("decision response evidence missing")
        if type(self.grounded) is not bool or type(self.retry_requested) is not bool:
            raise ValueError("invalid decision flags")
        if self.source == "real_model" and (not self.grounded or self.deployment_pin is None):
            raise ValueError("real model requires grounded invocation and explicit pin")
        if self.duration_ms is not None and (
            type(self.duration_ms) is not int or self.duration_ms < 0
        ):
            raise ValueError("invalid serving duration")
        if self.source == "real_model" and self.duration_ms is None:
            raise ValueError("real model requires measured serving duration")
        if self.deployment_pin is not None:
            _safe_id(self.deployment_pin, "deployment_pin")


@dataclass(frozen=True)
class OracleEvidence:
    initial_correct: bool | None
    business_success: bool
    hazards: frozenset[Hazard]
    source_evidence_digest: str

    def __post_init__(self) -> None:
        if self.initial_correct is not None and type(self.initial_correct) is not bool:
            raise ValueError("invalid initial correctness")
        if type(self.business_success) is not bool or not self.hazards <= HAZARDS:
            raise ValueError("invalid oracle result")
        _hex(self.source_evidence_digest, "oracle source evidence")


@dataclass(frozen=True)
class TrajectoryEvent:
    """Only a neutral phase marker and digest of typed, value-free evidence."""

    case_id: str
    phase: Literal["browser", "decision", "oracle"]
    attempt_number: int | None
    metadata_digest: str

    def __post_init__(self) -> None:
        _safe_id(self.case_id, "case_id")
        _hex(self.metadata_digest, "metadata_digest")
        if self.phase == "decision":
            if self.attempt_number is None or self.attempt_number < 1:
                raise ValueError("decision event needs positive attempt number")
        elif self.attempt_number is not None:
            raise ValueError("non-decision event cannot have an attempt number")


@dataclass(frozen=True)
class BrowserReceipt:
    """Metadata read from the registered browser adapter's transport journal."""

    source_id: str
    backend: Literal["cloud", "enterprise"]
    transport: str
    fixture_digest: str
    observation_digest: str
    candidate_digest: str
    receipt_digest: str


@dataclass(frozen=True)
class DecisionReceipt:
    """Metadata read from transport, independently of model response claims."""

    source_id: str
    invocation_id: str
    attempt_number: int
    fixture_digest: str
    request_digest: str
    wire_request_digest: str
    response_digest: str | None
    response_deployment: str
    manifest_digest: str
    serving_version: str
    dtype: str
    transport: str
    reserved_calls: int
    max_tokens: int
    timeout_ms: int
    receipt_digest: str


@dataclass(frozen=True)
class RegisteredBrowser:
    source_id: str
    backend: Literal["cloud", "enterprise"]
    transport: str
    fixture_digest: str


@dataclass(frozen=True)
class RegisteredDecision:
    source_id: str
    kind: Literal["real_model", "deterministic"]
    checkpoint: str
    manifest_digest: str
    serving_version: str
    dtype: str
    transport: str
    max_calls: int
    max_tokens: int
    timeout_ms: int


@dataclass(frozen=True)
class BrowserProof:
    source_id: str
    receipt_digest: str
    backend: Literal["cloud", "enterprise"]
    transport: str
    fixture_digest: str
    observation_digest: str
    candidate_digest: str


@dataclass(frozen=True)
class DecisionProof:
    source_id: str
    kind: Literal["real_model", "deterministic"]
    receipt_digest: str
    invocation_id: str
    request_digest: str
    wire_request_digest: str
    response_digest: str | None
    checkpoint: str
    manifest_digest: str
    serving_version: str
    dtype: str
    transport: str
    max_calls: int
    max_tokens: int
    timeout_ms: int


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    status: CaseStatus
    browser: BrowserEvidence | None = None
    attempts: tuple[AttemptEvidence, ...] = ()
    oracle: OracleEvidence | None = None
    failure_code: str | None = None
    events: tuple[TrajectoryEvent, ...] = ()
    browser_proof: BrowserProof | None = None
    decision_proofs: tuple[DecisionProof | None, ...] = ()

    def __post_init__(self) -> None:
        _safe_id(self.case_id, "case_id")
        if self.status not in ("PASS", "FAIL", "WAITING_ENV"):
            raise ValueError("invalid case status")
        if self.status == "WAITING_ENV" and (self.browser or self.attempts or self.oracle):
            raise ValueError("waiting case cannot contain executed evidence")
        if self.status == "WAITING_ENV" and self.events:
            raise ValueError("waiting case cannot contain trajectory evidence")
        if self.status == "WAITING_ENV" and (self.browser_proof or self.decision_proofs):
            raise ValueError("waiting case cannot contain source proofs")
        if self.status == "PASS" and (
            self.browser is None or not self.attempts or self.oracle is None
        ):
            raise ValueError("pass requires browser and independent oracle evidence")
        if (
            self.status == "PASS"
            and self.oracle is not None
            and (not self.oracle.business_success or self.oracle.hazards)
        ):
            raise ValueError("pass contradicts independent oracle")
        if self.browser_proof is not None and (
            self.browser is None
            or self.browser_proof.receipt_digest != self.browser.source_evidence_digest
            or self.browser_proof.backend != self.browser.backend
            or self.browser_proof.fixture_digest != self.browser.fixture_digest
            or self.browser_proof.observation_digest != self.browser.observation_digest
            or self.browser_proof.candidate_digest != self.browser.candidate_digest
        ):
            raise ValueError("browser proof does not bind its observation")
        if len({a.invocation_id for a in self.attempts}) != len(self.attempts):
            raise ValueError("duplicate invocation evidence")
        if self.decision_proofs and len(self.decision_proofs) != len(self.attempts):
            raise ValueError("decision proof count mismatch")
        if any(
            proof is not None
            and (
                proof.invocation_id != attempt.invocation_id
                or proof.kind != attempt.source
                or proof.receipt_digest != attempt.source_evidence_digest
                or proof.request_digest != attempt.request_digest
                or proof.response_digest != attempt.response_digest
                or proof.checkpoint != attempt.deployment_pin
            )
            for proof, attempt in zip(self.decision_proofs, self.attempts, strict=False)
        ):
            raise ValueError("decision proof does not bind its attempt")
        if self.failure_code is not None:
            _safe_id(self.failure_code, "failure_code")
        if any(event.case_id != self.case_id for event in self.events):
            raise ValueError("trajectory case identity mismatch")
