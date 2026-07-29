"""Build a credential-free OA Contract Pack from selected local HAR responses."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
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
_MAX_JSON_CONTAINER_BYTES = 1 * 1024 * 1024
_MAX_RECORDS = 10_000
_MAX_EMBEDDED_JSON_DEPTH = 8
_SYNTHETIC_TIMESTAMP = "2000-01-01T00:00:00+00:00"
_ALLOWED_RAW_PENDING_STATUSES = frozenset({"pending"})

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
    entry_indices: Sequence[int] | None = None,
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
    raw_payloads = _select_pending_workflow_payloads(
        har,
        entry_indices=entry_indices,
    )
    sensitive_values = _collect_sensitive_values(har)
    for raw_payload in raw_payloads:
        _collect_sensitive_fields(raw_payload, sensitive_values)
        _assert_response_payload_has_no_forbidden_keys(raw_payload)
    sample = _normalize_sample(raw_payloads)
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
    _assert_sensitive_values_absent(sensitive_values, candidate_payloads)
    _scan_forbidden_output(candidate_payloads)
    _validate_candidate_pack(candidate_payloads)

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
        cleanup_failed = False
        try:
            shutil.rmtree(temporary_dir)
        except OSError:
            cleanup_failed = True
    else:
        return

    if cleanup_failed:
        raise SanitizationError("temporary_cleanup_failed")
    raise SanitizationError("contract_pack_publish_failed")


def _load_har(path: Path) -> dict[str, Any]:
    stat_failed = False
    try:
        if path.stat().st_size > _MAX_HAR_BYTES:
            raise SanitizationError("input_too_large")
    except OSError:
        stat_failed = True
    if stat_failed:
        raise SanitizationError("input_unreadable")
    payload = _load_json_file(path)
    if not isinstance(payload, dict):
        raise SanitizationError("har_root_invalid")
    return payload


def _load_json_file(path: Path) -> Any:
    payload: Any = None
    load_failed = False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        load_failed = True
    if load_failed:
        raise SanitizationError("json_unreadable")
    return payload


def _select_pending_workflow_payloads(
    har: Mapping[str, Any],
    *,
    entry_indices: Sequence[int] | None,
) -> list[Mapping[str, Any]]:
    log = har.get("log")
    if not isinstance(log, Mapping):
        raise SanitizationError("har_log_missing")
    entries = log.get("entries")
    if not isinstance(entries, list):
        raise SanitizationError("har_entries_missing")

    if entry_indices is None:
        candidates: list[Mapping[str, Any]] = []
        for entry in entries:
            payload = _response_json(entry)
            if payload is not None and _is_pending_workflow_payload(payload):
                candidates.append(payload)
        if len(candidates) != 1:
            raise SanitizationError("pending_workflow_response_not_unique")
        return candidates

    if not entry_indices:
        raise SanitizationError("entry_index_missing")
    selected_payloads: list[Mapping[str, Any]] = []
    seen_indices: set[int] = set()
    for entry_index in entry_indices:
        if not isinstance(entry_index, int) or isinstance(entry_index, bool):
            raise SanitizationError("entry_index_invalid")
        if entry_index < 0 or entry_index >= len(entries):
            raise SanitizationError("entry_index_out_of_range")
        if entry_index in seen_indices:
            raise SanitizationError("entry_index_duplicate")
        seen_indices.add(entry_index)
        payload = _response_json(entries[entry_index], selected=True)
        if payload is None or not _is_pending_workflow_payload(payload):
            raise SanitizationError("selected_entry_not_pending_workflow_response")
        selected_payloads.append(payload)
    return selected_payloads


def _response_json(
    entry: Any,
    *,
    selected: bool = False,
) -> Mapping[str, Any] | None:
    if not isinstance(entry, Mapping):
        if selected:
            raise SanitizationError("selected_entry_invalid")
        return None
    response = entry.get("response")
    if not isinstance(response, Mapping):
        if selected:
            raise SanitizationError("selected_entry_invalid")
        return None
    content = response.get("content")
    if not isinstance(content, Mapping):
        if selected:
            raise SanitizationError("selected_entry_invalid")
        return None
    if content.get("encoding") not in (None, ""):
        raise SanitizationError("encoded_response_not_supported")
    text = content.get("text")
    if not isinstance(text, str):
        if selected:
            raise SanitizationError("selected_entry_invalid")
        return None
    try:
        payload = _bounded_json_loads(text)
    except json.JSONDecodeError:
        payload = None
        response_not_json = True
    else:
        response_not_json = False
    if response_not_json:
        if selected:
            raise SanitizationError("selected_entry_response_not_json")
        return None
    if not isinstance(payload, Mapping):
        if selected:
            raise SanitizationError("selected_entry_not_pending_workflow_response")
        return None
    return payload


def _is_pending_workflow_payload(payload: Mapping[str, Any]) -> bool:
    data = payload.get("data")
    return isinstance(data, Mapping) and isinstance(data.get("records"), list)


def _normalize_sample(
    raw_payloads: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    workflows: list[dict[str, Any]] = []
    for raw_payload in raw_payloads:
        data = raw_payload.get("data")
        if not isinstance(data, Mapping):
            raise SanitizationError("response_data_invalid")
        records = data.get("records")
        if not isinstance(records, list):
            raise SanitizationError("response_records_invalid")
        if len(workflows) + len(records) > _MAX_RECORDS:
            raise SanitizationError("response_records_invalid")

        for record in records:
            if not isinstance(record, Mapping):
                raise SanitizationError("response_record_invalid")
            _require_string(record, "workflowId")
            _require_string(record, "title")
            raw_status = _require_string(record, "status")
            if raw_status not in _ALLOWED_RAW_PENDING_STATUSES:
                raise SanitizationError("response_status_invalid")
            _require_string(record, "applicant")
            _require_string(record, "currentStep")
            approver = _optional_string(record, "approver")
            created_at = _optional_string(record, "createdAt")
            expired = record.get("expired")
            if not isinstance(expired, bool):
                raise SanitizationError("response_expired_invalid")
            index = len(workflows) + 1
            workflows.append(
                {
                    "workflow_id": f"workflow-synthetic-{index:03d}",
                    "title": f"workflow-title-synthetic-{index:03d}",
                    "status": "pending",
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
    sample_invalid = False
    try:
        OAPendingWorkflowCollection.model_validate(sample, strict=True)
    except ValidationError:
        sample_invalid = True
    if sample_invalid:
        raise SanitizationError("normalized_sample_invalid")
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
                _assert_entry_response_encoding_supported(entry)
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
    elif isinstance(value, str):
        decoded = _decode_json_container(value)
        if decoded is not None:
            _collect_sensitive_fields(decoded, output)


def _assert_response_payload_has_no_forbidden_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _normalize_key(str(key)) in _FORBIDDEN_KEYS:
                raise SanitizationError("forbidden_response_key")
            _assert_response_payload_has_no_forbidden_keys(child)
    elif isinstance(value, list):
        for item in value:
            _assert_response_payload_has_no_forbidden_keys(item)
    elif isinstance(value, str):
        decoded = _decode_json_container(value)
        if decoded is not None:
            _assert_response_payload_has_no_forbidden_keys(decoded)


def _decode_json_container(value: str) -> Mapping[str, Any] | list[Any] | None:
    candidate: Any = value
    for _depth in range(_MAX_EMBEDDED_JSON_DEPTH):
        if not isinstance(candidate, str):
            break
        stripped = candidate.strip()
        if not stripped or stripped[0] not in {'"', "{", "["}:
            return None
        try:
            candidate = _bounded_json_loads(stripped)
        except json.JSONDecodeError:
            return None
    if isinstance(candidate, (Mapping, list)):
        return candidate
    if isinstance(candidate, str):
        stripped = candidate.strip()
        if stripped and stripped[0] in {'"', "{", "["}:
            raise SanitizationError("embedded_json_depth_exceeded")
    return None


def _bounded_json_loads(value: str) -> Any:
    if len(value.encode("utf-8")) > _MAX_JSON_CONTAINER_BYTES:
        raise SanitizationError("json_container_too_large")
    return json.loads(value)


def _collect_entry_credentials(entry: Any, output: set[str]) -> None:
    if not isinstance(entry, Mapping):
        return
    for side_name in ("request", "response"):
        side = entry.get(side_name)
        if not isinstance(side, Mapping):
            continue
        _collect_header_values(side.get("headers"), output)
        _collect_cookie_values(side.get("cookies"), output)


def _assert_entry_response_encoding_supported(entry: Any) -> None:
    if not isinstance(entry, Mapping):
        return
    response = entry.get("response")
    if not isinstance(response, Mapping):
        return
    content = response.get("content")
    if (
        isinstance(content, Mapping)
        and content.get("encoding") not in (None, "")
    ):
        raise SanitizationError("encoded_response_not_supported")


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
    candidate_invalid = False
    try:
        OAContractPackProfile.model_validate(
            candidate_payloads["profile.json"],
            strict=True,
        )
        OAPendingWorkflowCollection.model_validate(
            candidate_payloads["sample.json"],
            strict=True,
        )
    except (KeyError, ValidationError):
        candidate_invalid = True
    if candidate_invalid:
        raise SanitizationError("candidate_contract_invalid")
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
    parser.add_argument(
        "--entry-index",
        action="append",
        dest="entry_indices",
        type=int,
        help=(
            "Zero-based HAR entry index. Repeat to aggregate selected "
            "pending-workflow pages in the supplied order."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        sanitize_har_to_contract_pack(
            input_har=args.input_har,
            output_dir=args.output_dir,
            profile_version=args.profile_version,
            entry_indices=args.entry_indices,
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
