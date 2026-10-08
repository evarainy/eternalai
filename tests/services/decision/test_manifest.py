from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from services.decision.manifest import (
    ARBITER_REFERENCE,
    Artifact,
    PinnedCheckpoint,
    digest,
    strict_json,
)
from services.decision.protocol import (
    ChoiceRequest,
    ChoiceResponse,
    ProjectionPolicy,
    schema_digest,
)


def projection_policy() -> ProjectionPolicy:
    return ProjectionPolicy(
        allowed_constraints=("Open the pending synthetic item",),
        allowed_labels=("Open", "Close"),
        allowed_context=(),
        allowed_roles=("button",),
    )


def artifact_bundle(root: Path) -> PinnedCheckpoint:
    """Generated text fixtures only; no real weights/checkpoint inference evidence."""
    root.mkdir(parents=True, exist_ok=True)
    runtime = [{"package": "pydantic", "version": importlib.metadata.version("pydantic")}]
    calibration = dict(
        schema_version="decision_calibration.v1",
        checkpoint_revision="a" * 40,
        input_schema_digest=schema_digest(ChoiceRequest),
        output_schema_digest=schema_digest(ChoiceResponse),
        evaluation_set_digest="b" * 64,
        evidence_digest="c" * 64,
        minimum_confidence=0.9,
    )
    dependency_lock = dict(
        schema_version="decision_dependencies.v1",
        checkpoint_revision="a" * 40,
        python_version=platform.python_version(),
        runtime=runtime,
    )
    artifacts = []
    for role in (
        "weights",
        "tokenizer",
        "config",
        "model_code",
        "input_adapter",
        "calibration",
        "dependency_lock",
    ):
        content = (
            json.dumps(calibration)
            if role == "calibration"
            else json.dumps(dependency_lock)
            if role == "dependency_lock"
            else f"generated synthetic {role}, no actual model"
        )
        path = f"{role}.synthetic"
        data = content.encode()
        (root / path).write_bytes(data)
        artifacts.append(
            dict(
                path=path, role=role, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
            )
        )
    payload = dict(
        schema_version="decision_checkpoint.v1",
        repository="cklxx/laya-browser",
        revision="a" * 40,
        subfolder="",
        tokenizer_revision="a" * 40,
        tokenizer_path="tokenizer.synthetic",
        dtype="float32",
        deployment="fixture-pinned-v1",
        input_schema_digest=schema_digest(ChoiceRequest),
        output_schema_digest=schema_digest(ChoiceResponse),
        projection_policy_digest=projection_policy().digest,
        calibration=calibration,
        python_version=platform.python_version(),
        runtime=runtime,
        artifacts=artifacts,
        arbiter_reference=ARBITER_REFERENCE,
    )
    return seal_manifest(payload)


def seal_manifest(payload: dict[str, Any]) -> PinnedCheckpoint:
    payload["digest"] = digest("decision_checkpoint.v1", payload)
    return PinnedCheckpoint.model_validate_json(json.dumps(payload))


def test_generated_manifest_pins_all_roles_schemas_and_metadata(tmp_path: Path) -> None:
    manifest = artifact_bundle(tmp_path)
    assert len(manifest.artifacts) == 7
    assert manifest.calibration.input_schema_digest == schema_digest(ChoiceRequest)
    assert manifest.tokenizer_revision == manifest.revision
    assert manifest.arbiter_reference == ARBITER_REFERENCE


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/absolute",
        "C:/outside",
        "a\\file",
        "a//b",
        ".env",
        "private/.env.local",
        ".git/config",
        "_scratch/file",
    ],
)
def test_artifact_paths_fail_closed(path: str) -> None:
    with pytest.raises(ValidationError, match="artifact_path_invalid"):
        Artifact(path=path, role="weights", sha256="a" * 64, size_bytes=1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", "main"),
        ("tokenizer_revision", "latest"),
        ("dtype", "auto"),
        ("deployment", "model-latest"),
        ("deployment", "synthetic-decision-test-v1"),
        ("subfolder", "../secret"),
        ("tokenizer_path", "unknown"),
        ("artifacts", []),
    ],
)
def test_unknown_or_unpinned_inputs_rejected(tmp_path: Path, field: str, value: Any) -> None:
    data = artifact_bundle(tmp_path).model_dump(mode="json")
    data[field] = value
    with pytest.raises(ValidationError):
        seal_manifest(data)


def test_wrong_nested_calibration_and_self_digest_rejected(tmp_path: Path) -> None:
    manifest = artifact_bundle(tmp_path)
    data = manifest.model_dump(mode="json")
    data["calibration"]["checkpoint_revision"] = "b" * 40
    with pytest.raises(ValidationError, match="calibration_mismatch"):
        seal_manifest(data)
    with pytest.raises(ValidationError, match="manifest_digest_mismatch"):
        PinnedCheckpoint.model_validate_json(
            manifest.model_copy(update={"digest": "0" * 64}).model_dump_json()
        )


def test_strict_json_rejects_duplicate_keys_and_nonfinite() -> None:
    for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
        with pytest.raises(ValueError):
            strict_json(raw)


def test_inventory_is_nonlaunchable_and_keeps_unknown_revisions_unset() -> None:
    path = Path(__file__).resolve().parents[3] / "services/decision/checkpoint_inventory.json"
    data = json.loads(path.read_bytes())
    assert data["status"] == "WAITING_ENV"
    assert data["launchable"] is False
    assert data["preferred_repository"] == "cklxx/laya-browser"
    assert data["arbiter_reference"] == ARBITER_REFERENCE
    assert "revision" not in data
    with pytest.raises(ValidationError):
        PinnedCheckpoint.model_validate_json(path.read_bytes())
