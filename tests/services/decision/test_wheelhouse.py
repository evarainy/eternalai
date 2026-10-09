"""Offline wheel and notice bundle contract; no dependency or model is imported."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from services.decision.wheelhouse import _LOCK, _inspect_bundle, _load_lock

_PACKAGES = (
    ("laya", "0.4.1"),
    ("torch", "2.14.1+cpu"),
    ("transformers", "5.19.0"),
    ("safetensors", "0.8.0"),
    ("huggingface-hub", "1.33.0"),
    ("numpy", "2.5.3"),
)


def _bundle(root: Path, *, notices: bool = True) -> Path:
    root.mkdir()
    entries = []
    locks = []
    for name, version in _PACKAGES:
        distribution = name.replace("-", "_")
        filename = f"{distribution}-{version}-py3-none-any.whl"
        body = f"synthetic wheel bytes for {name}".encode()
        (root / filename).write_bytes(body)
        wheel_sha = hashlib.sha256(body).hexdigest()
        source = "https://files.pythonhosted.org/packages/" + filename
        locks.append(
            "[[packages]]\n"
            f"name = {json.dumps(name)}\nversion = {json.dumps(version)}\n"
            f"wheels = [{{ url = {json.dumps(source)}, "
            f"size = {len(body)}, hashes = {{ sha256 = {json.dumps(wheel_sha)} }} }}]\n"
        )
        if notices:
            notice_path = f"notices/{distribution}.txt"
            notice_body = f"Synthetic notice for {name}; no actual license claim.\n".encode()
            (root / "notices").mkdir(exist_ok=True)
            (root / notice_path).write_bytes(notice_body)
            entries.append({
                "name": name,
                "version": version,
                "wheel_sha256": wheel_sha,
                "source_url": f"https://pypi.org/pypi/{name}/{version.split('+')[0]}/json",
                "license_id": "synthetic-test-only",
                "notice_path": notice_path,
                "notice_sha256": hashlib.sha256(notice_body).hexdigest(),
            })
    lock = root.parent / "synthetic-pylock.toml"
    lock.write_text(
        'lock-version = "1.0"\nrequires-python = ">=3.12"\n' + "\n".join(locks),
        encoding="utf-8",
    )
    if notices:
        (root / "notices.json").write_text(json.dumps({
            "schema_version": "decision_wheel_notices.v1",
            "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
            "packages": entries,
        }), encoding="utf-8")
    return lock


def test_committed_cpu_reference_has_exact_wheels_and_transitive_packages() -> None:
    wheels = _load_lock(_LOCK.read_bytes())
    assert len(wheels) == 35
    versions = {wheel.name: wheel.version for wheel in wheels}
    for name, version in _PACKAGES:
        assert versions[name] == version
    assert all(wheel.sha256 and wheel.filename.endswith(".whl") for wheel in wheels)


def test_license_source_metadata_covers_the_exact_reference_lock() -> None:
    source = _LOCK.with_name("license_sources.json")
    report = json.loads(source.read_bytes())
    wheels = _load_lock(_LOCK.read_bytes())
    assert report["lock_sha256"] == hashlib.sha256(_LOCK.read_bytes()).hexdigest()
    assert report["scope"] == "index_metadata_not_wheel_notice_or_legal_approval"
    assert {(item["name"], item["version"]) for item in report["packages"]} == {
        (wheel.name, wheel.version) for wheel in wheels
    }


def test_complete_synthetic_bundle_passes_only_exact_bytes_and_notices(tmp_path: Path) -> None:
    root = tmp_path / "wheelhouse"
    lock = _bundle(root)
    assert _inspect_bundle(root, lock).status == "READY"
    (root / "laya-0.4.1-py3-none-any.whl").write_bytes(b"changed")
    assert _inspect_bundle(root, lock).code == "wheel_mismatch"


def test_missing_notices_are_not_claimed_ready(tmp_path: Path) -> None:
    root = tmp_path / "wheelhouse"
    lock = _bundle(root, notices=False)
    result = _inspect_bundle(root, lock)
    assert (result.status, result.code) == ("WAITING_ENV", "notices_missing")


def test_notice_digest_mismatch_fails(tmp_path: Path) -> None:
    root = tmp_path / "wheelhouse"
    lock = _bundle(root)
    (root / "notices" / "laya.txt").write_text("changed", encoding="utf-8")
    assert _inspect_bundle(root, lock).code == "notice_mismatch"
