"""Offline transfer bytes and mounted-secret startup boundaries."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path, PurePosixPath
from types import ModuleType

import pytest

from scripts import run_browser_production as launcher
from scripts.verify_browser_production_bundle import BundleInvalid, verify_bundle

_BASE_ROLES = (
    "image_archive", "uv_lock", "browser_asset_manifest", "license_inventory",
    "site_registration", "operator_factory_file", "main_runtime_manifest",
)
_ROOT = Path(__file__).resolve().parents[2]
_DEPLOYMENT = _ROOT / "infra/docker/browser-runtime"
_ENTRY_SOURCES = (
    "scripts/run_browser_production.py", "scripts/run_browser_registered_server.py",
    "app/main.py",  # The API role imports this only after selecting its role.
)


def _source_inventory(document: str) -> set[str]:
    lines = document.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("!") and line.endswith("/"):
            assert lines[index + 1] == line[1:] + "*"
    entries = {
        line[1:] for line in lines
        if line.startswith("!") and not line.endswith("/")
    }
    assert lines[1] == "**"
    assert all(not any(character in entry for character in "*?[") for entry in entries)
    return entries


def _copied_sources(dockerfile: str, inventory: set[str]) -> set[str]:
    copied: set[str] = set()
    for line in dockerfile.splitlines():
        if not line.startswith("COPY "):
            continue
        *sources, destination = shlex.split(line)[1:]
        for source in sources:
            candidates = (
                {entry for entry in inventory if entry.startswith(source)}
                if source.endswith("/") else {source} & inventory
            )
            for entry in candidates:
                suffix = entry[len(source):] if source.endswith("/") else Path(entry).name
                target = str(PurePosixPath(destination) / suffix) if destination.endswith("/") \
                    else str(PurePosixPath(destination))
                assert target == entry, (entry, target)
                copied.add(entry)
    return copied


def _import_time_closure(root: Path, entries: tuple[str, ...]) -> set[str]:
    """Parse code only: no application import, model, browser, DB or secret read.

    Function-local imports are not import-time dependencies. Historical synthetic
    entry methods and operator factory modules need their separate path checks.
    """
    pending = list(entries)
    visited: set[str] = set()
    while pending:
        relative = pending.pop()
        if relative in visited:
            continue
        visited.add(relative)
        source = root / relative
        assert source.is_file() and not source.is_symlink()
        tree = ast.parse(source.read_text(encoding="utf-8"))
        nodes: list[ast.AST] = [tree]
        while nodes:
            node = nodes.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                package = list(PurePosixPath(relative).parent.parts)
                prefix = package[:len(package) - node.level + 1] if node.level else []
                module = ".".join([*prefix, *node.module.split(".")]) \
                    if node.module else ".".join(prefix)
                modules = [module, *(module + "." + alias.name for alias in node.names)]
            for module in modules:
                if module.split(".")[0] not in {"app", "services", "scripts"}:
                    continue
                path = module.replace(".", "/")
                for candidate in (path + ".py", path + "/__init__.py"):
                    if (root / candidate).is_file():
                        pending.append(candidate)
                        for parent in PurePosixPath(candidate).parents:
                            initializer = str(parent / "__init__.py")
                            if initializer != "__init__.py" and (root / initializer).is_file():
                                pending.append(initializer)
            nodes.extend(ast.iter_child_nodes(node))
    return visited


def test_production_image_contains_registered_entry_import_time_closure() -> None:
    inventory = _source_inventory(
        (_DEPLOYMENT / "Dockerfile.production.dockerignore").read_text(encoding="utf-8"),
    )
    copied = _copied_sources(
        (_DEPLOYMENT / "Dockerfile.production").read_text(encoding="utf-8"), inventory,
    )
    required = _import_time_closure(_ROOT, _ENTRY_SOURCES)
    assert not required - copied
    assert {entry for entry in copied if entry.startswith("services/")} == {
        "services/decision/__init__.py", "services/decision/manifest.py",
    }
    assert all((_ROOT / entry).is_file() for entry in inventory)
    assert "app/infra/browser/synthetic_configuration.py" not in copied
    assert "app/infra/browser/openrouter_jev.py" not in copied


def test_missing_decision_copy_or_allowlist_breaks_source_closure() -> None:
    document = (_DEPLOYMENT / "Dockerfile.production").read_text(encoding="utf-8")
    inventory = _source_inventory(
        (_DEPLOYMENT / "Dockerfile.production.dockerignore").read_text(encoding="utf-8"),
    )
    required = _import_time_closure(_ROOT, _ENTRY_SOURCES)
    missing_copy = re.sub(r"^COPY services/decision/.*\n", "", document, flags=re.MULTILINE)
    assert "services/decision/manifest.py" in required - _copied_sources(missing_copy, inventory)
    missing_allowlist = inventory - {"services/decision/manifest.py"}
    assert "services/decision/manifest.py" in required - _copied_sources(
        document, missing_allowlist,
    )


def test_directory_exception_cannot_allow_unlisted_build_material() -> None:
    document = (_DEPLOYMENT / "Dockerfile.production.dockerignore").read_text(encoding="utf-8")
    with pytest.raises(AssertionError):
        _source_inventory(document.replace("services/decision/*\n", ""))
    with pytest.raises(AssertionError):
        _source_inventory(document + "!services/decision/**\n")


def _service_blocks(document: str) -> dict[str, str]:
    """Extract named service blocks for text contracts; not a YAML parser."""
    services = document.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    pattern = r"^  ([a-z-]+):\n(.*?)(?=^  [a-z-]+:\n|\Z)"
    return dict(re.findall(pattern, services, flags=re.MULTILINE | re.DOTALL))


def _role_contract(document: str) -> bool:
    blocks = _service_blocks(document)
    if set(blocks) != {"browser-api", "browser-worker"}:
        return False
    return (
        "      - --role\n      - api\n" in blocks["browser-api"]
        and "      - --role\n      - worker\n" in blocks["browser-worker"]
        and "--host" not in blocks["browser-worker"]
        and "expose:" not in blocks["browser-worker"]
        and "ports:" not in blocks["browser-worker"]
        and "        read_only: false" in blocks["browser-worker"]
        and "      - *browser-artifacts" in blocks["browser-api"]
    )


def test_formal_roles_use_shared_spool_with_api_read_only() -> None:
    document = (_DEPLOYMENT / "compose.production.yaml").read_text(encoding="utf-8")
    assert _role_contract(document)
    artifact_mount = document.split("x-browser-artifacts: &browser-artifacts\n", 1)[1] \
        .split("\nservices:\n", 1)[0]
    assert "  read_only: true\n" in artifact_mount
    assert "  target: /var/lib/eternalai/browser-artifacts\n" in artifact_mount
    blocks = _service_blocks(document)
    for block in blocks.values():
        assert "    <<: *runtime\n" in block
        assert "      - *operator-bundle\n" in block
    assert "pull_policy: never" in document
    assert "build:" not in document
    assert "BROWSER_DECISION_" not in document


def test_collapsed_or_worker_api_role_is_rejected_by_deployment_contract() -> None:
    document = (_DEPLOYMENT / "compose.production.yaml").read_text(encoding="utf-8")
    assert not _role_contract(document.replace("      - worker\n", "      - api\n"))
    collapsed = document.split("\n  browser-worker:\n", 1)[0]
    assert not _role_contract(collapsed)


def test_decision_overlay_covers_both_roles_with_real_three_value_contract() -> None:
    overlay = (_DEPLOYMENT / "compose.production-decision.yaml").read_text(encoding="utf-8")
    blocks = _service_blocks(overlay)
    assert set(blocks) == {"browser-api", "browser-worker"}
    assert all("environment: *decision-environment" in block for block in blocks.values())
    keys = set(re.findall(r"^  (BROWSER_DECISION_[A-Z_]+):", overlay, flags=re.MULTILINE))
    assert keys == {
        "BROWSER_DECISION_ORIGIN", "BROWSER_DECISION_REQUEST_MODEL",
        "BROWSER_DECISION_DEPLOYMENT_MODEL",
    }


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


@pytest.mark.parametrize("role", ["api", "worker"])
def test_launcher_supplies_only_the_six_mounted_values_before_server(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
    role: str,
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
    arguments = ["--enable", "--role", role]
    if role == "api":
        arguments.extend(["--host", "0.0.0.0"])
    assert launcher.main(arguments) == 0
    assert received == {
        "argv": arguments,
        "values": {key: f"v{index}" for index, key in enumerate(launcher._SECRET_FILES)},
    }
    assert capsys.readouterr() == ("", "")


def test_launcher_rejects_environment_secret_override(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", "operator-env-value")
    assert launcher.main(["--enable"]) == 2
    assert capsys.readouterr().err == "browser_production_unavailable\n"
