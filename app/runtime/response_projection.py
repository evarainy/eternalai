"""Compatibility imports for the shared ResponseEnvelope projection boundary."""

from app.infra.sdui.response_projection import (
    project_response_data,
    schema_has_credential_property,
)
from app.ports.response_projection_contract import (
    ProjectionContractSnapshot,
    canonical_schema_digest,
    canonical_schema_json,
)

__all__ = (
    "ProjectionContractSnapshot",
    "canonical_schema_digest",
    "canonical_schema_json",
    "project_response_data",
    "schema_has_credential_property",
)
