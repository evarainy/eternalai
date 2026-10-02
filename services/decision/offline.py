"""Read-only, exact-file offline preflight. Never imports or downloads model code."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Never, SupportsIndex

from app.browser_skill.models import Contract
from services.decision.manifest import (
    CalibrationEvidence,
    ExactVersion,
    PinnedCheckpoint,
    Revision,
    RuntimeRequirement,
    strict_json,
)


class DependencyLock(Contract):
    schema_version: Literal["decision_dependencies.v1"]
    checkpoint_revision: Revision
    python_version: ExactVersion
    runtime: tuple[RuntimeRequirement, ...]


_ISSUER = object()


class VerifiedArtifacts:
    """Process-local receipt minted only after file and environment checks.

    Proves an offline preflight at the inspection time, not loaded weights, A1
    parity, A2 isolation, A3 mixed batches, A4 cold-start egress, or A5 quality.
    Trusted loading must preserve these verified files through model load.
    """

    __slots__ = ("__manifest_digest",)

    def __init__(self, manifest_digest: str, issuer: object) -> None:
        if issuer is not _ISSUER:
            raise ValueError("decision_artifact_receipt_invalid")
        self.__manifest_digest = manifest_digest

    def matches(self, manifest: PinnedCheckpoint) -> bool:
        return self.__manifest_digest == manifest.digest

    def __repr__(self) -> str:
        return "<VerifiedArtifacts: process-local preflight>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("decision_receipt_serialization_forbidden")


@dataclass(frozen=True)
class OfflineResult:
    status: Literal["READY", "WAITING_ENV", "FAIL"]
    codes: tuple[str, ...]
    receipt: VerifiedArtifacts | None = None


def _exact_file(root: Path, relative: str) -> Path:
    candidate = root
    for part in relative.split("/"):
        candidate = candidate / part
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError("artifact_link_forbidden")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("artifact_path_escape")
    return resolved


def _permitted_absolute(path: Path) -> bool:
    return path.is_absolute() and not any(
        part.lower() in {".git", "_scratch", "secrets"} or part.lower().startswith(".env")
        for part in path.parts
    )


def _contains_link(path: Path) -> bool:
    return any(part.is_symlink() or part.is_junction() for part in (path, *path.parents))


def inspect_offline(manifest: PinnedCheckpoint, artifact_root: Path) -> OfflineResult:
    try:
        checked = PinnedCheckpoint.model_validate_json(manifest.model_dump_json())
    except (ValueError, TypeError):
        return OfflineResult("FAIL", ("manifest_invalid",))
    if not _permitted_absolute(artifact_root):
        return OfflineResult("FAIL", ("artifact_root_not_absolute",))
    if _contains_link(artifact_root):
        return OfflineResult("FAIL", ("artifact_root_link_forbidden",))
    root = artifact_root.resolve()
    if not root.is_dir():
        return OfflineResult("WAITING_ENV", ("artifact_root_missing",))
    if platform.python_version() != checked.python_version:
        return OfflineResult("FAIL", ("python_version_mismatch",))
    for requirement in checked.runtime:
        try:
            actual = importlib.metadata.version(requirement.package)
        except importlib.metadata.PackageNotFoundError:
            return OfflineResult("WAITING_ENV", ("runtime_dependency_missing",))
        if actual != requirement.version:
            return OfflineResult("FAIL", ("runtime_dependency_version_mismatch",))
    metadata: dict[str, list[bytes]] = {"calibration": [], "dependency_lock": []}
    for artifact in checked.artifacts:
        try:
            path = _exact_file(root, artifact.path)
            if not path.is_file():
                return OfflineResult("WAITING_ENV", ("artifact_missing",))
            if path.stat().st_size != artifact.size_bytes:
                return OfflineResult("FAIL", ("artifact_size_mismatch",))
            checksum = hashlib.sha256()
            count = 0
            body = bytearray()
            with path.open("rb") as stream:
                while block := stream.read(1_048_576):
                    checksum.update(block)
                    count += len(block)
                    if count > artifact.size_bytes:
                        return OfflineResult("FAIL", ("artifact_size_mismatch",))
                    if artifact.role in metadata:
                        if count > 1_048_576:
                            return OfflineResult("FAIL", ("artifact_metadata_budget",))
                        body.extend(block)
            if count != artifact.size_bytes or checksum.hexdigest() != artifact.sha256:
                return OfflineResult("FAIL", ("artifact_digest_mismatch",))
            if artifact.role in metadata:
                metadata[artifact.role].append(bytes(body))
        except ValueError:
            return OfflineResult("FAIL", ("artifact_path_invalid",))
        except OSError:
            return OfflineResult("WAITING_ENV", ("artifact_unreadable",))
    try:
        if len(metadata["calibration"]) != 1 or len(metadata["dependency_lock"]) != 1:
            raise ValueError("metadata_ambiguous")
        calibration_raw = metadata["calibration"][0]
        dependency_raw = metadata["dependency_lock"][0]
        strict_json(calibration_raw)
        strict_json(dependency_raw)
        calibration = CalibrationEvidence.model_validate_json(calibration_raw)
        dependencies = DependencyLock.model_validate_json(dependency_raw)
        if calibration != checked.calibration or (
            dependencies.checkpoint_revision,
            dependencies.python_version,
            dependencies.runtime,
        ) != (checked.revision, checked.python_version, checked.runtime):
            raise ValueError("metadata_mismatch")
    except (ValueError, TypeError):
        return OfflineResult("FAIL", ("artifact_metadata_mismatch",))
    return OfflineResult("READY", (), VerifiedArtifacts(checked.digest, _ISSUER))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if (
            not _permitted_absolute(args.manifest)
            or args.manifest.name.startswith(".env")
            or args.manifest.suffix != ".json"
            or _contains_link(args.manifest)
        ):
            raise ValueError("manifest_path_invalid")
        if args.manifest.stat().st_size > 1_048_576:
            raise ValueError("manifest_budget")
        raw = args.manifest.read_bytes()
        strict_json(raw)
        document = json.loads(raw)
        if isinstance(document, dict) and document.get("status") == "WAITING_ENV":
            result = OfflineResult("WAITING_ENV", ("checkpoint_not_frozen",))
        else:
            manifest = PinnedCheckpoint.model_validate_json(raw)
            result = inspect_offline(manifest, args.artifact_root)
    except OSError:
        result = OfflineResult("WAITING_ENV", ("manifest_unavailable",))
    except (ValueError, TypeError):
        result = OfflineResult("FAIL", ("manifest_invalid",))
    print(
        json.dumps(
            {
                "status": result.status,
                "codes": list(result.codes),
                "proof_scope": "offline_preflight_only",
            },
            separators=(",", ":"),
        )
    )
    return {"READY": 0, "WAITING_ENV": 2, "FAIL": 1}[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
