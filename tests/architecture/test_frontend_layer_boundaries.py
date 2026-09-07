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


_FLAT_LEGACY_DIRECTORIES: tuple[str, ...] = ("components", "pages")

# Grandfathered inventory of every .ts/.tsx file that already existed in the two
# flat legacy directories (web/src/components/, web/src/pages/) at the time this
# guard was introduced (P2-FE-DIR-GUARD-001, 2026-09-08). Paths are POSIX-style,
# relative to web/src/. This is a literal frozenset, not a derived/rglob-based
# exception list: renaming or deleting one of these files without also editing
# this set makes the guard fail (see the existence assertion in the test below),
# forcing the removal to happen in the same commit rather than silently drifting.
_GRANDFATHERED_FLAT_FILES: frozenset[str] = frozenset(
    {
        "components/ConfirmCard.tsx",
        "components/OACredentialBindingCard.tsx",
        "components/RecordsList.tsx",
        "components/RoleSelector.tsx",
        "components/__tests__/OACredentialBindingCard.test.tsx",
        "components/__tests__/RoleSelector.test.tsx",
        "pages/ChatPage.tsx",
        "pages/HealthPage.tsx",
        "pages/LoginPage.tsx",
        "pages/WorkObjectsPage.tsx",
        "pages/chatGreeting.ts",
        "pages/loginNavigation.ts",
        "pages/__tests__/ChatPage.test.tsx",
        "pages/__tests__/HealthPage.test.tsx",
        "pages/__tests__/LoginPage.test.tsx",
        "pages/__tests__/WorkObjectsPage.test.tsx",
        "pages/__tests__/loginNavigation.test.ts",
        "pages/admin/BindingsPage.tsx",
        "pages/admin/RegistryPage.tsx",
        "pages/admin/TasksPage.tsx",
        "pages/admin/registryValidation.ts",
        "pages/admin/__tests__/BindingsPage.test.tsx",
        "pages/admin/__tests__/RegistryPage.test.tsx",
        "pages/admin/__tests__/TasksPage.test.tsx",
    }
)


def _find_new_flat_directory_files(web_src: Path) -> list[str]:
    """Return grandfather-list violations in the flat legacy directories.

    Decision anchor: DECISIONS.md 2026-08-27 "八、前端目录、状态与 API 边界"
    bans new *page code* from landing in the flat web/src/components/ /
    web/src/pages/ directories (new features must go to web/src/features/<name>/,
    old code stays in place). A file-name heuristic such as "basename ends with
    Page.tsx" was considered and rejected: it has an explicit bypass (a new
    routed page named e.g. Dashboard.tsx would slip past undetected), and a
    guard that can be dodged by naming is worse than no guard — it manufactures
    false confidence.

    This function instead implements a **strictly stricter** rule than the
    decision's literal wording: any .ts/.tsx file appearing anywhere under
    these two flat directories that is not in `_GRANDFATHERED_FLAT_FILES` is a
    violation, full stop — no attempt is made to classify "is this a page" at
    all. Three reasons, in order of weight:

    1. Fully decidable: no heuristic, no naming-based bypass surface.
    2. The same decision paragraph already commits to the migration direction
       "old code stays put, new feature work goes to features/<name>/" — these
       two directories are the stranded legacy pool that direction is
       retiring, so freezing their membership matches where the codebase is
       headed anyway.
    3. The grandfather list is itself the escape hatch: a genuinely justified
       new file in one of these directories requires the change author to add
       a literal line to `_GRANDFATHERED_FLAT_FILES`, which shows up in the
       diff for review — that is exactly how a grandfather clause is meant to
       work.

    Because this bans *any* new file rather than only new *page* code, it is
    stricter than the decision's literal text (which only names page code).
    That gap is a deliberate trade for decidability, not a misreading of the
    decision; if GOV-SYNC judges it overly strict (e.g. wants to allow new
    non-page shared components into web/src/components/), it can be narrowed
    in a follow-up without needing to touch DECISIONS.md's substance.
    """
    violations: list[str] = []
    for directory_name in _FLAT_LEGACY_DIRECTORIES:
        directory = web_src / directory_name
        for source_file in _typescript_files(directory):
            relative = source_file.relative_to(web_src).as_posix()
            if relative not in _GRANDFATHERED_FLAT_FILES:
                violations.append(relative)
    return sorted(violations)


def test_new_files_may_not_land_in_flat_components_or_pages_directories() -> None:
    for entry in _GRANDFATHERED_FLAT_FILES:
        assert (WEB_SRC / entry).is_file(), (
            f"grandfathered entry {entry!r} no longer exists on disk; remove it "
            "from _GRANDFATHERED_FLAT_FILES in the same commit that moved/deleted it"
        )
    assert _find_new_flat_directory_files(WEB_SRC) == []


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


def test_flat_directory_guard_flags_ungrandfathered_new_file_regardless_of_name(
    tmp_path: Path,
) -> None:
    """Synthetic proof the rule is "any new file", not a page-name heuristic.

    Uses a file name that would defeat a `*Page.tsx`-suffix heuristic
    (`Dashboard.tsx`, no "Page" suffix) to demonstrate the guard still catches
    it, and also proves a grandfathered entry is exempt while a same-directory
    sibling that is not on the list is not.
    """
    web_src = tmp_path / "web" / "src"
    components = web_src / "components"
    pages = web_src / "pages"
    components.mkdir(parents=True)
    pages.mkdir(parents=True)

    (components / "Existing.tsx").write_text("export {};\n", encoding="utf-8")
    (pages / "Dashboard.tsx").write_text("export {};\n", encoding="utf-8")

    original = _GRANDFATHERED_FLAT_FILES
    try:
        globals()["_GRANDFATHERED_FLAT_FILES"] = frozenset({"components/Existing.tsx"})
        violations = _find_new_flat_directory_files(web_src)
    finally:
        globals()["_GRANDFATHERED_FLAT_FILES"] = original

    assert violations == ["pages/Dashboard.tsx"]
