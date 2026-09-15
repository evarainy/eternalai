"""Deterministic, request-relevant capability candidates for the intent prompt.

The selector revalidates every visible Registry definition, projects only an
allowlisted safe summary, ranks the whole visible set with integer lexical
scores, and admits complete equal-score groups within fixed count and byte
budgets. Candidates are prompt data only; they never grant execution rights.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from pydantic import ValidationError

from app.knowledge.basic_knowledge import (
    contains_sensitive_location_or_identity,
    sanitize_knowledge_text,
)
from app.ports.capability_registry import CapabilitySpec

SelectionOutcome: TypeAlias = Literal[
    "ready",
    "empty",
    "low_confidence",
    "ambiguous",
    "over_budget",
    "catalog_invalid",
]
TruncationReason: TypeAlias = Literal["relevance", "count", "bytes"]

CAPABILITY_CANDIDATE_PROTOCOL_VERSION = "capability-topk-v1"
MAX_CAPABILITY_CANDIDATES = 8
MAX_CAPABILITY_CONTRACT_BYTES = 4096
MAX_CAPABILITY_CANDIDATE_SEGMENT_BYTES = 16384
MAX_CAPABILITY_ID_LENGTH = 96
# Canonical description of every ranking/budget constant; the intent Prompt
# version digest covers it so an algorithm change cannot keep the same marker.
CAPABILITY_SELECTION_RULES = (
    f"{CAPABILITY_CANDIDATE_PROTOCOL_VERSION};k={MAX_CAPABILITY_CANDIDATES};"
    f"contract_bytes={MAX_CAPABILITY_CONTRACT_BYTES};"
    f"segment_bytes={MAX_CAPABILITY_CANDIDATE_SEGMENT_BYTES};"
    "normalize=nfkc,casefold,whitespace;tokens=ascii[a-z0-9]{2,},cjk_bigram;"
    "score=exact_id_phrase,exact_tag_phrase,exact_name_phrase,"
    "name_jaccard_milli,description_jaccard_milli,id_jaccard_milli;"
    "admission=complete_score_groups;json=ascii,sorted,compact"
)

_TRUNCATION_ORDER: tuple[TruncationReason, ...] = ("relevance", "count", "bytes")
_SCHEMA_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
_SCHEMA_ANNOTATION_KEYS = frozenset(
    {
        "title",
        "description",
        "default",
        "examples",
        "example",
        "$comment",
        "$schema",
        "$id",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
)
_ROOT_STRUCTURE_KEYS = frozenset({"type", "properties", "required", "additionalProperties"})
_REDACTED = "[REDACTED]"
_SAFE_CAPABILITY_ID = re.compile(r"[A-Za-z0-9._-]+")
_SENSITIVE_ID_MARKER = re.compile(
    r"(?:bearer|token|credential|secret|password|passwd|auth|authorization|"
    r"cookie|session)",
    re.IGNORECASE,
)
# Property keys are argument identifiers the model must reproduce exactly, never
# credential values. They get the value-shape checks (URL/email/IP/UNC) only: a
# credential-word rule would reject legitimate contracts such as the canonical OA
# ``authoritative_count`` or credential-named input fields that the confirm card
# already redacts downstream.
_SAFE_PROPERTY_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9._-]*")
_SAFE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:-]{0,95}")
_ASCII_TOKEN = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


@dataclass(frozen=True, slots=True)
class CandidateBinding:
    """Host-only identity of one admitted candidate for this request."""

    capability_id: str
    version: str
    fingerprint: str
    capability_type: str
    target_system: str | None
    intent_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapabilityCandidateSet:
    """Result of one deterministic candidate selection; never an authorization."""

    outcome: SelectionOutcome
    contracts: tuple[dict[str, object], ...]
    bindings: tuple[CandidateBinding, ...]
    visible_count: int
    selected_count: int
    omitted_count: int
    coverage_complete: bool
    truncated_by: tuple[TruncationReason, ...]
    payload_bytes: int
    payload_json: str = ""

    def trace_attributes(self) -> dict[str, Any]:
        """Return the value-free selection summary allowed in Trace."""

        return {
            "protocol_version": CAPABILITY_CANDIDATE_PROTOCOL_VERSION,
            "outcome": self.outcome,
            "coverage_complete": self.coverage_complete,
            "visible_count": self.visible_count,
            "selected_count": self.selected_count,
            "omitted_count": self.omitted_count,
            "truncated_by": list(self.truncated_by),
            "payload_bytes": self.payload_bytes,
        }


@dataclass(frozen=True, slots=True)
class _Candidate:
    spec: CapabilitySpec
    contract: dict[str, object]
    contract_bytes: int
    binding: CandidateBinding
    score: tuple[int, int, int, int, int, int]


class _InvalidCatalogError(ValueError):
    pass


def is_safe_capability_id(value: object) -> bool:
    """Return whether an ID is exactly usable as a prompt-visible identifier."""

    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_CAPABILITY_ID_LENGTH
        and _SAFE_CAPABILITY_ID.fullmatch(value) is not None
        and _SENSITIVE_ID_MARKER.search(value) is None
    )


def capability_fingerprint(capability: CapabilitySpec) -> str:
    """Hash the complete canonical definition for same-request drift checks."""

    canonical = json.dumps(
        capability.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_json(value: object) -> str:
    """Serialize exactly as the candidate segment is measured and sent."""

    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def select_capability_candidates(
    message: str,
    capabilities: Sequence[CapabilitySpec],
) -> CapabilityCandidateSet:
    """Select at most eight request-relevant candidates from the visible set."""

    visible = [item for item in capabilities if item.status == "active"]
    if not visible:
        return _failure("empty", visible_count=0, truncated_by=())
    try:
        candidates = _validated_candidates(visible)
    except _InvalidCatalogError:
        return _failure("catalog_invalid", visible_count=None, truncated_by=())

    query_tokens = _tokens(message)
    normalized_message = _normalize(message)
    scored = sorted(
        (_with_score(candidate, normalized_message, query_tokens) for candidate in candidates),
        key=lambda item: (_negated(item.score), item.binding.capability_id),
    )
    visible_count = len(scored)
    reasons: set[TruncationReason] = set()

    if visible_count <= MAX_CAPABILITY_CANDIDATES:
        prefix_groups = _score_groups(scored)
    else:
        if all(value == 0 for value in scored[0].score):
            return _failure(
                "low_confidence",
                visible_count=visible_count,
                truncated_by=("relevance",),
            )
        relevant = [item for item in scored if any(item.score)]
        if len(relevant) < visible_count:
            reasons.add("relevance")
        groups = _score_groups(relevant)
        if len(groups[0]) > MAX_CAPABILITY_CANDIDATES:
            return _failure(
                "ambiguous",
                visible_count=visible_count,
                truncated_by=("count",),
            )
        prefix_groups = []
        admitted = 0
        for group in groups:
            if admitted + len(group) > MAX_CAPABILITY_CANDIDATES:
                reasons.add("count")
                break
            prefix_groups.append(group)
            admitted += len(group)

    fitting_groups: list[list[_Candidate]] = []
    for index, group in enumerate(prefix_groups):
        if any(item.contract_bytes > MAX_CAPABILITY_CONTRACT_BYTES for item in group):
            if index == 0:
                return _failure(
                    "over_budget",
                    visible_count=visible_count,
                    truncated_by=_ordered(reasons | {"bytes"}),
                )
            reasons.add("bytes")
            break
        fitting_groups.append(group)

    while fitting_groups:
        selected = [item for group in fitting_groups for item in group]
        omitted = visible_count - len(selected)
        truncated_by = _ordered(reasons)
        payload_json = _segment_json(selected, omitted_count=omitted, truncated_by=truncated_by)
        if len(payload_json.encode("utf-8")) <= MAX_CAPABILITY_CANDIDATE_SEGMENT_BYTES:
            return CapabilityCandidateSet(
                outcome="ready",
                contracts=tuple(copy.deepcopy(item.contract) for item in selected),
                bindings=tuple(item.binding for item in selected),
                visible_count=visible_count,
                selected_count=len(selected),
                omitted_count=omitted,
                coverage_complete=omitted == 0,
                truncated_by=truncated_by,
                payload_bytes=len(payload_json.encode("utf-8")),
                payload_json=payload_json,
            )
        fitting_groups.pop()
        reasons.add("bytes")
    return _failure(
        "over_budget",
        visible_count=visible_count,
        truncated_by=_ordered(reasons | {"bytes"}),
    )


def _failure(
    outcome: SelectionOutcome,
    *,
    visible_count: int | None,
    truncated_by: tuple[TruncationReason, ...],
) -> CapabilityCandidateSet:
    counted = 0 if visible_count is None else visible_count
    return CapabilityCandidateSet(
        outcome=outcome,
        contracts=(),
        bindings=(),
        visible_count=counted,
        selected_count=0,
        omitted_count=counted,
        coverage_complete=outcome == "empty",
        truncated_by=truncated_by,
        payload_bytes=0,
    )


def _ordered(reasons: set[TruncationReason]) -> tuple[TruncationReason, ...]:
    return tuple(reason for reason in _TRUNCATION_ORDER if reason in reasons)


def _negated(score: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(-value for value in score)


def _score_groups(items: list[_Candidate]) -> list[list[_Candidate]]:
    groups: list[list[_Candidate]] = []
    for item in items:
        if groups and groups[-1][0].score == item.score:
            groups[-1].append(item)
        else:
            groups.append([item])
    return groups


def _segment_json(
    selected: list[_Candidate],
    *,
    omitted_count: int,
    truncated_by: tuple[TruncationReason, ...],
) -> str:
    return canonical_json(
        {
            "capability_candidates": {
                "protocol_version": CAPABILITY_CANDIDATE_PROTOCOL_VERSION,
                "coverage_complete": omitted_count == 0,
                "omitted_count": omitted_count,
                "truncated_by": list(truncated_by),
                "items": [item.contract for item in selected],
            }
        }
    )


def _validated_candidates(visible: list[CapabilitySpec]) -> list[_Candidate]:
    seen: set[str] = set()
    candidates: list[_Candidate] = []
    for original in visible:
        if not is_safe_capability_id(original.capability_id):
            raise _InvalidCatalogError("unsafe capability id")
        if original.capability_id in seen:
            raise _InvalidCatalogError("duplicate capability id")
        seen.add(original.capability_id)
        try:
            spec = CapabilitySpec.model_validate(original.model_dump(mode="python"))
        except (ValidationError, TypeError, ValueError, AttributeError) as exc:
            raise _InvalidCatalogError("capability definition failed validation") from exc
        spec = spec.model_copy(deep=True)
        if spec.capability_id != original.capability_id or spec.status != "active":
            raise _InvalidCatalogError("capability identity changed during validation")
        contract = _project_contract(spec)
        candidates.append(
            _Candidate(
                spec=spec,
                contract=contract,
                contract_bytes=len(canonical_json(contract).encode("utf-8")),
                binding=CandidateBinding(
                    capability_id=spec.capability_id,
                    version=spec.version,
                    fingerprint=capability_fingerprint(spec),
                    capability_type=spec.type,
                    target_system=spec.target_system,
                    intent_tags=tuple(spec.intent_tags),
                ),
                score=(0, 0, 0, 0, 0, 0),
            )
        )
    return candidates


def _project_contract(spec: CapabilitySpec) -> dict[str, object]:
    for text in (spec.name, spec.owner, spec.short_description, *spec.intent_tags):
        _require_safe_text(text)
    if _SAFE_VERSION.fullmatch(spec.version) is None:
        raise _InvalidCatalogError("unsafe version")
    _require_safe_text(spec.version)

    input_properties, input_required = _schema_keys(spec.input_schema)
    additional_properties = spec.input_schema.get("additionalProperties", True)
    if isinstance(additional_properties, dict):
        contract_additional: bool | str = "schema"
    elif isinstance(additional_properties, bool):
        contract_additional = additional_properties
    else:
        raise _InvalidCatalogError("invalid additionalProperties")
    allowed_argument_keys = sorted(input_properties)
    contract: dict[str, object] = {
        "capability_id": spec.capability_id,
        "capability_type": spec.type,
        "target_system": spec.target_system or "none",
        "status": spec.status,
        "allowed_argument_keys": allowed_argument_keys,
        "required_argument_keys": sorted(input_required),
        "additionalProperties": contract_additional,
        "short_description": spec.short_description,
        "owner": spec.owner,
        "version": spec.version,
        "risk_level": spec.risk_level,
        "input_summary": _schema_summary(spec.input_schema),
        "output_summary": _schema_summary(spec.output_schema),
    }
    if not allowed_argument_keys and contract_additional is False:
        contract["arguments_must_be"] = {}
    return contract


def _require_safe_text(value: str) -> None:
    if not value or sanitize_knowledge_text(value) == _REDACTED:
        raise _InvalidCatalogError("unsafe capability metadata")


def _schema_keys(schema: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise _InvalidCatalogError("schema properties must be an object")
    for key in properties:
        _require_safe_property_key(key)
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
        raise _InvalidCatalogError("schema required must be a list of strings")
    if len(set(required)) != len(required) or any(key not in properties for key in required):
        raise _InvalidCatalogError("schema required must be unique declared properties")
    return properties, list(required)


def _require_safe_property_key(key: object) -> None:
    if not isinstance(key, str) or _SAFE_PROPERTY_KEY.fullmatch(key) is None:
        raise _InvalidCatalogError("unsafe schema property key")
    if contains_sensitive_location_or_identity(key):
        raise _InvalidCatalogError("sensitive schema property key")


def _schema_summary(schema: dict[str, Any]) -> dict[str, object]:
    properties, required = _schema_keys(schema)
    partial = False
    raw_type = schema.get("type")
    if isinstance(raw_type, str) and raw_type in _SCHEMA_TYPES:
        root_type = raw_type
    else:
        root_type = "unspecified"
        partial = True
    if any(
        key not in _ROOT_STRUCTURE_KEYS and key not in _SCHEMA_ANNOTATION_KEYS for key in schema
    ):
        partial = True
    property_summaries: list[dict[str, str]] = []
    for key in sorted(properties):
        property_type, property_partial = _property_type(properties[key])
        partial = partial or property_partial
        property_summaries.append({"key": key, "type": property_type})
    additional: bool | str
    if root_type != "object":
        additional = "unspecified"
        if "additionalProperties" in schema:
            partial = True
    else:
        raw_additional = schema.get("additionalProperties", True)
        if isinstance(raw_additional, bool):
            additional = raw_additional
        elif isinstance(raw_additional, dict):
            additional = "schema"
            partial = True
        else:
            raise _InvalidCatalogError("invalid additionalProperties")
    return {
        "root_type": root_type,
        "properties": property_summaries,
        "required": sorted(required),
        "additionalProperties": additional,
        "partial": partial,
    }


def _property_type(schema: object) -> tuple[str, bool]:
    if not isinstance(schema, dict):
        return "unspecified", True
    raw_type = schema.get("type")
    supported = isinstance(raw_type, str) and raw_type in _SCHEMA_TYPES
    extra_keys = any(key != "type" and key not in _SCHEMA_ANNOTATION_KEYS for key in schema)
    if not supported:
        return "unspecified", True
    return str(raw_type), extra_keys


def _with_score(
    candidate: _Candidate,
    normalized_message: str,
    query_tokens: frozenset[str],
) -> _Candidate:
    spec = candidate.spec
    score = (
        int(_slug_phrase_in(spec.capability_id.casefold(), normalized_message)),
        int(any(_slug_phrase_in(tag, normalized_message) for tag in spec.intent_tags)),
        int(_name_phrase_in(_normalize(spec.name), normalized_message)),
        _jaccard_milli(query_tokens, _tokens(spec.name)),
        _jaccard_milli(query_tokens, _tokens(spec.short_description)),
        _jaccard_milli(query_tokens, _tokens(spec.capability_id)),
    )
    return _Candidate(
        spec=candidate.spec,
        contract=candidate.contract,
        contract_bytes=candidate.contract_bytes,
        binding=candidate.binding,
        score=score,
    )


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _tokens(value: str) -> frozenset[str]:
    normalized = _normalize(value)
    tokens = {token for token in _ASCII_TOKEN.findall(normalized) if len(token) >= 2}
    for run in _CJK_RUN.findall(normalized):
        tokens.update(run[index : index + 2] for index in range(len(run) - 1))
    return frozenset(tokens)


def _jaccard_milli(query: frozenset[str], field: frozenset[str]) -> int:
    union = query | field
    if not union:
        return 0
    return (1000 * len(query & field)) // len(union)


def _slug_phrase_in(phrase: str, normalized_message: str) -> bool:
    if not phrase:
        return False
    pattern = rf"(?<![a-z0-9._-]){re.escape(phrase)}(?![a-z0-9_-]|\.[a-z0-9])"
    return re.search(pattern, normalized_message) is not None


def _name_phrase_in(phrase: str, normalized_message: str) -> bool:
    if not phrase:
        return False
    start = r"(?<![a-z0-9])" if _is_ascii_alnum(phrase[0]) else ""
    end = r"(?![a-z0-9])" if _is_ascii_alnum(phrase[-1]) else ""
    return re.search(f"{start}{re.escape(phrase)}{end}", normalized_message) is not None


def _is_ascii_alnum(character: str) -> bool:
    return character.isascii() and character.isalnum()


__all__ = (
    "CAPABILITY_CANDIDATE_PROTOCOL_VERSION",
    "CAPABILITY_SELECTION_RULES",
    "MAX_CAPABILITY_CANDIDATES",
    "MAX_CAPABILITY_CANDIDATE_SEGMENT_BYTES",
    "MAX_CAPABILITY_CONTRACT_BYTES",
    "CandidateBinding",
    "CapabilityCandidateSet",
    "SelectionOutcome",
    "TruncationReason",
    "canonical_json",
    "capability_fingerprint",
    "is_safe_capability_id",
    "select_capability_candidates",
)
