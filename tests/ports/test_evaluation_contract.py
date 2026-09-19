"""Evidence stays immutable and private across the Pydantic execution boundary."""

from dataclasses import FrozenInstanceError, asdict, replace

import pytest
from pydantic import ValidationError

from app.ports.capability_gateway import ExecutionResult
from app.ports.evaluation import EvaluationScope, VerificationChecks
from tests.evaluator.test_overview_postconditions import evidence


def test_value_types_are_immutable_and_payload_is_not_serialized():
    value, _ = evidence()
    marker = "synthetic-private-marker"
    value = replace(value, output_json='{"marker":"' + marker + '"}')
    execution = ExecutionResult(status="completed", trace_id="public", postcondition_input=value)
    assert execution.postcondition_input == value
    assert set(execution.model_dump()) == {"status", "data", "error_code", "trace_id"}
    assert marker not in execution.model_dump_json()
    for obj in (value, value.scope, *value.observations, execution):
        assert marker not in repr(obj)
        assert "synthetic-todo" not in repr(obj)
    with pytest.raises(FrozenInstanceError):
        value.output_json = "{}"
    with pytest.raises(FrozenInstanceError):
        value.observations[0].attempt = 2
    with pytest.raises(ValidationError):
        ExecutionResult(status="completed", trace_id="public", postcondition_input=asdict(value))
    assert ExecutionResult(status="completed", trace_id="public").postcondition_input is None


@pytest.mark.parametrize("bad", [None, "", " ", 1, True, [], {}])
def test_scope_requires_nonempty_exact_strings(bad):
    with pytest.raises(ValueError, match="nonempty"):
        EvaluationScope(bad, "trace", "session", "tenant", "user")


@pytest.mark.parametrize("bad", [True, False, 0, -1, 1.0, "1"])
def test_attempt_requires_strict_positive_integer(bad):
    value, _ = evidence()
    with pytest.raises(ValueError, match="positive integer"):
        replace(value.observations[0], attempt=bad)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("scope", {}),
        ("observations", []),
        ("observations", ({},)),
        ("rule_id", "other"),
        ("structure_result", "not_checked"),
        ("output_json", None),
    ],
)
def test_input_rejects_mutable_or_invalid_values(field, bad):
    value, _ = evidence()
    with pytest.raises(ValueError):
        replace(value, **{field: bad})


def test_verification_enums_are_closed():
    with pytest.raises(ValueError, match="enum"):
        VerificationChecks("not_applicable", "passed", "passed")
