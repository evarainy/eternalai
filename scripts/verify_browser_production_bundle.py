"""Offline, exact-file check of a trusted browser production transfer bundle.

This does not inspect image layers, approve licenses, or start any component.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*\Z")
_BASE_ROLES = frozenset({
    "image_archive", "uv_lock", "browser_asset_manifest", "license_inventory",
    "site_registration", "operator_factory_file", "main_runtime_manifest",
})
_DECISION_ROLES = frozenset({
    "decision_checkpoint_archive", "decision_checkpoint_manifest",
    "decision_wheelhouse_archive", "decision_wheel_lock", "decision_notices",
})


class BundleInvalid(ValueError):
    """A fixed diagnostic; never include a path or manifest value."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise BundleInvalid
    return value


def _relative_file(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value.isascii() or len(value) > 512:
        raise BundleInvalid
    parts = value.split("/")
    if any(not _PART.fullmatch(part) or part in {".", ".."} for part in parts):
        raise BundleInvalid
    candidate = root
    for part in parts:
        candidate /= part
        if candidate.is_symlink() or (hasattr(candidate, "is_junction")
                                      and candidate.is_junction()):
            raise BundleInvalid
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise BundleInvalid
    return resolved


def verify_bundle(
    root: Path, manifest_path: Path, *,
    expected_source_commit: str, expected_uv_lock_sha256: str,
) -> int:
    """Verify a local bundle without importing app code or contacting a service."""
    if (not root.is_absolute() or not root.is_dir() or root.is_symlink()
            or not manifest_path.is_file() or manifest_path.is_symlink()
            or manifest_path.stat().st_size > 1_048_576
            or type(expected_source_commit) is not str
            or not _COMMIT.fullmatch(expected_source_commit)
            or type(expected_uv_lock_sha256) is not str
            or not _DIGEST.fullmatch(expected_uv_lock_sha256)):
        raise BundleInvalid
    resolved_root = root.resolve(strict=True)
    if not manifest_path.resolve(strict=True).is_relative_to(resolved_root):
        raise BundleInvalid
    try:
        bundle = json.loads(
            manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise BundleInvalid from error
    if (type(bundle) is not dict
            or set(bundle) != {"schema", "source_commit", "activation", "artifacts"}
            or bundle["schema"] != "browser.production.bundle.v1"
            or not isinstance(bundle["source_commit"], str)
            or not _COMMIT.fullmatch(bundle["source_commit"])
            or bundle["source_commit"] != expected_source_commit
            or type(bundle["activation"]) is not str
            or bundle["activation"] not in {"deterministic", "decision"}
            or type(bundle["artifacts"]) is not list
            or not 7 <= len(bundle["artifacts"]) <= 4096):
        raise BundleInvalid
    roles: set[str] = set()
    names: set[str] = set()
    for item in bundle["artifacts"]:
        if (type(item) is not dict
                or set(item) != {"role", "path", "sha256", "size_bytes"}
                or type(item["role"]) is not str
                or item["role"] not in _BASE_ROLES | _DECISION_ROLES
                or type(item["path"]) is not str
                or not isinstance(item["sha256"], str)
                or not _DIGEST.fullmatch(item["sha256"])
                or type(item["size_bytes"]) is not int
                or not 0 < item["size_bytes"] <= 1_099_511_627_776
                or item["path"] in names):
            raise BundleInvalid
        path = _relative_file(resolved_root, item["path"])
        if (item["role"] == "uv_lock"
                and item["sha256"] != expected_uv_lock_sha256):
            raise BundleInvalid
        names.add(item["path"])
        roles.add(item["role"])
        if path.stat().st_size != item["size_bytes"]:
            raise BundleInvalid
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1_048_576), b""):
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise BundleInvalid
    required = _BASE_ROLES | (_DECISION_ROLES if bundle["activation"] == "decision"
                              else frozenset())
    if not required <= roles or (bundle["activation"] == "deterministic"
                                and roles & _DECISION_ROLES):
        raise BundleInvalid
    # One image and lock must identify one candidate, not several alternatives.
    for unique in required - {"operator_factory_file"}:
        if sum(item["role"] == unique for item in bundle["artifacts"]) != 1:
            raise BundleInvalid
    return len(bundle["artifacts"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-uv-lock-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        count = verify_bundle(
            args.bundle_root, args.manifest,
            expected_source_commit=args.expected_source_commit,
            expected_uv_lock_sha256=args.expected_uv_lock_sha256,
        )
    except (BundleInvalid, OSError):
        print("browser_production_bundle_invalid", file=sys.stderr)
        return 2
    print(f"browser_production_bundle_verified files={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
