"""Architecture guards for the decided frontend contracts/shared/features layers."""

from __future__ import annotations

import re
from pathlib import Path

from app.ports import trace as trace_port
from app.ports.page_context import (
    PAGE_CONTEXT_TIMESTAMP_MAX_LENGTH,
    PAGE_CONTEXT_TIMESTAMP_PATTERN,
)

WEB_SRC = Path(__file__).resolve().parents[2] / "web" / "src"
_IMPORT_RE = re.compile(
    r"^\s*(?:import|export)\s+(?:type\s+)?(?:[^'\"]*?\sfrom\s*)?['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)


def _typescript_files(root: Path) -> list[Path]:
    return sorted((*root.rglob("*.ts"), *root.rglob("*.tsx"))) if root.is_dir() else []


def _imports(source_file: Path) -> tuple[str, ...]:
    return tuple(_IMPORT_RE.findall(source_file.read_text(encoding="utf-8")))


def _resolved_layer(
    source_file: Path,
    imported: str,
    web_src: Path,
) -> tuple[str, ...] | None:
    if not imported.startswith("."):
        return None
    resolved = (source_file.parent / imported).resolve()
    try:
        return resolved.relative_to(web_src.resolve()).parts
    except ValueError:
        return None


def _find_frontend_layer_violations(web_src: Path) -> list[str]:
    violations: list[str] = []
    contracts = web_src / "contracts"
    for source_file in _typescript_files(contracts):
        for imported in _imports(source_file):
            if (
                imported == "react"
                or imported.startswith("react/")
                or imported == "antd"
                or imported.startswith("antd/")
                or imported.startswith("@ant-design/")
            ):
                violations.append(
                    f"contracts_rendering_dependency:{source_file.name}:{imported}"
                )

    shared = web_src / "shared"
    for source_file in _typescript_files(shared):
        for imported in _imports(source_file):
            layer = _resolved_layer(source_file, imported, web_src)
            if layer is not None and layer[:1] == ("features",):
                violations.append(
                    f"shared_feature_dependency:{source_file.name}:{imported}"
                )

    features = web_src / "features"
    for source_file in _typescript_files(features):
        relative = source_file.relative_to(features)
        if len(relative.parts) < 2:
            continue
        source_feature = relative.parts[0]
        for imported in _imports(source_file):
            layer = _resolved_layer(source_file, imported, web_src)
            if (
                layer is not None
                and len(layer) >= 2
                and layer[0] == "features"
                and layer[1] != source_feature
            ):
                violations.append(
                    f"cross_feature_dependency:{source_file.name}:{imported}"
                )
    return violations


def test_frontend_layers_follow_decided_dependency_boundaries() -> None:
    assert (WEB_SRC / "contracts" / "pageContext.ts").is_file()
    assert _find_frontend_layer_violations(WEB_SRC) == []


def test_page_context_timestamp_contract_is_identical_across_languages() -> None:
    source = (WEB_SRC / "contracts" / "pageContext.ts").read_text(encoding="utf-8")
    pattern_match = re.search(
        r"PAGE_CONTEXT_TIMESTAMP_PATTERN\s*=\s*String\.raw`([^`]*)`",
        source,
    )
    length_match = re.search(
        r"PAGE_CONTEXT_TIMESTAMP_MAX_LENGTH\s*=\s*(\d+)",
        source,
    )

    assert pattern_match is not None
    assert pattern_match.group(1) == PAGE_CONTEXT_TIMESTAMP_PATTERN
    assert length_match is not None
    assert int(length_match.group(1)) == PAGE_CONTEXT_TIMESTAMP_MAX_LENGTH


def test_page_context_sensitive_value_rules_match_backend_sanitizer() -> None:
    source = (WEB_SRC / "contracts" / "pageContext.ts").read_text(encoding="utf-8")
    patterns_match = re.search(
        r"PAGE_CONTEXT_SENSITIVE_VALUE_PATTERNS\s*=\s*\[(.*?)\]\s+as const",
        source,
        re.DOTALL,
    )

    assert patterns_match is not None
    frontend_patterns = tuple(
        re.findall(r"String\.raw`([^`]*)`", patterns_match.group(1))
    )
    backend_patterns = tuple(
        pattern.pattern
        for pattern in getattr(trace_port, "_CREDENTIAL_VALUE_PATTERNS")
    )
    assert frontend_patterns == backend_patterns


def test_frontend_layer_guard_detects_each_forbidden_direction(tmp_path: Path) -> None:
    web_src = tmp_path / "web" / "src"
    contracts = web_src / "contracts"
    shared = web_src / "shared"
    feature_a = web_src / "features" / "a"
    feature_b = web_src / "features" / "b"
    for directory in (contracts, shared, feature_a, feature_b):
        directory.mkdir(parents=True, exist_ok=True)

    (contracts / "bad.ts").write_text(
        "import type { ReactNode } from 'react';\n",
        encoding="utf-8",
    )
    (shared / "bad.ts").write_text(
        "import { featureA } from '../features/a/value';\n",
        encoding="utf-8",
    )
    (feature_a / "bad.ts").write_text(
        "import { featureB } from '../b/value';\n",
        encoding="utf-8",
    )
    (feature_a / "value.ts").write_text("export const featureA = 1;\n", encoding="utf-8")
    (feature_b / "value.ts").write_text("export const featureB = 2;\n", encoding="utf-8")

    violations = _find_frontend_layer_violations(web_src)

    assert len(violations) == 3
    assert any(item.startswith("contracts_rendering_dependency:") for item in violations)
    assert any(item.startswith("shared_feature_dependency:") for item in violations)
    assert any(item.startswith("cross_feature_dependency:") for item in violations)
