"""Build a credential-free OA Contract Pack from one local HAR capture."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError  # noqa: E402

from app.infra.adapters.oa.contracts import (  # noqa: E402
    OAContractPackProfile,
    OAPendingWorkflowCollection,
    build_structural_fingerprint,
)

_PROFILE_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_MAX_HAR_BYTES = 32 * 1024 * 1024
_MAX_RECORDS = 10_000
_SYNTHETIC_TIMESTAMP = "2000-01-01T00:00:00+00:00"

_FORBIDDEN_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "setcookie",
        "token",
        "accesstoken",
        "refreshtoken",
        "session",
        "sessionid",
        "jsessionid",
        "ecologyjsessionid",
        "loginidweaver",
        "loginuuids",
        "password",
        "passphrase",
        "secret",
        "apikey",
        "loginid",
        "userid",
        "workcode",
        "employeeno",
    }
)
_SYNTHESIZED_SOURCE_KEYS = frozenset(
    {
        "workflowid",
        "title",
        "applicant",
        "currentstep",
        "approver",
        "createdat",
    }
)
_SENSITIVE_HEADER_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "setcookie",
        "xauthtoken",
        "xtoken",
    }
)
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(
        r"(?i)\b(?:authorization|cookie|set-cookie|token|sessionid|password)"
        r"\s*[:=]\s*\S+"
    ),
    re.compile(r"(?i)\b(?:ecology_jsessionid|loginidweaver|loginuuids)\b"),
    re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
)


class SanitizationError(RuntimeError):
    """A fail-closed sanitization condition with a non-sensitive message."""


def sanitize_har_to_contract_pack(
    *,
    input_har: Path,
    output_dir: Path,
    profile_version: str,
) -> None:
    """Create a Contract Pack atomically or leave the target absent."""

    if not _PROFILE_VERSION_PATTERN.fullmatch(profile_version):
        raise SanitizationError("invalid_profile_version")
    if output_dir.name != profile_version:
        raise SanitizationError("profile_output_name_mismatch")
    if output_dir.exists():
        raise SanitizationError("output_already_exists")
    if not output_dir.parent.is_dir():
        raise SanitizationError("output_parent_missing")

    har = _load_har(input_har)
    raw_payload = _select_pending_workflow_payload(har)
    sensitive_values = _collect_sensitive_values(har)
    sample = _normalize_sample(raw_payload)
    fingerprint = build_structural_fingerprint(sample)
    profile = {
        "profile_version": profile_version,
        "capability_id": "oa.list_pending_workflows",
        "source_kind": "sanitized_capture",
        "sanitizer_version": "1",
        "sample_file": "sample.json",
        "fingerprint_file": "fingerprint.json",
    }
    candidate_payloads = {
        "profile.json": profile,
        "sample.json": sample,
        "fingerprint.json": fingerprint,
    }

    temporary_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{profile_version}.",
            dir=output_dir.parent,
        )
    )
    try:
        for file_name, payload in candidate_payloads.items():
            _write_json(temporary_dir / file_name, payload)
        reparsed = {
            file_name: _load_json_file(temporary_dir / file_name)
            for file_name in candidate_payloads
        }
        _assert_sensitive_values_absent(sensitive_values, reparsed)
        _scan_forbidden_output(reparsed)
        _validate_candidate_pack(reparsed)
        os.replace(temporary_dir, output_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


def _load_har(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > _MAX_HAR_BYTES:
            raise SanitizationError("input_too_large")
    except OSError as exc:
        raise SanitizationError("input_unreadable") from exc
    payload = _load_json_file(path)
    if not isinstance(payload, dict):
        raise SanitizationError("har_root_invalid")
    return payload


def _load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SanitizationError("json_unreadable") from exc


def _select_pending_workflow_payload(har: Mapping[str, Any]) -> dict[str, Any]:
    log = har.get("log")
    if not isinstance(log, Mapping):
        raise SanitizationError("har_log_missing")
    entries = log.get("entries")
    if not isinstance(entries, list):
        raise SanitizationError("har_entries_missing")

    candidates: list[dict[str, Any]] = []
    for entry in entries:
        payload = _response_json(entry)
        if payload is not None and _is_pending_workflow_payload(payload):
            candidates.append(payload)
    if len(candidates) != 1:
        raise SanitizationError("pending_workflow_response_not_unique")
    return candidates[0]


def _response_json(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None
    response = entry.get("response")
    if not isinstance(response, Mapping):
        return None
    content = response.get("content")
    if not isinstance(content, Mapping):
        return None
    if content.get("encoding") not in (None, ""):
        raise SanitizationError("encoded_response_not_supported")
    text = content.get("text")
    if not isinstance(text, str):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _is_pending_workflow_payload(payload: Mapping[str, Any]) -> bool:
    data = payload.get("data")
    return isinstance(data, Mapping) and isinstance(data.get("records"), list)


def _normalize_sample(raw_payload: Mapping[str, Any]) -> dict[str, Any]:
    data = raw_payload.get("data")
    if not isinstance(data, Mapping):
        raise SanitizationError("response_data_invalid")
    records = data.get("records")
    if (
        not isinstance(records, list)
        or not records
        or len(records) > _MAX_RECORDS
    ):
        raise SanitizationError("response_records_invalid")

    workflows: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise SanitizationError("response_record_invalid")
        _require_string(record, "workflowId")
        _require_string(record, "title")
        status = _require_string(record, "status")
        _require_string(record, "applicant")
        _require_string(record, "currentStep")
        approver = _optional_string(record, "approver")
        created_at = _optional_string(record, "createdAt")
        expired = record.get("expired")
        if not isinstance(expired, bool):
            raise SanitizationError("response_expired_invalid")
        workflows.append(
            {
                "workflow_id": f"workflow-synthetic-{index:03d}",
                "title": f"workflow-title-synthetic-{index:03d}",
                "status": status,
                "applicant": f"applicant-synthetic-{index:03d}",
                "current_step": f"step-synthetic-{index:03d}",
                "approver": (
                    f"approver-synthetic-{index:03d}"
                    if approver is not None
                    else None
                ),
                "created_at": (
                    _SYNTHETIC_TIMESTAMP if created_at is not None else None
                ),
                "expired": expired,
            }
        )
    sample = {"workflows": workflows}
    try:
        OAPendingWorkflowCollection.model_validate(sample, strict=True)
    except ValidationError as exc:
        raise SanitizationError("normalized_sample_invalid") from exc
    return sample


def _require_string(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SanitizationError("response_required_string_invalid")
    return value


def _optional_string(record: Mapping[str, Any], key: str) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SanitizationError("response_optional_string_invalid")
    return value


def _collect_sensitive_values(har: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    _collect_sensitive_fields(har, values)
    log = har.get("log")
    if isinstance(log, Mapping):
        entries = log.get("entries")
        if isinstance(entries, list):
            for entry in entries:
                _collect_entry_credentials(entry, values)
    return {value for value in values if value}


def _collect_sensitive_fields(value: Any, output: set[str]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized_key = _normalize_key(str(key))
            if (
                normalized_key in _FORBIDDEN_KEYS
                or normalized_key in _SYNTHESIZED_SOURCE_KEYS
            ):
                output.update(_flatten_strings(child))
            _collect_sensitive_fields(child, output)
    elif isinstance(value, list):
        for item in value:
            _collect_sensitive_fields(item, output)


def _collect_entry_credentials(entry: Any, output: set[str]) -> None:
    if not isinstance(entry, Mapping):
        return
    for side_name in ("request", "response"):
        side = entry.get(side_name)
        if not isinstance(side, Mapping):
            continue
        _collect_header_values(side.get("headers"), output)
        _collect_cookie_values(side.get("cookies"), output)


def _collect_header_values(headers: Any, output: set[str]) -> None:
    if not isinstance(headers, list):
        return
    for header in headers:
        if not isinstance(header, Mapping):
            continue
        name = header.get("name")
        value = header.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        normalized_name = _normalize_key(name)
        if (
            normalized_name in _SENSITIVE_HEADER_KEYS
            or normalized_name in _FORBIDDEN_KEYS
        ):
            output.add(value)
            output.update(_credential_components(value))


def _collect_cookie_values(cookies: Any, output: set[str]) -> None:
    if not isinstance(cookies, list):
        return
    for cookie in cookies:
        if not isinstance(cookie, Mapping):
            continue
        name = cookie.get("name")
        value = cookie.get("value")
        if isinstance(name, str):
            output.add(name)
        if isinstance(value, str):
            output.add(value)


def _credential_components(value: str) -> set[str]:
    components: set[str] = set()
    for part in re.split(r"[;,]", value):
        stripped = part.strip()
        if "=" in stripped:
            _name, component = stripped.split("=", 1)
            if component:
                components.add(component.strip())
        elif " " in stripped:
            _scheme, component = stripped.split(" ", 1)
            if component:
                components.add(component.strip())
    return components


def _flatten_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Mapping):
        return tuple(
            string
            for child in value.values()
            for string in _flatten_strings(child)
        )
    if isinstance(value, list):
        return tuple(string for child in value for string in _flatten_strings(child))
    return ()


def _assert_sensitive_values_absent(
    sensitive_values: set[str],
    candidate_payloads: Mapping[str, Any],
) -> None:
    rendered = "\n".join(
        json.dumps(payload, ensure_ascii=False, sort_keys=True)
        for payload in candidate_payloads.values()
    )
    if any(value in rendered for value in sensitive_values):
        raise SanitizationError("raw_sensitive_value_survived")


def _scan_forbidden_output(candidate_payloads: Mapping[str, Any]) -> None:
    for payload in candidate_payloads.values():
        _scan_payload(payload)


def _scan_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _normalize_key(str(key)) in _FORBIDDEN_KEYS:
                raise SanitizationError("forbidden_output_key")
            _scan_payload(child)
        return
    if isinstance(value, list):
        for item in value:
            _scan_payload(item)
        return
    if isinstance(value, str) and any(
        pattern.search(value) for pattern in _FORBIDDEN_VALUE_PATTERNS
    ):
        raise SanitizationError("forbidden_output_value")


def _validate_candidate_pack(candidate_payloads: Mapping[str, Any]) -> None:
    try:
        OAContractPackProfile.model_validate(
            candidate_payloads["profile.json"],
            strict=True,
        )
        OAPendingWorkflowCollection.model_validate(
            candidate_payloads["sample.json"],
            strict=True,
        )
    except (KeyError, ValidationError) as exc:
        raise SanitizationError("candidate_contract_invalid") from exc
    if candidate_payloads["fingerprint.json"] != build_structural_fingerprint(
        candidate_payloads["sample.json"]
    ):
        raise SanitizationError("candidate_fingerprint_invalid")


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.casefold())


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline OA HAR to sanitized Contract Pack converter.",
    )
    parser.add_argument("--input-har", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--profile-version", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        sanitize_har_to_contract_pack(
            input_har=args.input_har,
            output_dir=args.output_dir,
            profile_version=args.profile_version,
        )
    except SanitizationError as exc:
        print(f"sanitization failed: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("sanitization failed: unexpected_error", file=sys.stderr)
        return 3
    print("sanitized Contract Pack created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
