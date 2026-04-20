from services.api.app.schemas.knowledge_source import (
    GetKnowledgeSourceResponse,
    RegisterKnowledgeSourceRequest,
    RegisterKnowledgeSourceResponse,
)
from services.api.tests.contract_helpers import get_contract_operation_block, get_resource_block


def test_register_knowledge_source_request_has_typed_source_type() -> None:
    request = RegisterKnowledgeSourceRequest(source_type="url", source_locator="https://example.com")
    assert request.source_type == "url"
    assert RegisterKnowledgeSourceRequest.model_json_schema()["properties"]["source_type"]["enum"] == ["url", "file", "wiki"]


def test_register_knowledge_source_response_defaults_to_queued() -> None:
    response = RegisterKnowledgeSourceResponse(
        knowledge_source_id="ks_123",
        task_id="task_123",
        source_type="url",
        source_locator="https://example.com",
        created_at="2026-04-20T12:00:00Z",
    )
    assert response.status == "queued"


def test_get_knowledge_source_response_exposes_status_boundary() -> None:
    response = GetKnowledgeSourceResponse(
        knowledge_source_id="ks_123",
        source_type="url",
        source_locator="https://example.com",
        status="ready",
        created_at="2026-04-20T12:00:00Z",
    )
    assert response.status == "ready"


def test_knowledge_source_contract_and_resource_spec_reference_schema_models() -> None:
    register_block = get_contract_operation_block("registerKnowledgeSource")
    detail_block = get_contract_operation_block("getKnowledgeSource")
    resource_block = get_resource_block("knowledge_source")

    assert "schema_ref: services.api.app.schemas.knowledge_source.RegisterKnowledgeSourceRequest" in register_block
    assert "schema_ref: services.api.app.schemas.knowledge_source.RegisterKnowledgeSourceResponse" in register_block
    assert "schema_ref: services.api.app.schemas.knowledge_source.GetKnowledgeSourceResponse" in detail_block
    assert "services.api.app.schemas.knowledge_source.GetKnowledgeSourceResponse" in resource_block
