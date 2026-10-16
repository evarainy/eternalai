"""Offline integrity check for the one Linux CPU reference wheel bundle.

This runs before installation and imports no model dependency. It proves exact
wheel and notice bytes only; it does not approve a checkpoint or target host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

_LOCK = Path(__file__).with_name("pylock.linux_cpu.toml")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"[0-9]+(?:[.][0-9A-Za-z]+)*(?:[+._-][0-9A-Za-z]+)*\Z")
_PACKAGE = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*\Z")
_FORBIDDEN = {".git", "_scratch", "secrets"}


@dataclass(frozen=True)
class Wheel:
    name: str
    version: str
    filename: str
    sha256: str
    size_bytes: int | None


@dataclass(frozen=True)
class BundleResult:
    status: Literal["READY", "WAITING_ENV", "FAIL"]
    code: str
    lock_sha256: str | None = None


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _safe_root(root: Path) -> bool:
    return root.is_absolute() and not any(
        part.lower() in _FORBIDDEN or part.lower().startswith(".env")
        or _is_link(parent)
        for parent in (root, *root.parents)
        for part in (parent.name,)
    )


def _file(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative:
        raise ValueError("bundle_path_invalid")
    parts = relative.split("/")
    if any(
        part in {"", ".", ".."} or part.lower() in _FORBIDDEN
        or part.lower().startswith(".env")
        or re.fullmatch(r"[A-Za-z0-9_.+-]+", part) is None
        for part in parts
    ):
        raise ValueError("bundle_path_invalid")
    candidate = root
    for part in parts:
        candidate /= part
        if _is_link(candidate):
            raise ValueError("bundle_link_forbidden")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("bundle_path_escape")
    return resolved


def _load_lock(raw: bytes, *, reference: bool = True) -> tuple[Wheel, ...]:
    document = tomllib.loads(raw.decode("utf-8"))
    if (
        document.get("lock-version") != "1.0"
        or not isinstance(document.get("requires-python"), str)
    ):
        raise ValueError("wheel_lock_invalid")
    if reference and document["requires-python"] != ">=3.12":
        raise ValueError("wheel_lock_target_mismatch")
    packages = document.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("wheel_lock_invalid")
    seen_names: set[str] = set()
    seen_files: set[str] = set()
    wheels: list[Wheel] = []
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("wheel_lock_invalid")
        name, version = package.get("name"), package.get("version")
        if (
            not isinstance(name, str) or _PACKAGE.fullmatch(name) is None
            or not isinstance(version, str) or _VERSION.fullmatch(version) is None
            or _normalized(name) in seen_names
            or "sdist" in package or "vcs" in package or "directory" in package
        ):
            raise ValueError("wheel_lock_invalid")
        entries = package.get("wheels")
        if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
            raise ValueError("wheel_lock_invalid")
        entry = entries[0]
        address = entry.get("url")
        if not isinstance(address, str):
            raise ValueError("wheel_lock_invalid")
        url = urlparse(address)
        if (
            url.scheme != "https"
            or url.hostname not in {"files.pythonhosted.org", "download-r2.pytorch.org"}
            or url.username or url.password or url.query or url.fragment
        ):
            raise ValueError("wheel_lock_source_invalid")
        filename = unquote(url.path.rsplit("/", 1)[-1])
        if (
            not filename.endswith(".whl")
            or filename.count("/") or filename.count("\\")
            or filename.casefold() in seen_files
        ):
            raise ValueError("wheel_lock_invalid")
        fields = filename[:-4].split("-")
        if (
            len(fields) not in {5, 6}
            or _normalized(fields[0]) != _normalized(name)
            or fields[1] != version
        ):
            raise ValueError("wheel_lock_filename_mismatch")
        hashes = entry.get("hashes")
        sha256 = hashes.get("sha256") if isinstance(hashes, dict) else None
        size = entry.get("size")
        if (
            not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None
            or (size is not None and (type(size) is not int or size <= 0))
        ):
            raise ValueError("wheel_lock_invalid")
        seen_names.add(_normalized(name))
        seen_files.add(filename.casefold())
        wheels.append(Wheel(_normalized(name), version, filename, sha256, size))
    required = {"laya", "torch", "transformers", "safetensors", "huggingface-hub", "numpy"}
    if reference and (
        not required <= seen_names
        or next(w.version for w in wheels if w.name == "torch") != "2.14.1+cpu"
    ):
        raise ValueError("wheel_lock_target_mismatch")
    return tuple(wheels)


def _strict_json(raw: bytes) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                raise ValueError("notices_duplicate_key")
            value[key] = item
        return value

    return json.loads(raw, object_pairs_hook=pairs)


def _sha256(path: Path, *, limit: int | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1_048_576):
            size += len(chunk)
            if limit is not None and size > limit:
                raise ValueError("bundle_file_budget")
            digest.update(chunk)
    return digest.hexdigest(), size


def _inspect_bundle(bundle_root: Path, lock_path: Path) -> BundleResult:
    if not _safe_root(bundle_root) or not _safe_root(lock_path):
        return BundleResult("FAIL", "bundle_root_invalid")
    if not bundle_root.is_dir():
        return BundleResult("WAITING_ENV", "bundle_root_missing")
    try:
        lock_raw = lock_path.read_bytes()
        wheels = _load_lock(lock_raw)
        lock_digest = hashlib.sha256(lock_raw).hexdigest()
    except OSError:
        return BundleResult("WAITING_ENV", "wheel_lock_missing")
    except (ValueError, UnicodeError, TypeError, tomllib.TOMLDecodeError):
        return BundleResult("FAIL", "wheel_lock_invalid")
    expected = {wheel.filename.casefold() for wheel in wheels}
    try:
        observed = {
            path.name.casefold() for path in bundle_root.iterdir()
            if path.suffix.casefold() == ".whl"
        }
    except OSError:
        return BundleResult("WAITING_ENV", "bundle_unreadable", lock_digest)
    if observed != expected:
        if any(not (bundle_root / wheel.filename).is_file() for wheel in wheels):
            return BundleResult("WAITING_ENV", "wheel_missing", lock_digest)
        return BundleResult("FAIL", "wheel_unlisted", lock_digest)
    try:
        for wheel in wheels:
            path = _file(bundle_root, wheel.filename)
            actual_hash, actual_size = _sha256(path)
            if actual_hash != wheel.sha256 or (
                wheel.size_bytes is not None and actual_size != wheel.size_bytes
            ):
                return BundleResult("FAIL", "wheel_mismatch", lock_digest)
        notices_path = _file(bundle_root, "notices.json")
        if not notices_path.is_file():
            return BundleResult("WAITING_ENV", "notices_missing", lock_digest)
        if notices_path.stat().st_size > 1_048_576:
            raise ValueError("notices_budget")
        notices = _strict_json(notices_path.read_bytes())
        if (
            not isinstance(notices, dict)
            or notices.get("schema_version") != "decision_wheel_notices.v1"
            or notices.get("lock_sha256") != lock_digest
        ):
            raise ValueError("notices_invalid")
        entries = notices.get("packages")
        if not isinstance(entries, list) or len(entries) != len(wheels):
            raise ValueError("notices_invalid")
        by_name = {wheel.name: wheel for wheel in wheels}
        covered: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("notices_invalid")
            name, version = entry.get("name"), entry.get("version")
            if not isinstance(name, str) or name not in by_name or name in covered:
                raise ValueError("notices_invalid")
            wheel = by_name[name]
            source = entry.get("source_url")
            notice = entry.get("notice_path")
            notice_hash = entry.get("notice_sha256")
            license_id = entry.get("license_id")
            if (
                version != wheel.version or entry.get("wheel_sha256") != wheel.sha256
                or not isinstance(source, str)
                or source != f"https://pypi.org/pypi/{name}/{version.split('+')[0]}/json"
                or not isinstance(notice, str) or not notice.startswith("notices/")
                or not isinstance(notice_hash, str) or _SHA256.fullmatch(notice_hash) is None
                or not isinstance(license_id, str) or not license_id.strip()
            ):
                raise ValueError("notices_invalid")
            notice_file = _file(bundle_root, notice)
            if not notice_file.is_file():
                return BundleResult("WAITING_ENV", "notice_file_missing", lock_digest)
            actual_hash, actual_size = _sha256(notice_file, limit=1_048_576)
            if actual_size == 0 or actual_hash != notice_hash:
                return BundleResult("FAIL", "notice_mismatch", lock_digest)
            covered.add(name)
        if covered != set(by_name):
            raise ValueError("notices_invalid")
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return BundleResult("FAIL", "bundle_metadata_invalid", lock_digest)
    except OSError:
        return BundleResult("WAITING_ENV", "bundle_unreadable", lock_digest)
    return BundleResult("READY", "bundle_verified", lock_digest)


def inspect_reference_bundle(bundle_root: Path) -> BundleResult:
    return _inspect_bundle(bundle_root, _LOCK)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", required=True, type=Path)
    result = inspect_reference_bundle(parser.parse_args(argv).bundle_root)
    print(json.dumps(
        {"status": result.status, "code": result.code, "lock_sha256": result.lock_sha256},
        separators=(",", ":"),
    ))
    return {"READY": 0, "WAITING_ENV": 2, "FAIL": 1}[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
