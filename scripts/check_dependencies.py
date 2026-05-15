#!/usr/bin/env python3
"""Validate repository dependency manifests against the Phase 0 allowlist."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "docs" / "dev" / "dependency_policy.md"
ALLOWLIST_HEADING = "## Dependency Allowlist"
REQUIRED_FIELDS = (
    "ecosystem",
    "package",
    "allowed_version_range",
    "dependency_group",
    "mirror_status",
    "approval_source",
)
EXCLUDED_DIRS = {".git", ".venv", "node_modules", "__pycache__"}
PYTHON_DEPENDENCY_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?")


@dataclass(frozen=True)
class AllowlistEntry:
    ecosystem: str
    package: str
    allowed_version_range: str
    dependency_group: str
    mirror_status: str
    approval_source: str


@dataclass(frozen=True)
class Dependency:
    ecosystem: str
    package: str
    source: Path


class DependencyCheckError(Exception):
    """Raised when dependency policy or manifest validation fails."""


def normalize_python_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def normalize_npm_name(name: str) -> str:
    return name.lower()


def strip_markdown_cell(value: str) -> str:
    return value.strip().strip("`").strip()


def split_markdown_row(line: str) -> list[str]:
    return [strip_markdown_cell(cell) for cell in line.strip().strip("|").split("|")]


def is_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def allowlist_section(policy_text: str, policy_path: Path) -> list[str]:
    lines = policy_text.splitlines()
    start_index = None
    for index, line in enumerate(lines):
        if line.strip() == ALLOWLIST_HEADING:
            start_index = index + 1
            break

    if start_index is None:
        raise DependencyCheckError(
            f"Missing {ALLOWLIST_HEADING!r} in deterministic allowlist source {policy_path}"
        )

    section: list[str] = []
    for line in lines[start_index:]:
        stripped = line.strip()
        if stripped.startswith("## ") and stripped != ALLOWLIST_HEADING:
            break
        section.append(line)
    return section


def parse_allowlist(policy_path: Path) -> dict[tuple[str, str], AllowlistEntry]:
    if not policy_path.exists():
        raise DependencyCheckError(f"Deterministic allowlist source not found: {policy_path}")

    section = allowlist_section(policy_path.read_text(encoding="utf-8"), policy_path)
    table_rows = [split_markdown_row(line) for line in section if line.strip().startswith("|")]
    table_rows = [row for row in table_rows if row and not is_separator_row(row)]

    if not table_rows:
        raise DependencyCheckError(
            f"No markdown allowlist table found in deterministic allowlist source {policy_path}"
        )

    header = [cell.lower() for cell in table_rows[0]]
    missing_fields = [field for field in REQUIRED_FIELDS if field not in header]
    if missing_fields:
        raise DependencyCheckError(
            f"Dependency allowlist in {policy_path} is missing required fields: "
            + ", ".join(missing_fields)
        )

    field_positions = {field: header.index(field) for field in REQUIRED_FIELDS}
    entries: dict[tuple[str, str], AllowlistEntry] = {}
    for row_number, row in enumerate(table_rows[1:], start=2):
        values: dict[str, str] = {}
        for field, position in field_positions.items():
            if position >= len(row) or not row[position].strip():
                raise DependencyCheckError(
                    f"Allowlist row {row_number} in {policy_path} has empty required field {field}"
                )
            values[field] = row[position].strip()

        ecosystem = values["ecosystem"].lower()
        package = (
            normalize_python_name(values["package"])
            if ecosystem == "python"
            else normalize_npm_name(values["package"])
        )
        key = (ecosystem, package)
        entry = AllowlistEntry(
            ecosystem=ecosystem,
            package=package,
            allowed_version_range=values["allowed_version_range"],
            dependency_group=values["dependency_group"],
            mirror_status=values["mirror_status"],
            approval_source=values["approval_source"],
        )
        if key in entries and entries[key] != entry:
            raise DependencyCheckError(
                f"Conflicting allowlist entries for {ecosystem}:{package} in {policy_path}"
            )
        entries[key] = entry

    if not entries:
        raise DependencyCheckError(f"Dependency allowlist in {policy_path} has no entries")
    return entries


def path_is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_DIRS for part in path.parts)


def discover_manifests(repo_root: Path) -> list[Path]:
    patterns = ("pyproject.toml", "requirements*.txt", "package.json")
    manifests: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in repo_root.rglob(pattern):
            if path_is_excluded(path.relative_to(repo_root)):
                continue
            resolved = path.resolve()
            if resolved not in seen:
                manifests.append(path)
                seen.add(resolved)
    return sorted(manifests)


def dependency_name_from_python_spec(spec: str, source: Path) -> str | None:
    cleaned = spec.split("#", 1)[0].strip()
    if not cleaned or cleaned.startswith(("-", "http://", "https://", "git+")):
        return None
    if " @ " in cleaned:
        cleaned = cleaned.split(" @ ", 1)[0].strip()
    match = PYTHON_DEPENDENCY_RE.match(cleaned)
    if not match:
        raise DependencyCheckError(f"Cannot parse Python dependency {spec!r} in {source}")
    return normalize_python_name(match.group(1))


def parse_requirements(path: Path) -> list[Dependency]:
    dependencies: list[Dependency] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = dependency_name_from_python_spec(line, path)
        if name:
            dependencies.append(Dependency("python", name, path))
    return dependencies


def parse_pyproject(path: Path) -> list[Dependency]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    dependencies: list[Dependency] = []

    project = data.get("project", {})
    for spec in project.get("dependencies", []):
        name = dependency_name_from_python_spec(str(spec), path)
        if name:
            dependencies.append(Dependency("python", name, path))

    optional_dependencies = project.get("optional-dependencies", {})
    for specs in optional_dependencies.values():
        for spec in specs:
            name = dependency_name_from_python_spec(str(spec), path)
            if name:
                dependencies.append(Dependency("python", name, path))

    dependency_groups = data.get("dependency-groups", {})
    for specs in dependency_groups.values():
        if isinstance(specs, list):
            for spec in specs:
                name = dependency_name_from_python_spec(str(spec), path)
                if name:
                    dependencies.append(Dependency("python", name, path))

    return dependencies


def parse_package_json(path: Path) -> list[Dependency]:
    data = json.loads(path.read_text(encoding="utf-8"))
    dependency_fields = (
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    )
    dependencies: list[Dependency] = []
    for field in dependency_fields:
        for package in data.get(field, {}):
            dependencies.append(Dependency("npm", normalize_npm_name(package), path))
    return dependencies


def parse_manifest(path: Path) -> list[Dependency]:
    name = path.name.lower()
    if name == "pyproject.toml":
        return parse_pyproject(path)
    if name.startswith("requirements") and path.suffix.lower() == ".txt":
        return parse_requirements(path)
    if name == "package.json":
        return parse_package_json(path)
    raise DependencyCheckError(f"Unsupported dependency manifest: {path}")


def collect_dependencies(manifests: list[Path]) -> list[Dependency]:
    dependencies: list[Dependency] = []
    for manifest in manifests:
        if not manifest.exists():
            continue
        dependencies.extend(parse_manifest(manifest))
    return dependencies


def relative_display(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def run_check(policy_path: Path, manifests: list[Path], repo_root: Path) -> int:
    allowlist = parse_allowlist(policy_path)
    dependencies = collect_dependencies(manifests)
    undeclared = [
        dependency
        for dependency in dependencies
        if (dependency.ecosystem, dependency.package) not in allowlist
    ]

    policy_display = relative_display(policy_path, repo_root)
    if undeclared:
        print("Dependency check failed.", file=sys.stderr)
        print(f"Deterministic allowlist source: {policy_display}", file=sys.stderr)
        for dependency in undeclared:
            source_display = relative_display(dependency.source, repo_root)
            print(
                f"Undeclared dependency: {dependency.ecosystem}:{dependency.package} "
                f"in {source_display}",
                file=sys.stderr,
            )
        return 1

    print("Dependency check passed.")
    print(f"Deterministic allowlist source: {policy_display}")
    print(f"Manifests scanned: {len(manifests)}")
    print(f"Dependencies checked: {len(dependencies)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check dependency manifests against docs/dev/dependency_policy.md."
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
        help="Policy file containing the deterministic Dependency Allowlist section.",
    )
    parser.add_argument(
        "--manifest",
        action="append",
        type=Path,
        default=[],
        help="Additional manifest to scan. May be specified more than once.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = REPO_ROOT
    discovered = discover_manifests(repo_root)
    extra_manifests = [path.resolve() for path in args.manifest]
    manifests = discovered + [path for path in extra_manifests if path not in discovered]

    try:
        return run_check(args.policy.resolve(), manifests, repo_root)
    except (DependencyCheckError, OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        print("Dependency check failed.", file=sys.stderr)
        print(
            f"Deterministic allowlist source: {relative_display(args.policy.resolve(), repo_root)}",
            file=sys.stderr,
        )
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
