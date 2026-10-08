"""Pinned operator registrations. A declaration is not artifact or model validation."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Final, Literal, Self

from pydantic import Field, model_validator

from app.browser_skill.models import Contract, Digest, Probability, SafeText

ARBITER_REFERENCE: Final = "bf71358774c85aeff2051ec7b1dcc4577a87706d"
Revision = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
PackageName = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")]
ExactVersion = Annotated[str, Field(pattern=r"^[0-9]+(?:\.[0-9A-Za-z]+)+(?:[+._-][0-9A-Za-z]+)*$")]


def digest(domain: str, payload: dict[str, object]) -> str:
    """Domain-separated canonical JSON; omit only this object's digest."""
    body = {key: value for key, value in payload.items() if key != "digest"}
    return hashlib.sha256(
        domain.encode("ascii")
        + b"\n"
        + json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def strict_json(raw: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise ValueError("decision_duplicate_json_key")
            result[key] = value
        return result

    def constant(value: str) -> object:
        raise ValueError("decision_nonfinite_json")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def relative_path(value: str, *, allow_root: bool = False) -> str:
    if value == "" and allow_root:
        return value
    parts = value.split("/")
    if not value or any(
        part in {"", ".", ".."}
        or part.startswith(".env")
        or part.lower() in {".git", "_scratch", "secrets"}
        for part in parts
    ):
        raise ValueError("decision_artifact_path_invalid")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise ValueError("decision_artifact_path_invalid")
    return value


class Artifact(Contract):
    path: SafeText
    role: Literal[
        "weights",
        "tokenizer",
        "config",
        "model_code",
        "input_adapter",
        "calibration",
        "dependency_lock",
    ]
    sha256: Digest
    size_bytes: Annotated[int, Field(gt=0, le=1_099_511_627_776)]

    @model_validator(mode="after")
    def safe_path(self) -> Self:
        relative_path(self.path)
        return self


class RuntimeRequirement(Contract):
    package: PackageName
    version: ExactVersion


class CalibrationEvidence(Contract):
    schema_version: Literal["decision_calibration.v1"] = "decision_calibration.v1"
    checkpoint_revision: Revision
    input_schema_digest: Digest
    output_schema_digest: Digest
    evaluation_set_digest: Digest
    evidence_digest: Digest
    minimum_confidence: Probability


class PinnedCheckpoint(Contract):
    schema_version: Literal["decision_checkpoint.v1"] = "decision_checkpoint.v1"
    repository: Literal["cklxx/laya-browser", "ichenney/laya-browser-v32b"]
    revision: Revision
    subfolder: str
    tokenizer_revision: Revision
    tokenizer_path: SafeText
    dtype: Literal["float32", "float16", "bfloat16"]
    deployment: SafeText
    input_schema_digest: Digest
    output_schema_digest: Digest
    projection_policy_digest: Digest
    calibration: CalibrationEvidence
    python_version: ExactVersion
    runtime: Annotated[tuple[RuntimeRequirement, ...], Field(min_length=1, max_length=128)]
    artifacts: Annotated[tuple[Artifact, ...], Field(min_length=7, max_length=4096)]
    arbiter_reference: Literal["bf71358774c85aeff2051ec7b1dcc4577a87706d"] = ARBITER_REFERENCE
    digest: Digest

    @model_validator(mode="after")
    def pinned(self) -> Self:
        relative_path(self.subfolder, allow_root=True)
        relative_path(self.tokenizer_path)
        if "latest" in self.deployment.lower() or self.deployment.startswith("synthetic-"):
            raise ValueError("decision_deployment_not_pinned")
        if len({artifact.path.casefold() for artifact in self.artifacts}) != len(self.artifacts):
            raise ValueError("decision_artifact_duplicate")
        required = {
            "weights",
            "tokenizer",
            "config",
            "model_code",
            "input_adapter",
            "calibration",
            "dependency_lock",
        }
        if {artifact.role for artifact in self.artifacts} != required:
            raise ValueError("decision_artifacts_incomplete")
        if self.tokenizer_path not in {a.path for a in self.artifacts if a.role == "tokenizer"}:
            raise ValueError("decision_tokenizer_unbound")
        packages = [re.sub(r"[-_.]+", "-", item.package) for item in self.runtime]
        if len(set(packages)) != len(packages):
            raise ValueError("decision_runtime_duplicate")
        if (
            self.calibration.checkpoint_revision,
            self.calibration.input_schema_digest,
            self.calibration.output_schema_digest,
        ) != (self.revision, self.input_schema_digest, self.output_schema_digest):
            raise ValueError("decision_calibration_mismatch")
        if digest(self.schema_version, self.model_dump(mode="json")) != self.digest:
            raise ValueError("decision_manifest_digest_mismatch")
        return self
