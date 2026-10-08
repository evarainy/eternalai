"""Strict codecs. TypeSafe is the documented API; local v1 is our serving contract.

Local v1 does not claim checkpoint compatibility or model quality. Both codecs
operate on the same neutral request and validate before yielding an executable ID.
"""

from __future__ import annotations

import json
import math
from typing import Literal, Protocol

from pydantic import Field

from app.browser_skill.models import (
    Contract,
    DecisionCallContext,
    DecisionRequest,
    DecisionResult,
    DecisionStatus,
    OpaqueId,
    Probability,
    SafeText,
    ScopeStamp,
)


class InvalidResponse(ValueError):
    pass


class ModelMismatch(ValueError):
    pass


class _Usage(Contract):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class _Choice(Contract):
    type: Literal["choice"]
    choice: OpaqueId
    probabilities: dict[OpaqueId, Probability]
    confidence: Probability


class _Answers(Contract):
    select_target: _Choice


class _TypeSafeResponse(Contract):
    model: str
    answers: _Answers
    usage: _Usage


class _OpenRouterUsage(_Usage):
    cost: float = Field(ge=0, allow_inf_nan=False)


class _OpenRouterJevResponse(Contract):
    model: SafeText
    answers: _Answers
    usage: _OpenRouterUsage
    id: OpaqueId
    provider: Literal["TypeSafe"]


class _LocalProbability(Contract):
    id: OpaqueId
    p: Probability


class _LocalResponse(Contract):
    schema_version: Literal["browser_choice.v1"]
    request_id: OpaqueId
    deployment: str
    snapshot: ScopeStamp
    outcome: DecisionStatus
    selected_id: OpaqueId | None
    distribution: tuple[_LocalProbability, ...]
    certainty: Probability
    usage: _Usage


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def _strict_json(raw: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise InvalidResponse("duplicate_json_key")
            result[key] = value
        return result

    def constant(_: str) -> object:
        raise InvalidResponse("nonfinite_json")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _selection(
    request: DecisionRequest,
    context: DecisionCallContext,
    choice: str,
    probabilities: dict[str, float],
    confidence: float,
) -> DecisionResult:
    refs = {candidate.ref.target_id: candidate.ref for candidate in request.candidates}
    if set(probabilities) != set(refs) or choice not in refs:
        raise InvalidResponse("candidate_set_mismatch")
    if not math.isclose(math.fsum(probabilities.values()), 1.0, rel_tol=0, abs_tol=1e-6):
        raise InvalidResponse("probability_sum")
    maximum = max(probabilities.values())
    if probabilities[choice] != maximum:
        raise InvalidResponse("selection_not_maximal")
    if sum(value == maximum for value in probabilities.values()) != 1:
        return DecisionResult(
            request_id=request.request_id,
            scope=request.scope,
            status="ambiguous",
            reason="tie",
        )
    if confidence < context.budget.minimum_confidence:
        return DecisionResult(
            request_id=request.request_id,
            scope=request.scope,
            status="abstained",
            reason="confidence",
        )
    return DecisionResult(
        request_id=request.request_id,
        scope=request.scope,
        status="selected",
        selected=refs[choice],
    )


class DecisionCodec(Protocol):
    path: str

    def encode(self, request: DecisionRequest, context: DecisionCallContext) -> bytes: ...
    def decode(
        self,
        raw: bytes,
        request: DecisionRequest,
        context: DecisionCallContext,
    ) -> DecisionResult: ...


class TypeSafeCodec:
    """https://docs.typesafe.ai/api and /primitives/choice, explicit field allowlist."""

    path = "/v1/systemone"

    def encode(self, request: DecisionRequest, context: DecisionCallContext) -> bytes:
        return _json_bytes(
            {
                "model": context.manifest.request_model,
                "state": {
                    "scope": request.scope.model_dump(mode="json"),
                    "criteria": list(request.criteria),
                },
                "questions": {
                    "select_target": {
                        "type": "choice",
                        "instructions": "Select the existing candidate matching the criteria.",
                        "criteria": {
                            candidate.ref.target_id: {
                                "role": candidate.role,
                                "name": candidate.name,
                                "context": list(candidate.context),
                                "row_label": candidate.row_label,
                                "column_label": candidate.column_label,
                                "candidate_epoch": candidate.ref.candidate_epoch,
                            }
                            for candidate in request.candidates
                        },
                    }
                },
            }
        )

    def decode(
        self,
        raw: bytes,
        request: DecisionRequest,
        context: DecisionCallContext,
    ) -> DecisionResult:
        response = _TypeSafeResponse.model_validate(_strict_json(raw))
        if response.model != context.manifest.deployment_model:
            raise ModelMismatch("deployment_mismatch")
        answer = response.answers.select_target
        # TypeSafe does not echo epochs. Bind to this HTTP request; the executor
        # must compare result.validate_for(request, current_scope) before dispatch.
        return _selection(request, context, answer.choice, answer.probabilities, answer.confidence)


class OpenRouterJevCodec(TypeSafeCodec):
    """OpenRouter Decisions API Jev choice, with an explicit deployment pin."""

    path = "/api/alpha/decisions"

    def decode(
        self,
        raw: bytes,
        request: DecisionRequest,
        context: DecisionCallContext,
    ) -> DecisionResult:
        response = _OpenRouterJevResponse.model_validate(_strict_json(raw))
        if response.model != context.manifest.deployment_model:
            raise ModelMismatch("deployment_mismatch")
        answer = response.answers.select_target
        return _selection(request, context, answer.choice, answer.probabilities, answer.confidence)


class LocalChoiceCodec:
    """Separate versioned local service schema: arrays, explicit outcome and echoed snapshot."""

    path = "/select"

    def encode(self, request: DecisionRequest, context: DecisionCallContext) -> bytes:
        return _json_bytes(
            {
                "schema_version": "browser_choice.v1",
                "request_id": request.request_id,
                "deployment": context.manifest.deployment_model,
                "snapshot": request.scope.model_dump(mode="json"),
                "constraints": list(request.criteria),
                "options": [
                    {
                        "id": c.ref.target_id,
                        "epoch": c.ref.candidate_epoch,
                        "role": c.role,
                        "label": c.name,
                        "context": list(c.context),
                        "row_label": c.row_label,
                        "column_label": c.column_label,
                    }
                    for c in request.candidates
                ],
            }
        )

    def decode(
        self,
        raw: bytes,
        request: DecisionRequest,
        context: DecisionCallContext,
    ) -> DecisionResult:
        # Strict JSON precheck forbids duplicate keys/nonfinite literals; JSON mode
        # permits arrays for tuple fields without permitting scalar coercion.
        _strict_json(raw)
        response = _LocalResponse.model_validate_json(raw)
        if response.deployment != context.manifest.deployment_model:
            raise ModelMismatch("deployment_mismatch")
        if response.request_id != request.request_id or response.snapshot != request.scope:
            raise InvalidResponse("stale_response")
        if response.outcome != "selected":
            if response.selected_id is not None or response.distribution:
                raise InvalidResponse("nonselection_payload")
            return DecisionResult(
                request_id=request.request_id,
                scope=request.scope,
                status=response.outcome,
            )
        if response.selected_id is None:
            raise InvalidResponse("missing_selection")
        probabilities = {entry.id: entry.p for entry in response.distribution}
        if len(probabilities) != len(response.distribution):
            raise InvalidResponse("duplicate_probability")
        return _selection(
            request,
            context,
            response.selected_id,
            probabilities,
            response.certainty,
        )
