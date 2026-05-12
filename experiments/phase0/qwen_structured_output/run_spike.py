"""P0-SPIKE-001: Qwen structured output spike via provider API.

Tests ≥50 fixed samples across Intent, CapabilityRef, PlanDraft, ResponseEnvelope.
Structured output mode: json_object (json_schema not supported by this provider).
Success = Pydantic model_validate passes + business-critical fields non-empty.

Credentials are NEVER printed or written to files.
"""

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, ValidationError, __version__ as pydantic_version

# ---------------------------------------------------------------------------
# Pydantic schema definitions (client-side validation, not server-enforced)
#
# Enum-bearing fields use Literal[...] so model_validate() rejects invalid
# enum values directly — no separate post-hoc enum check needed.
# ---------------------------------------------------------------------------

class IntentResult(BaseModel):
    intent: Literal[
        "ask_question", "request_action", "provide_info",
        "chitchat", "complaint", "feedback",
    ]
    confidence: float
    entities: list[str]
    language: str

class CapabilityRefResult(BaseModel):
    capability_id: str
    domain: str
    sub_domain: str
    version: str
    description: str
    input_schema_hint: str
    output_schema_hint: str

class PlanStep(BaseModel):
    step_id: int
    action: str
    target: str
    depends_on: list[int]

class PlanDraftResult(BaseModel):
    plan_id: str
    goal: str
    steps: list[PlanStep]
    estimated_cost: Literal["low", "medium", "high"]
    risk_level: Literal["low", "medium", "high"]

class TraceInfo(BaseModel):
    request_id: str
    timestamp: str

class ResponseEnvelopeResult(BaseModel):
    status: Literal["success", "error"]
    code: int
    message: str
    data: dict
    trace: TraceInfo

# output_type -> Pydantic model mapping
SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "Intent": IntentResult,
    "CapabilityRef": CapabilityRefResult,
    "PlanDraft": PlanDraftResult,
    "ResponseEnvelope": ResponseEnvelopeResult,
}

# Business-critical fields that must be non-empty after Pydantic validation
CRITICAL_FIELDS: dict[str, list[str]] = {
    "Intent": ["intent", "confidence", "language"],
    "CapabilityRef": ["capability_id", "domain", "description"],
    "PlanDraft": ["plan_id", "goal", "steps"],
    "ResponseEnvelope": ["status", "code", "message", "trace"],
}

# Enum enforcement: Literal[...] types in Pydantic models above enforce enums
# at model_validate() time. Invalid enum values raise ValidationError and are
# categorized as schema_fail. This dict is kept for documentation/audit only.
ENUM_FIELD_DEFINITIONS: dict[str, dict[str, list[str]]] = {
    "Intent": {"intent": [
        "ask_question", "request_action", "provide_info",
        "chitchat", "complaint", "feedback",
    ]},
    "PlanDraft": {"estimated_cost": ["low", "medium", "high"], "risk_level": ["low", "medium", "high"]},
    "ResponseEnvelope": {"status": ["success", "error"]},
}

# Required minimum sample count per task prompt
MIN_SAMPLE_COUNT = 50

# Refusal detection keywords
REFUSAL_PATTERNS = [
    r"\bi can'?t\b", r"\bi cannot\b", r"\bi'?m not able\b",
    r"\bsorry.*can'?t\b", r"\bunable to\b", r"\bnot possible\b",
    r"\bnot allowed\b", r"\bdecline\b", r"\brefuse\b",
]

# ---------------------------------------------------------------------------
# Failure categories (honest, mutually exclusive)
# ---------------------------------------------------------------------------
# parse_fail        — response is not valid JSON
# schema_fail       — valid JSON but Pydantic model_validate rejected it
#                     (includes enum failures: invalid Literal[...] values
#                      raise ValidationError and are caught here)
# critical_empty    — Pydantic passed but a business-critical field is empty
# refusal           — model explicitly refused the request
# timeout           — request timed out
# api_error         — provider/network error
# ok                — passed all checks
#
# NOTE: enum_fail is NOT a separate category. Invalid enum values are rejected
# by Pydantic's Literal[...] type constraint during model_validate() and
# reported as schema_fail. The error message will include "pydantic_validation_error".

FAILURE_CATEGORIES = [
    "parse_fail", "schema_fail", "critical_empty",
    "refusal", "timeout", "api_error", "ok",
]

# ---------------------------------------------------------------------------
# Sample definitions — 54 total (≥13 per type)
# ---------------------------------------------------------------------------

@dataclass
class Sample:
    id: str
    output_type: str  # Intent | CapabilityRef | PlanDraft | ResponseEnvelope
    user_msg: str
    system_hint: str

def build_samples() -> list[Sample]:
    samples: list[Sample] = []

    # --- Intent samples (14) ---
    intent_prompts = [
        ("INT-001", "What's the weather in Beijing tomorrow?"),
        ("INT-002", "Please book a meeting room for 3pm."),
        ("INT-003", "The project deadline is next Friday."),
        ("INT-004", "Hey, how are you doing today?"),
        ("INT-005", "Your app keeps crashing on my phone!"),
        ("INT-006", "I think the UI could use more contrast."),
        ("INT-007", "Can you translate this to English?"),
        ("INT-008", "Send the report to my email."),
        ("INT-009", "The server IP is 10.0.0.5."),
        ("INT-010", "Nice weather we're having!"),
        ("INT-011", "I've been waiting 2 hours for support!"),
        ("INT-012", "The onboarding flow was confusing."),
        ("INT-013", "How do I reset my password?"),
        ("INT-014", "Schedule a call with the design team."),
    ]
    intent_system = (
        'Classify user intent. Output JSON: '
        '{"intent": "<one of: ask_question|request_action|provide_info|chitchat|complaint|feedback>", '
        '"confidence": <0.0-1.0>, "entities": [<extracted entities>], "language": "<detected language code>"}'
    )
    for sid, msg in intent_prompts:
        samples.append(Sample(
            id=sid, output_type="Intent", user_msg=msg,
            system_hint=intent_system,
        ))

    # --- CapabilityRef samples (14) ---
    cap_prompts = [
        ("CAP-001", "Summarize the document management capability."),
        ("CAP-002", "What does the user authentication capability do?"),
        ("CAP-003", "Describe the email notification capability."),
        ("CAP-004", "What are the inputs and outputs of the search capability?"),
        ("CAP-005", "Summarize the payment processing capability."),
        ("CAP-006", "Describe the file upload capability."),
        ("CAP-007", "What does the logging capability handle?"),
        ("CAP-008", "Summarize the scheduling capability."),
        ("CAP-009", "Describe the data export capability."),
        ("CAP-010", "What does the role-based access control capability do?"),
        ("CAP-011", "Summarize the audit trail capability."),
        ("CAP-012", "Describe the webchat integration capability."),
        ("CAP-013", "What are the inputs and outputs of the image processing capability?"),
        ("CAP-014", "Summarize the real-time collaboration capability."),
    ]
    cap_system = (
        'Describe a software capability as a reference. Output JSON: '
        '{"capability_id": "<domain.sub_domain>", "domain": "<business domain>", '
        '"sub_domain": "<sub domain>", "version": "<semver>", '
        '"description": "<one sentence>", "input_schema_hint": "<brief input description>", '
        '"output_schema_hint": "<brief output description>"}'
    )
    for sid, msg in cap_prompts:
        samples.append(Sample(
            id=sid, output_type="CapabilityRef", user_msg=msg,
            system_hint=cap_system,
        ))

    # --- PlanDraft samples (13) ---
    plan_prompts = [
        ("PLN-001", "Create a plan to onboard a new team member."),
        ("PLN-002", "Draft a plan for migrating the database."),
        ("PLN-003", "Plan the deployment of version 2.0."),
        ("PLN-004", "Create a plan to set up CI/CD pipeline."),
        ("PLN-005", "Draft a plan for quarterly security audit."),
        ("PLN-006", "Plan the user acceptance testing phase."),
        ("PLN-007", "Create a plan to optimize API response times."),
        ("PLN-008", "Draft a plan for data backup and recovery."),
        ("PLN-009", "Plan the rollout of the new notification system."),
        ("PLN-010", "Create a plan to refactor the auth module."),
        ("PLN-011", "Draft a plan for the annual disaster recovery drill."),
        ("PLN-012", "Plan the integration with the new CRM system."),
        ("PLN-013", "Create a plan for scaling the microservices architecture."),
    ]
    plan_system = (
        'Draft an execution plan. Output JSON: '
        '{"plan_id": "<plan-NNN>", "goal": "<one sentence goal>", '
        '"steps": [{"step_id": <int>, "action": "<what>", "target": "<on what>", '
        '"depends_on": [<step_ids>]}], "estimated_cost": "<low|medium|high>", '
        '"risk_level": "<low|medium|high>"}'
    )
    for sid, msg in plan_prompts:
        samples.append(Sample(
            id=sid, output_type="PlanDraft", user_msg=msg,
            system_hint=plan_system,
        ))

    # --- ResponseEnvelope samples (13) ---
    env_prompts = [
        ("ENV-001", "Wrap a successful user creation response."),
        ("ENV-002", "Wrap a not-found error for order #12345."),
        ("ENV-003", "Wrap a successful payment confirmation."),
        ("ENV-004", "Wrap a validation error for missing email field."),
        ("ENV-005", "Wrap a successful list retrieval of 10 items."),
        ("ENV-006", "Wrap a rate limit exceeded error."),
        ("ENV-007", "Wrap a successful file upload response."),
        ("ENV-008", "Wrap an unauthorized access error."),
        ("ENV-009", "Wrap a successful search results response."),
        ("ENV-010", "Wrap a server timeout error."),
        ("ENV-011", "Wrap a successful config update response."),
        ("ENV-012", "Wrap a conflict error for duplicate entry."),
        ("ENV-013", "Wrap a successful batch operation response."),
    ]
    env_system = (
        'Wrap an API response. Output JSON: '
        '{"status": "<success|error>", "code": <HTTP status code>, '
        '"message": "<human readable>", "data": {<response payload>}, '
        '"trace": {"request_id": "<uuid>", "timestamp": "<ISO8601>"}}'
    )
    for sid, msg in env_prompts:
        samples.append(Sample(
            id=sid, output_type="ResponseEnvelope", user_msg=msg,
            system_hint=env_system,
        ))

    return samples


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

# Explicit request timeout in seconds
REQUEST_TIMEOUT_S = 30

@dataclass
class SampleResult:
    sample_id: str
    output_type: str
    success: bool
    failure_category: str  # one of FAILURE_CATEGORIES
    schema_validation_passed: bool = False
    raw_response: str = ""
    parsed: dict | None = None
    error: str = ""
    latency_ms: float = 0.0


def _sanitize_error(exc: Exception) -> str:
    """Return safe error detail — no URLs, keys, tokens, headers."""
    cls = type(exc).__name__
    # Extract HTTP status if present (openai SDK wraps it)
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status:
        return f"{cls} (status={status})"
    return cls


def _detect_refusal(text: str) -> bool:
    """Heuristic refusal detection on raw response text."""
    lower = text.lower()
    for pat in REFUSAL_PATTERNS:
        if re.search(pat, lower):
            return True
    return False


def validate_sample(sample: Sample, raw_text: str) -> SampleResult:
    """Validate via Pydantic model_validate + business-critical field checks.

    Enum validation: Enum-bearing fields use Literal[...] types, so invalid
    enum values are rejected by model_validate() as schema_fail (Pydantic
    ValidationError). No separate post-hoc enum check is needed.

    Failure categories (mutually exclusive, first match wins):
      1. refusal       — model explicitly refused
      2. parse_fail    — not valid JSON / not a JSON object
      3. schema_fail   — Pydantic model_validate rejected it (includes enum)
      4. critical_empty — Pydantic passed but a critical field is empty
      5. ok            — all checks passed
    """
    sr = SampleResult(
        sample_id=sample.id,
        output_type=sample.output_type,
        success=False,
        failure_category="ok",
    )

    # 0. Refusal check (on raw text, before JSON parse)
    if _detect_refusal(raw_text):
        sr.failure_category = "refusal"
        sr.error = "model_refused"
        return sr

    # 1. Parse JSON
    try:
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()
        data = json.loads(cleaned)
        sr.parsed = data
    except (json.JSONDecodeError, ValueError):
        sr.failure_category = "parse_fail"
        sr.error = "json_parse_error"
        return sr

    if not isinstance(data, dict):
        sr.failure_category = "parse_fail"
        sr.error = f"expected_dict_got_{type(data).__name__}"
        return sr

    # 2. Pydantic model_validate (the real schema check)
    model_cls = SCHEMA_MAP.get(sample.output_type)
    if model_cls is None:
        sr.failure_category = "schema_fail"
        sr.error = f"no_schema_for_type_{sample.output_type}"
        return sr

    try:
        model_cls.model_validate(data)
        sr.schema_validation_passed = True
    except ValidationError as ve:
        sr.failure_category = "schema_fail"
        # Sanitize: only error count and first error location, no field values
        sr.error = f"pydantic_validation_error ({ve.error_count()} error(s))"
        return sr

    # 3. Business-critical field non-empty check (post-Pydantic)
    critical = CRITICAL_FIELDS.get(sample.output_type, [])
    empty_critical = []
    for fname in critical:
        val = data.get(fname)
        if val is None or val == "" or val == [] or val == {}:
            empty_critical.append(fname)

    if empty_critical:
        sr.failure_category = "critical_empty"
        sr.error = f"empty_critical_fields: {empty_critical}"
        return sr

    # All checks passed
    sr.failure_category = "ok"
    sr.success = True
    return sr


def _self_check(samples: list[Sample]) -> list[str]:
    """Run self-checks. Returns list of errors (empty = all passed)."""
    errors: list[str] = []

    # 1. All sample output_types must have a model mapping
    for s in samples:
        if s.output_type not in SCHEMA_MAP:
            errors.append(f"Sample {s.id} has output_type '{s.output_type}' with no SCHEMA_MAP entry")

    # 2. Sample count >= minimum
    if len(samples) < MIN_SAMPLE_COUNT:
        errors.append(f"Sample count {len(samples)} < required minimum {MIN_SAMPLE_COUNT}")

    # 3. Verify Literal fields exist in models for documented enums
    import inspect
    for type_name, enum_fields in ENUM_FIELD_DEFINITIONS.items():
        model_cls = SCHEMA_MAP.get(type_name)
        if model_cls is None:
            errors.append(f"ENUM_FIELD_DEFINITIONS references type '{type_name}' with no SCHEMA_MAP entry")
            continue
        for field_name in enum_fields:
            if field_name not in model_cls.model_fields:
                errors.append(f"{type_name}.{field_name} documented in ENUM_FIELD_DEFINITIONS but not in model")

    # 4. Verify enum-bearing model fields are Literal (not str)
    for type_name, model_cls in SCHEMA_MAP.items():
        for fname, finfo in model_cls.model_fields.items():
            ann = finfo.annotation
            # Check if the annotation is str when it should be Literal
            if type_name in ENUM_FIELD_DEFINITIONS and fname in ENUM_FIELD_DEFINITIONS[type_name]:
                if ann is str:
                    errors.append(f"{type_name}.{fname} is str but should be Literal[...] (enum field not enforced)")

    return errors


def run_spike() -> dict:
    """Execute the full spike and return results dict."""
    base_url = os.environ.get("LLM_BASE_URL", "")
    api_key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "")

    if not all([base_url, api_key, model]):
        print("ERROR: Missing environment variables", file=sys.stderr)
        sys.exit(1)

    # Explicit timeout on the client
    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=REQUEST_TIMEOUT_S,
    )
    samples = build_samples()

    # Self-check before execution
    check_errors = _self_check(samples)
    if check_errors:
        print("SELF-CHECK FAILED:", file=sys.stderr)
        for err in check_errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(2)

    results: list[SampleResult] = []

    print(f"Running spike: {len(samples)} samples, model={model}")
    print(f"Structured output mode: json_object (json_schema not supported by provider)")
    print(f"Pydantic version: {pydantic_version}")
    print(f"Enum validation: Literal[...] types in Pydantic models (model_validate rejects invalid)")
    print(f"Request timeout: {REQUEST_TIMEOUT_S}s")
    print(f"Self-check: PASSED")
    print("-" * 60)

    for i, sample in enumerate(samples):
        print(f"[{i+1}/{len(samples)}] {sample.id} ({sample.output_type})...", end=" ", flush=True)
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": sample.system_hint},
                    {"role": "user", "content": sample.user_msg},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=1024,
            )
            latency = (time.time() - t0) * 1000
            raw = resp.choices[0].message.content or ""
            sr = validate_sample(sample, raw)
            sr.latency_ms = latency
            sr.raw_response = raw
            if sr.success:
                print("OK")
            else:
                print(f"FAIL ({sr.failure_category}: {sr.error})")
        except Exception as e:
            safe_err = _sanitize_error(e)
            # Check if it's a timeout
            is_timeout = "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower()
            sr = SampleResult(
                sample_id=sample.id,
                output_type=sample.output_type,
                success=False,
                failure_category="timeout" if is_timeout else "api_error",
                error=safe_err,
            )
            print(f"ERROR ({sr.failure_category}: {safe_err})")
        results.append(sr)

    # --- Aggregate stats ---
    total = len(results)
    passed = sum(1 for r in results if r.success)
    success_rate = passed / total * 100 if total > 0 else 0.0

    by_type: dict[str, list[SampleResult]] = {}
    for r in results:
        by_type.setdefault(r.output_type, []).append(r)

    type_stats = {}
    for tp, rs in by_type.items():
        tp_total = len(rs)
        tp_passed = sum(1 for r in rs if r.success)
        type_stats[tp] = {
            "total": tp_total,
            "passed": tp_passed,
            "failed": tp_total - tp_passed,
            "success_rate": round(tp_passed / tp_total * 100, 1) if tp_total else 0,
        }

    # Failure category breakdown
    failure_categories = {}
    for r in results:
        if r.failure_category != "ok":
            failure_categories[r.failure_category] = failure_categories.get(r.failure_category, 0) + 1

    avg_latency = sum(r.latency_ms for r in results) / total if total else 0
    p50_latency = sorted(r.latency_ms for r in results)[total // 2] if total else 0

    failed_samples = [
        {
            "id": r.sample_id,
            "type": r.output_type,
            "category": r.failure_category,
            "error": r.error,
        }
        for r in results if not r.success
    ]

    # Find malformed output samples for ADR evidence (up to 2)
    malformed_samples = []
    for r in results:
        if not r.success and r.failure_category == "schema_fail":
            malformed_samples.append({
                "id": r.sample_id,
                "category": r.failure_category,
                "error": r.error,
            })
            if len(malformed_samples) >= 2:
                break
    for r in results:
        if not r.success and r.failure_category == "parse_fail":
            malformed_samples.append({
                "id": r.sample_id,
                "category": r.failure_category,
                "error": r.error,
            })
            if len(malformed_samples) >= 2:
                break

    meets_threshold = success_rate >= 80.0

    report = {
        "model": model,
        "total_samples": total,
        "passed": passed,
        "failed": total - passed,
        "success_rate": round(success_rate, 1),
        "meets_threshold": meets_threshold,
        "structured_output_mode": "json_object",
        "json_schema_supported": False,
        "pydantic_version": pydantic_version,
        "pydantic_validation_used": True,
        "enum_validation_method": "Literal[type] in Pydantic model; rejected by model_validate as schema_fail",
        "self_check_passed": True,
        "request_timeout_s": REQUEST_TIMEOUT_S,
        "avg_latency_ms": round(avg_latency, 1),
        "p50_latency_ms": round(p50_latency, 1),
        "type_stats": type_stats,
        "failure_categories": failure_categories,
        "failed_samples": failed_samples,
        "malformed_output_samples": malformed_samples,
    }

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed}/{total} passed ({report['success_rate']}%)")
    print(f"Threshold (>=80%): {'PASS' if meets_threshold else 'FAIL'}")
    print(f"Pydantic version: {pydantic_version}")
    print(f"Avg latency: {report['avg_latency_ms']}ms | P50: {report['p50_latency_ms']}ms")
    print(f"Failure categories: {failure_categories}")
    print()
    for tp, stats in type_stats.items():
        print(f"  {tp}: {stats['passed']}/{stats['total']} ({stats['success_rate']}%)")
    print()

    return report


if __name__ == "__main__":
    report = run_spike()
    # Write report to temp for ADR evidence capture
    report_path = os.path.join(
        os.environ.get("TEMP", "/tmp"),
        "p0_spike_001_report.json",
    )
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Report saved to: {report_path}")

    # Exit non-zero if below threshold
    if not report["meets_threshold"]:
        print("EXIT 1: success rate below 80% threshold", file=sys.stderr)
        sys.exit(1)
