"""Exact browser_choice.v1 wire plus a smaller, approved model input projection."""

from __future__ import annotations

import math
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from app.browser_skill.models import (
    Contract,
    DecisionStatus,
    Digest,
    Epoch,
    OpaqueId,
    Probability,
    Role,
    SafeText,
    ScopeStamp,
)
from services.decision.manifest import digest, strict_json


class ChoiceOption(Contract):
    id: OpaqueId
    epoch: Epoch
    role: Role
    label: SafeText | None
    context: Annotated[tuple[SafeText, ...], Field(max_length=16)]
    row_label: SafeText | None
    column_label: SafeText | None


class ChoiceRequest(Contract):
    schema_version: Literal["browser_choice.v1"]
    request_id: OpaqueId
    deployment: SafeText
    snapshot: ScopeStamp
    constraints: Annotated[tuple[SafeText, ...], Field(min_length=1, max_length=16)]
    options: Annotated[tuple[ChoiceOption, ...], Field(min_length=1, max_length=255)]

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({option.id for option in self.options}) != len(self.options):
            raise ValueError("decision_duplicate_option")
        return self

    @classmethod
    def parse(cls, raw: bytes, maximum_bytes: int) -> Self:
        if len(raw) > maximum_bytes:
            raise ValueError("decision_request_budget")
        strict_json(raw)
        return cls.model_validate_json(raw)


class EngineOption(Contract):
    id: OpaqueId
    role: Role
    label: SafeText | None
    context: tuple[SafeText, ...]
    row_label: SafeText | None
    column_label: SafeText | None


class EngineInput(Contract):
    task_kind: Literal["select_target"] = "select_target"
    criteria: tuple[SafeText, ...]
    options: tuple[EngineOption, ...]


class ProjectionPolicy(Contract):
    """Trusted registration of already approved labels; never request-owned."""

    allowed_constraints: tuple[SafeText, ...]
    allowed_labels: tuple[SafeText, ...]
    allowed_context: tuple[SafeText, ...]
    allowed_roles: tuple[Role, ...]
    maximum_candidates: Annotated[int, Field(ge=1, le=255)] = 128

    @property
    def digest(self) -> str:
        return digest("decision_projection.v1", self.model_dump(mode="json"))

    def project(self, request: ChoiceRequest) -> EngineInput:
        if len(request.options) > self.maximum_candidates or not set(request.constraints) <= set(
            self.allowed_constraints
        ):
            raise ValueError("decision_input_unapproved")
        for option in request.options:
            if option.role not in self.allowed_roles or not set(option.context) <= set(
                self.allowed_context
            ):
                raise ValueError("decision_input_unapproved")
            if any(
                label not in self.allowed_labels
                for label in (option.label, option.row_label, option.column_label)
                if label is not None
            ):
                raise ValueError("decision_input_unapproved")
        return EngineInput(
            criteria=request.constraints,
            options=tuple(
                EngineOption(
                    id=item.id,
                    role=item.role,
                    label=item.label,
                    context=item.context,
                    row_label=item.row_label,
                    column_label=item.column_label,
                )
                for item in request.options
            ),
        )


class ProbabilityEntry(Contract):
    id: OpaqueId
    p: Probability


class Usage(Contract):
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]


class EngineResult(Contract):
    outcome: DecisionStatus
    selected_id: OpaqueId | None
    distribution: tuple[ProbabilityEntry, ...]
    certainty: Probability
    usage: Usage
    # A model choice probability is only usable with checkpoint-bound calibration.
    # Synthetic probability is restricted to the explicitly synthetic deployment.
    score_semantics: Literal["synthetic_probability", "laya_answer_confidence"] = (
        "synthetic_probability"
    )
    calibration_digest: Digest | None = None

    @model_validator(mode="after")
    def shape(self) -> Self:
        if self.outcome == "selected":
            if self.selected_id is None or not self.distribution:
                raise ValueError("decision_selection_missing")
        elif self.selected_id is not None or self.distribution:
            raise ValueError("decision_nonselection_payload")
        if len({entry.id for entry in self.distribution}) != len(self.distribution):
            raise ValueError("decision_duplicate_probability")
        if (self.score_semantics == "laya_answer_confidence") != (
            self.calibration_digest is not None
        ):
            raise ValueError("decision_score_calibration_unbound")
        return self


class ChoiceResponse(EngineResult):
    schema_version: Literal["browser_choice.v1"] = "browser_choice.v1"
    request_id: OpaqueId
    deployment: SafeText
    snapshot: ScopeStamp
    manifest_digest: Digest | None = None


def encode_response(
    request: ChoiceRequest,
    result: EngineResult,
    *,
    minimum_confidence: float,
    maximum_bytes: int,
    expected_semantics: Literal[
        "synthetic_probability", "laya_answer_confidence"
    ] = "synthetic_probability",
    calibration_digest: Digest | None = None,
    manifest_digest: Digest | None = None,
) -> bytes:
    checked = EngineResult.model_validate_json(result.model_dump_json(warnings=False))
    if (checked.score_semantics, checked.calibration_digest) != (
        expected_semantics, calibration_digest
    ):
        raise ValueError("decision_score_semantics_mismatch")
    if (expected_semantics == "laya_answer_confidence") != (manifest_digest is not None):
        raise ValueError("decision_manifest_binding_missing")
    if checked.outcome == "selected":
        probabilities = {item.id: item.p for item in checked.distribution}
        if set(probabilities) != {option.id for option in request.options}:
            raise ValueError("decision_candidate_set_mismatch")
        tolerance = (
            len(probabilities) * 0.00005 + 1e-6
            if checked.score_semantics == "laya_answer_confidence" else 1e-6
        )
        if not math.isclose(math.fsum(probabilities.values()), 1.0, rel_tol=0, abs_tol=tolerance):
            raise ValueError("decision_probability_sum")
        maximum = max(probabilities.values())
        chosen = checked.selected_id
        if chosen is None or chosen not in probabilities or probabilities[chosen] != maximum:
            raise ValueError("decision_selection_not_maximal")
        if sum(value == maximum for value in probabilities.values()) != 1:
            checked = EngineResult(
                outcome="ambiguous",
                selected_id=None,
                distribution=(),
                certainty=0.0,
                usage=checked.usage,
                score_semantics=checked.score_semantics,
                calibration_digest=checked.calibration_digest,
            )
        elif checked.certainty < minimum_confidence:
            checked = EngineResult(
                outcome="abstained",
                selected_id=None,
                distribution=(),
                certainty=checked.certainty,
                usage=checked.usage,
                score_semantics=checked.score_semantics,
                calibration_digest=checked.calibration_digest,
            )
    response = ChoiceResponse(
        **checked.model_dump(),
        request_id=request.request_id,
        deployment=request.deployment,
        snapshot=request.snapshot,
        manifest_digest=manifest_digest,
    )
    raw = response.model_dump_json().encode()
    if len(raw) > maximum_bytes:
        raise ValueError("decision_response_budget")
    return raw


def schema_digest(model: type[ChoiceRequest] | type[ChoiceResponse]) -> Digest:
    return digest("decision_wire_schema.v1", model.model_json_schema())
