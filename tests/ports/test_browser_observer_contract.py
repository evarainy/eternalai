from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from app.browser_skill.models import ObservationRequest, VisibleCandidate, VisibleProjection
from app.ports.browser import WebAdapter
from tests.browser_skill.factories import projection, scope


def test_observe_port_takes_registered_region_request() -> None:
    params = tuple(inspect.signature(WebAdapter.observe).parameters)
    assert params == ("self", "session", "request", "policy")
    assert ObservationRequest(region_id="inbox", expected_scope=scope()).region_id == "inbox"
    with pytest.raises(ValidationError, match="observation_region_mismatch"):
        ObservationRequest(region_id="other", expected_scope=scope())
    with pytest.raises(ValidationError, match="Extra inputs"):
        ObservationRequest.model_validate({"region_id": "inbox", "selector": "body"})


def test_projection_cannot_carry_raw_dom_fields_or_cross_frame_candidates() -> None:
    item = projection()
    candidate = item.candidates[0]
    for field in ("raw_text", "html", "input_value", "cookie", "element_handle"):
        with pytest.raises(ValidationError, match="Extra inputs"):
            VisibleCandidate.model_validate({**candidate.model_dump(), field: "synthetic"})
        with pytest.raises(ValidationError, match="Extra inputs"):
            VisibleProjection.model_validate({**item.model_dump(), field: "synthetic"})
    assert candidate.ref.scope.frame_path == item.scope.frame_path
    assert item.scope.frame_path[0] == item.frames.frame
