from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from services.decision.offline import VerifiedArtifacts, inspect_offline, main
from tests.services.decision.test_manifest import artifact_bundle, seal_manifest


def test_generated_artifact_preflight_is_real_hash_check_but_not_model_evidence(
    tmp_path: Path,
) -> None:
    manifest = artifact_bundle(tmp_path)
    result = inspect_offline(manifest, tmp_path)
    assert result.status == "READY"
    assert result.receipt is not None
    assert result.receipt.matches(manifest)
    assert "preflight" in repr(result.receipt)
    with pytest.raises(ValueError, match="receipt_invalid"):
        VerifiedArtifacts(manifest.digest, object())


@pytest.mark.parametrize(
    "case,code", [("size", "artifact_size_mismatch"), ("hash", "artifact_digest_mismatch")]
)
def test_actual_file_tampering_fails(tmp_path: Path, case: str, code: str) -> None:
    manifest = artifact_bundle(tmp_path)
    artifact = manifest.artifacts[0]
    (tmp_path / artifact.path).write_bytes(b"x" * (artifact.size_bytes if case == "hash" else 1))
    result = inspect_offline(manifest, tmp_path)
    assert result.status == "FAIL"
    assert result.codes == (code,)
    assert result.receipt is None


def test_missing_artifact_or_dependency_is_waiting_not_no_go(tmp_path: Path) -> None:
    manifest = artifact_bundle(tmp_path)
    payload = manifest.model_dump(mode="json")
    payload["artifacts"][0]["path"] = "missing.synthetic"
    result = inspect_offline(seal_manifest(payload), tmp_path)
    assert result.status == "WAITING_ENV"
    assert result.codes == ("artifact_missing",)
    payload = manifest.model_dump(mode="json")
    payload["runtime"] = [{"package": "synthetic-missing-model-dependency", "version": "1.0.0"}]
    result = inspect_offline(seal_manifest(payload), tmp_path)
    assert result.status == "WAITING_ENV"
    assert result.codes == ("runtime_dependency_missing",)


@pytest.mark.parametrize("part", ["calibration", "dependency_lock"])
def test_matching_hash_cannot_hide_wrong_checkpoint_metadata(tmp_path: Path, part: str) -> None:
    manifest = artifact_bundle(tmp_path)
    artifact = next(item for item in manifest.artifacts if item.role == part)
    path = tmp_path / artifact.path
    data = json.loads(path.read_bytes())
    data["checkpoint_revision"] = "b" * 40
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    payload = manifest.model_dump(mode="json")
    for entry in payload["artifacts"]:
        if entry["role"] == part:
            entry["size_bytes"] = len(raw)
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
    result = inspect_offline(seal_manifest(payload), tmp_path)
    assert result.status == "FAIL"
    assert result.codes == ("artifact_metadata_mismatch",)


def test_symlink_cannot_escape_exact_root(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    manifest = artifact_bundle(root)
    outside = tmp_path / "generated_outside.synthetic"
    outside.write_bytes((root / manifest.artifacts[0].path).read_bytes())
    link = root / "link.synthetic"
    link.symlink_to(outside)
    payload = manifest.model_dump(mode="json")
    payload["artifacts"][0]["path"] = "link.synthetic"
    result = inspect_offline(seal_manifest(payload), root)
    assert result.status == "FAIL"
    assert result.codes == ("artifact_path_invalid",)
    assert result.receipt is None


def test_relative_root_and_forged_model_copy_rejected(tmp_path: Path) -> None:
    manifest = artifact_bundle(tmp_path)
    result = inspect_offline(manifest, Path("relative"))
    assert result.status == "FAIL"
    assert result.codes == ("artifact_root_not_absolute",)
    forged = manifest.model_copy(update={"digest": "0" * 64})
    assert inspect_offline(forged, tmp_path).codes == ("manifest_invalid",)


def test_root_under_symlink_is_rejected_before_artifact_reads(tmp_path: Path) -> None:
    actual = tmp_path / "actual" / "bundle"
    manifest = artifact_bundle(actual)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "actual", target_is_directory=True)
    result = inspect_offline(manifest, alias / "bundle")
    assert result.status == "FAIL"
    assert result.codes == ("artifact_root_link_forbidden",)


def test_forbidden_manifest_path_is_rejected_without_reading(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    forbidden = tmp_path / "_scratch" / "oa" / "manifest.json"
    assert main(["--manifest", str(forbidden), "--artifact-root", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["codes"] == ["manifest_invalid"]


def test_cli_exit_codes_and_no_artifact_content_in_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = artifact_bundle(tmp_path / "artifacts")
    path = tmp_path / "manifest.json"
    path.write_text(manifest.model_dump_json(), encoding="utf-8")
    assert main(["--manifest", str(path), "--artifact-root", str(tmp_path / "artifacts")]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "READY", "codes": [], "proof_scope": "offline_preflight_only"}
    assert main(["--manifest", str(path), "--artifact-root", str(tmp_path / "missing")]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "WAITING_ENV"
    path.write_text('{"revision":"main"}', encoding="utf-8")
    assert main(["--manifest", str(path), "--artifact-root", str(tmp_path / "artifacts")]) == 1
    assert json.loads(capsys.readouterr().out)["codes"] == ["manifest_invalid"]
