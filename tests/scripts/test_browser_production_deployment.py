"""Offline transfer bytes and mounted-secret startup boundaries."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

from scripts import run_browser_production as launcher
from scripts.verify_browser_production_bundle import BundleInvalid, verify_bundle

_BASE_ROLES = (
    "image_archive", "uv_lock", "browser_asset_manifest", "license_inventory",
    "site_registration", "operator_factory_file", "main_runtime_manifest",
)


def _bundle(root: Path, *, decision: bool = False) -> Path:
    roles = (*_BASE_ROLES, *((
        "decision_checkpoint_archive", "decision_checkpoint_manifest",
        "decision_wheelhouse_archive", "decision_wheel_lock", "decision_notices",
    ) if decision else ()))
    artifacts = []
    for index, role in enumerate(roles):
        name = f"artifact-{index}.bin"
        content = f"material-{role}".encode()
        (root / name).write_bytes(content)
        artifacts.append({
            "role": role, "path": name, "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        })
    manifest = root / "bundle.json"
    manifest.write_text(json.dumps({
        "schema": "browser.production.bundle.v1",
        "source_commit": "a" * 40,
        "activation": "decision" if decision else "deterministic",
        "artifacts": artifacts,
    }), encoding="utf-8")
    return manifest


def _verify(root: Path, manifest: Path) -> int:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    uv_lock = next(item for item in data["artifacts"] if item["role"] == "uv_lock")
    return verify_bundle(
        root, manifest, expected_source_commit="a" * 40,
        expected_uv_lock_sha256=uv_lock["sha256"],
    )


def test_bundle_requires_exact_bytes_and_rejects_path_escape(tmp_path: Path) -> None:
    manifest = _bundle(tmp_path)
    assert _verify(tmp_path, manifest) == 7
    (tmp_path / "artifact-0.bin").write_bytes(b"changed")
    with pytest.raises(BundleInvalid):
        _verify(tmp_path, manifest)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["artifacts"][0]["path"] = "../outside.bin"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(BundleInvalid):
        _verify(tmp_path, manifest)


def test_decision_bundle_requires_its_five_extra_artifacts(tmp_path: Path) -> None:
    manifest = _bundle(tmp_path, decision=True)
    assert _verify(tmp_path, manifest) == 12
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["artifacts"] = data["artifacts"][:-1]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(BundleInvalid):
        _verify(tmp_path, manifest)


def test_secret_loader_rejects_multiline_and_logs_no_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "database-url").write_text("sensitive\nextra", encoding="utf-8")
    with pytest.raises(ValueError):
        launcher._read_secret(tmp_path / "database-url")
    monkeypatch.setattr(launcher, "_SECRET_ROOT", tmp_path)
    assert launcher.main(["--enable"]) == 2
    captured = capsys.readouterr()
    assert captured.err == "browser_production_unavailable\n"
    assert "sensitive" not in captured.out + captured.err


def test_launcher_supplies_only_the_six_mounted_values_before_server(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}
    for index, (key, filename) in enumerate(launcher._SECRET_FILES.items()):
        (tmp_path / filename).write_text(f"v{index}\n", encoding="utf-8")
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(launcher, "_SECRET_ROOT", tmp_path)
    server = ModuleType("scripts.run_browser_registered_server")

    def serve(argv: list[str] | None = None) -> int:
        received["argv"] = argv
        received["values"] = {key: os.environ[key] for key in launcher._SECRET_FILES}
        return 0

    server.main = serve  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, server.__name__, server)
    assert launcher.main(["--enable", "--host", "0.0.0.0"]) == 0
    assert received == {
        "argv": ["--enable", "--host", "0.0.0.0"],
        "values": {key: f"v{index}" for index, key in enumerate(launcher._SECRET_FILES)},
    }
    assert capsys.readouterr() == ("", "")


def test_launcher_rejects_environment_secret_override(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", "operator-env-value")
    assert launcher.main(["--enable"]) == 2
    assert capsys.readouterr().err == "browser_production_unavailable\n"
