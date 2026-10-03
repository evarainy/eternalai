"""Fixed source loading and query boundaries; no browser/provider success oracle."""

import hashlib
import json
from typing import cast

import pytest
from pydantic import ValidationError

from app.browser_skill.models import ModelManifest
from app.browser_skill.publication_contracts import BrowserPublicationManifest
from app.browser_skill.site_rules import RegisteredQueryReadRule
from app.infra.adapters.oa.capabilities import expected_oa_capabilities
from app.infra.browser.composition import BrowserVerticalDependencies
from app.infra.browser.fixed_synthetic_seed import (
    SYNTHETIC_DETAIL_CAPABILITY_ID,
    SYNTHETIC_ORIGIN,
    SyntheticDetailArguments,
    build_fixed_synthetic_query_source,
    build_fixed_synthetic_source,
    install_fixed_synthetic_seed,
)
from app.ports.browser_publication_store import BrowserPublicationError
from app.ports.llm_provider import LLMProviderPort
from app.ports.structured_output import StructuredOutputPort


def _model() -> ModelManifest:
    return ModelManifest(
        request_model="fixture_model_v1", deployment_model="deploymentfixturev1",
        manifest_digest=hashlib.sha256(b"synthetic-query-loader-only-v1").hexdigest(),
    )


def test_query_seed_binds_admitted_key_without_expected_output() -> None:
    source = build_fixed_synthetic_query_source(_model())
    manifest = BrowserPublicationManifest.model_validate_json(source.manifest.model_dump_json())
    rule = manifest.site.read_rule
    assert isinstance(rule, RegisteredQueryReadRule)
    assert rule.key_ref.name == "business_key"
    assert manifest.skill.parameters == ("business_key",)
    assert tuple(field.model_dump() for field in rule.fields) == ({"field_id": "collection"},)
    assert rule.output_schema_json == manifest.output.output_schema_json
    assert rule.maximum_result_bytes == manifest.output.maximum_bytes
    assert source.rules.read.object_type is not None
    assert source.rules.read.object_type.selector == "#fixture-object-type"
    assert b'id="fixture-object-type">system_message_collection<' in source.html
    with pytest.raises(BrowserPublicationError, match="fixture_expected_values_forbidden"):
        _ = source.fixture_reference_values


def test_query_capability_is_separate_and_does_not_widen_canonical_oa() -> None:
    canonical = expected_oa_capabilities()[1]
    source = build_fixed_synthetic_query_source(_model())
    detail = source.manifest.capability
    assert detail.capability_id == SYNTHETIC_DETAIL_CAPABILITY_ID
    assert detail.capability_id != canonical.capability_id
    assert canonical.input_schema["properties"] == {}
    assert detail.input_schema["required"] == ["business_key"]
    assert detail.input_schema["additionalProperties"] is False
    assert detail.output_schema == canonical.output_schema
    assert source.manifest.site.navigation_origins == (SYNTHETIC_ORIGIN,)


@pytest.mark.parametrize("arguments", [
    {}, {"business_key": ""}, {"business_key": "x" * 97}, {"business_key": 1},
    {"business_key": "key", "fixture_collection": "untrusted"},
    {"business_key": "https://example.invalid"},
])
def test_query_key_is_required_bounded_and_closed(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SyntheticDetailArguments.model_validate(arguments)


def test_query_accepts_a_legal_nonfixture_key_without_selecting_a_result() -> None:
    parsed = SyntheticDetailArguments(business_key="different_business_key")
    assert parsed.business_key == "different_business_key"
    assert parsed.model_dump() == {"business_key": "different_business_key"}


def test_projector_returns_actual_dynamic_collection_not_fixture_reference() -> None:
    source = build_fixed_synthetic_query_source(_model())
    observed = {
        "messages": [{
            "message_id": "different_message", "title": "Actual dynamic title",
            "content": "Actual dynamic content", "source_name": "Synthetic source",
            "occurred_at": "2026-10-03T01:02:03Z", "business_state": "read",
            "link": None, "mobile_link": None,
        }],
        "returned_count": 1, "is_complete": True,
    }
    result = source.projector({"collection": json.dumps(observed)})
    assert result == observed
    assert "Actual dynamic title" in json.dumps(result)


@pytest.mark.parametrize("query,code", [
    (False, "browser_fixed_seed_expected_input_contract_unapproved"),
    (True, "browser_fixed_seed_source_placement_unapproved"),
])
def test_local_installation_stays_off_without_approved_source_placement(
    query: bool, code: str,
) -> None:
    source = (build_fixed_synthetic_query_source(_model()) if query
              else build_fixed_synthetic_source(_model()))
    absent_dependencies = cast(BrowserVerticalDependencies, None)
    absent_provider = cast(LLMProviderPort, None)
    absent_parser = cast(StructuredOutputPort, None)
    assert install_fixed_synthetic_seed(
        absent_dependencies, source=source, llm_provider=absent_provider,
        structured_output=absent_parser, intent_model="fixture_model_v1",
    ) is None
    with pytest.raises(BrowserPublicationError, match=code):
        install_fixed_synthetic_seed(
            absent_dependencies, source=source, llm_provider=absent_provider,
            structured_output=absent_parser, intent_model="fixture_model_v1", enabled=True,
        )
