"""Build a credential-free OA Contract Pack from selected local HAR responses."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import parse_qsl, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError  # noqa: E402

from app.infra.adapters.oa.contracts import (  # noqa: E402
    EXTERNAL_SANITIZATION_WARNING,
    OAContractPackProfile,
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
    build_structural_fingerprint,
)

_PROFILE_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_PENDING_WORKFLOW_PROFILE_VERSION = "ecology9-pending-workflows-v1"
_SYSTEM_MESSAGE_PROFILE_VERSION = "ecology9-system-messages-v1"
_PENDING_WORKFLOW_PROFILE_VERSION_PATTERN = re.compile(
    r"^ecology9-pending-workflows-v[1-9][0-9]*$"
)
_MAX_HAR_BYTES = 32 * 1024 * 1024
_MAX_JSON_CONTAINER_BYTES = 1 * 1024 * 1024
_MAX_RECORDS = 10_000
_MAX_EMBEDDED_JSON_DEPTH = 8
# At the 32 MiB scan bound, random hex collisions fall from 0.78% at 8
# characters to 0.049% at 9 characters.
_MIN_SENSITIVE_SUBSTRING_LENGTH = 9
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


class _NoEchoArgumentParser(argparse.ArgumentParser):
    """Reject invalid CLI input without rendering its raw value."""

    def error(self, _message: str) -> NoReturn:
        raise SanitizationError("invalid_arguments")


def _capture_profile_kind(profile_version: str) -> str:
    if _PROFILE_VERSION_PATTERN.fullmatch(profile_version) is None:
        raise SanitizationError("invalid_profile_version")
    if _PENDING_WORKFLOW_PROFILE_VERSION_PATTERN.fullmatch(profile_version):
        return "pending_workflows"
    if profile_version == _SYSTEM_MESSAGE_PROFILE_VERSION:
        return "system_messages"
    raise SanitizationError("invalid_profile_version")


def sanitize_har_to_contract_pack(
    *,
    input_har: Path,
    output_dir: Path,
    profile_version: str,
    entry_indices: Sequence[int] | None = None,
) -> None:
    """Create a Contract Pack atomically or leave the target absent."""

    profile_kind = _capture_profile_kind(profile_version)
    if output_dir.name != profile_version:
        raise SanitizationError("profile_output_name_mismatch")
    if output_dir.exists():
        raise SanitizationError("output_already_exists")
    if not output_dir.parent.is_dir():
        raise SanitizationError("output_parent_missing")

    har = _load_har(input_har)
    if profile_kind == "pending_workflows":
        raw_payloads = _select_pending_workflow_payloads(
            har,
            entry_indices=entry_indices,
        )
        system_message_page_size = None
        capability_id = "oa.list_pending_workflows"
        source_kind = "sanitized_capture"
        sanitizer_version = "1"
        source_warning = None
    else:
        raw_payload, system_message_page_size = _select_system_message_payload(
            har,
            entry_indices=entry_indices,
        )
        raw_payloads = [raw_payload]
        capability_id = "oa.list_system_messages"
        source_kind = "externally_sanitized_capture"
        sanitizer_version = "2"
        source_warning = EXTERNAL_SANITIZATION_WARNING
    sensitive_values = _collect_sensitive_values(
        har,
        short_transport_as_full_token=(
            source_kind == "externally_sanitized_capture"
        ),
    )
    for raw_payload in raw_payloads:
        _collect_sensitive_fields(raw_payload, sensitive_values)
        _assert_response_payload_has_no_forbidden_keys(raw_payload)
    if profile_kind == "system_messages":
        sensitive_values.update(_system_message_source_values(raw_payloads[0]))
        assert system_message_page_size is not None
        sample = _normalize_system_message_sample(
            raw_payloads[0],
            page_size=system_message_page_size,
        )
    else:
        sample = _normalize_pending_workflow_sample(raw_payloads)
    fingerprint = build_structural_fingerprint(sample)
    profile = {
        "profile_version": profile_version,
        "capability_id": capability_id,
        "source_kind": source_kind,
        "sanitizer_version": sanitizer_version,
        "sample_file": "sample.json",
        "fingerprint_file": "fingerprint.json",
    }
    if source_warning is not None:
        profile["source_warning"] = source_warning
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


def _select_system_message_payload(
    har: Mapping[str, Any],
    *,
    entry_indices: Sequence[int] | None,
) -> tuple[Mapping[str, Any], int]:
    log = har.get("log")
    if not isinstance(log, Mapping):
        raise SanitizationError("har_log_missing")
    entries = log.get("entries")
    if not isinstance(entries, list):
        raise SanitizationError("har_entries_missing")

    if entry_indices is None:
        candidates = [
            entry
            for entry in entries
            if (payload := _response_json(entry)) is not None
            and _is_system_message_payload(payload)
        ]
        if len(candidates) != 1:
            raise SanitizationError("system_message_response_not_unique")
        selected_entry = candidates[0]
    else:
        if len(entry_indices) != 1:
            raise SanitizationError("system_message_entry_index_invalid")
        entry_index = entry_indices[0]
        if not isinstance(entry_index, int) or isinstance(entry_index, bool):
            raise SanitizationError("entry_index_invalid")
        if entry_index < 0 or entry_index >= len(entries):
            raise SanitizationError("entry_index_out_of_range")
        selected_entry = entries[entry_index]

    payload = _response_json(selected_entry, selected=True)
    if payload is None or not _is_system_message_payload(payload):
        raise SanitizationError("selected_entry_not_system_message_response")
    return payload, _system_message_page_size(selected_entry)


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
    text = _decoded_response_text(content, selected=selected)
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


def _is_system_message_payload(payload: Mapping[str, Any]) -> bool:
    records = payload.get("data")
    return (
        isinstance(records, list)
        and all(
            isinstance(record, Mapping)
            and {"messageid", "title", "context", "name", "time"}.issubset(record)
            for record in records
        )
    )


def _normalize_pending_workflow_sample(
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


def _normalize_system_message_sample(
    raw_payload: Mapping[str, Any],
    *,
    page_size: int,
) -> dict[str, Any]:
    records = raw_payload.get("data")
    if not isinstance(records, list) or len(records) > _MAX_RECORDS:
        raise SanitizationError("response_records_invalid")
    if len(records) > page_size:
        raise SanitizationError("system_message_page_size_invalid")

    messages: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise SanitizationError("response_record_invalid")
        raw_message_id = _require_string(record, "messageid")
        raw_title = _require_string(record, "title")
        raw_content = _require_string(record, "context")
        raw_source_name = _require_string(record, "name")
        raw_occurred_at = _require_string(record, "time")
        raw_business_state = _require_string(record, "bizstate")
        raw_link = _optional_blankable_string(record, "link")
        raw_mobile_link = _optional_blankable_string(record, "linkmobileurl")
        messages.append(
            {
                "message_id": _synthetic_identifier(len(raw_message_id), index),
                "title": _synthetic_chinese_text(len(raw_title), index),
                "content": _synthetic_chinese_text(len(raw_content), index + 100),
                "source_name": _synthetic_chinese_text(
                    len(raw_source_name), index + 200
                ),
                "occurred_at": _synthetic_timestamp(len(raw_occurred_at)),
                "business_state": _synthetic_machine_text(
                    len(raw_business_state), index
                ),
                "link": (
                    _synthetic_relative_path(len(raw_link), index, "desktop")
                    if raw_link
                    else None
                ),
                "mobile_link": (
                    _synthetic_relative_path(len(raw_mobile_link), index, "mobile")
                    if raw_mobile_link
                    else None
                ),
            }
        )
    sample = {
        "messages": messages,
        "returned_count": len(messages),
        "is_complete": len(messages) < page_size,
    }
    try:
        OASystemMessageCollection.model_validate(sample, strict=True)
    except ValidationError:
        raise SanitizationError("normalized_sample_invalid") from None
    return sample


def _system_message_source_values(raw_payload: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    records = raw_payload.get("data")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, Mapping):
                continue
            for key in (
                "messageid",
                "title",
                "context",
                "name",
                "time",
                "link",
                "linkmobileurl",
            ):
                value = record.get(key)
                if isinstance(value, str) and value:
                    values.add(value)
    for key in ("mintime", "msgid", "maxtime"):
        value = raw_payload.get(key)
        if isinstance(value, str) and value:
            values.add(value)
    return values


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


def _optional_blankable_string(
    record: Mapping[str, Any],
    key: str,
) -> str | None:
    value = record.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise SanitizationError("response_optional_string_invalid")
    return value


def _synthetic_identifier(length: int, index: int) -> str:
    if length <= 0:
        raise SanitizationError("response_required_string_invalid")
    seed = f"900{index:05d}"
    return (seed * ((length // len(seed)) + 1))[:length]


def _synthetic_chinese_text(length: int, index: int) -> str:
    if length <= 0:
        raise SanitizationError("response_required_string_invalid")
    alphabet = "系统消息合成样本文本内容通知提醒待办查阅"
    offset = index % len(alphabet)
    rotated = alphabet[offset:] + alphabet[:offset]
    return (rotated * ((length // len(rotated)) + 1))[:length]


def _synthetic_timestamp(length: int) -> str:
    seed = "2000-01-01 00:00:00"
    if length <= 0:
        raise SanitizationError("response_required_string_invalid")
    return (seed * ((length // len(seed)) + 1))[:length]


def _synthetic_machine_text(length: int, index: int) -> str:
    if length <= 0:
        raise SanitizationError("response_required_string_invalid")
    seed = str((index % 8) + 1)
    return seed * length


def _synthetic_relative_path(
    length: int,
    index: int,
    channel: str,
) -> str:
    prefix = f"/oa/system-messages/{channel}/{index:03d}"
    if length < len(prefix):
        return "/" + ("m" * (length - 1))
    return prefix + ("x" * (length - len(prefix)))


def _collect_sensitive_values(
    har: Mapping[str, Any],
    *,
    short_transport_as_full_token: bool = False,
) -> set[str]:
    values: set[str] = set()
    _collect_sensitive_fields(har, values)
    log = har.get("log")
    if isinstance(log, Mapping):
        entries = log.get("entries")
        if isinstance(entries, list):
            for entry in entries:
                decoded_payload = _decoded_entry_response_json(entry)
                if decoded_payload is not None:
                    _collect_sensitive_fields(decoded_payload, values)
                _collect_entry_credentials(
                    entry,
                    values,
                    short_transport_as_full_token=short_transport_as_full_token,
                )
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


def _collect_entry_credentials(
    entry: Any,
    output: set[str],
    *,
    short_transport_as_full_token: bool,
) -> None:
    if not isinstance(entry, Mapping):
        return
    for side_name in ("request", "response"):
        side = entry.get(side_name)
        if not isinstance(side, Mapping):
            continue
        _collect_header_values(
            side.get("headers"),
            output,
            short_transport_as_full_token=short_transport_as_full_token,
        )
        _collect_cookie_values(
            side.get("cookies"),
            output,
            short_transport_as_full_token=short_transport_as_full_token,
        )
        if side_name == "request":
            _collect_request_parameter_values(side, output)


def _collect_request_parameter_values(
    request: Mapping[str, Any],
    output: set[str],
) -> None:
    url = request.get("url")
    if isinstance(url, str):
        try:
            parsed = urlsplit(url)
            url_parameters = parse_qsl(
                parsed.query,
                keep_blank_values=True,
                max_num_fields=_MAX_RECORDS,
            )
        except ValueError:
            raise SanitizationError("request_parameters_invalid") from None
        _collect_named_parameter_values(url_parameters, output)
        if parsed.username:
            output.add(parsed.username)
        if parsed.password:
            output.add(parsed.password)

    _collect_har_parameter_values(request.get("queryString"), output)
    post_data = request.get("postData")
    if not isinstance(post_data, Mapping):
        return
    _collect_har_parameter_values(post_data.get("params"), output)
    mime_type = post_data.get("mimeType")
    text = post_data.get("text")
    if (
        isinstance(mime_type, str)
        and mime_type.partition(";")[0].strip().casefold()
        == "application/x-www-form-urlencoded"
    ):
        if text is None:
            return
        if not isinstance(text, str):
            raise SanitizationError("request_parameters_invalid")
        try:
            form_parameters = parse_qsl(
                text,
                keep_blank_values=True,
                max_num_fields=_MAX_RECORDS,
            )
        except ValueError:
            raise SanitizationError("request_parameters_invalid") from None
        _collect_named_parameter_values(form_parameters, output)


def _collect_har_parameter_values(parameters: Any, output: set[str]) -> None:
    if parameters is None:
        return
    if not isinstance(parameters, list):
        raise SanitizationError("request_parameters_invalid")
    named_values: list[tuple[str, str]] = []
    for parameter in parameters:
        if not isinstance(parameter, Mapping):
            raise SanitizationError("request_parameters_invalid")
        name = parameter.get("name")
        value = parameter.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            raise SanitizationError("request_parameters_invalid")
        named_values.append((name, value))
    _collect_named_parameter_values(named_values, output)


def _collect_named_parameter_values(
    parameters: Iterable[tuple[str, str]],
    output: set[str],
) -> None:
    for name, value in parameters:
        normalized_name = _normalize_key(name)
        if (
            normalized_name in _FORBIDDEN_KEYS
            or normalized_name in _SENSITIVE_HEADER_KEYS
        ):
            output.add(value)
            output.update(_credential_components(value))


def _system_message_page_size(entry: Any) -> int:
    if not isinstance(entry, Mapping):
        raise SanitizationError("selected_entry_invalid")
    request = entry.get("request")
    if not isinstance(request, Mapping):
        raise SanitizationError("selected_entry_invalid")
    observed: list[str] = []
    post_data = request.get("postData")
    if isinstance(post_data, Mapping):
        parameters = post_data.get("params")
        if isinstance(parameters, list):
            for parameter in parameters:
                if (
                    isinstance(parameter, Mapping)
                    and _normalize_key(str(parameter.get("name", "")))
                    == "pagesize"
                    and isinstance(parameter.get("value"), str)
                ):
                    observed.append(parameter["value"])
        text = post_data.get("text")
        if isinstance(text, str):
            try:
                form_parameters = parse_qsl(
                    text,
                    keep_blank_values=True,
                    max_num_fields=_MAX_RECORDS,
                )
            except ValueError:
                raise SanitizationError("request_parameters_invalid") from None
            observed.extend(
                value
                for name, value in form_parameters
                if _normalize_key(name) == "pagesize"
            )
    if not observed or any(value != observed[0] for value in observed[1:]):
        raise SanitizationError("system_message_page_size_invalid")
    try:
        page_size = int(observed[0], 10)
    except ValueError:
        raise SanitizationError("system_message_page_size_invalid") from None
    if not 1 <= page_size <= _MAX_RECORDS:
        raise SanitizationError("system_message_page_size_invalid")
    return page_size


def _decoded_entry_response_json(entry: Any) -> Mapping[str, Any] | list[Any] | None:
    if not isinstance(entry, Mapping):
        return None
    response = entry.get("response")
    if not isinstance(response, Mapping):
        return None
    content = response.get("content")
    if not isinstance(content, Mapping):
        return None
    text = _decoded_response_text(content, selected=False)
    if text is None:
        return None
    try:
        payload = _bounded_json_loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, (Mapping, list)) else None


def _decoded_response_text(
    content: Mapping[str, Any],
    *,
    selected: bool,
) -> str | None:
    text = content.get("text")
    if not isinstance(text, str):
        if selected:
            raise SanitizationError("selected_entry_invalid")
        return None
    encoding = content.get("encoding")
    if encoding in (None, ""):
        return text
    if not isinstance(encoding, str) or encoding.casefold() != "base64":
        raise SanitizationError("encoded_response_not_supported")
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise SanitizationError("encoded_response_invalid") from None
    mime_type = content.get("mimeType")
    normalized_mime = (
        mime_type.partition(";")[0].strip().casefold()
        if isinstance(mime_type, str)
        else ""
    )
    is_textual = normalized_mime.startswith("text/") or normalized_mime in {
        "application/json",
        "application/problem+json",
    }
    if not is_textual:
        if selected:
            raise SanitizationError("selected_entry_response_not_json")
        return None
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError:
        raise SanitizationError("encoded_response_invalid") from None


def _collect_header_values(
    headers: Any,
    output: set[str],
    *,
    short_transport_as_full_token: bool = False,
) -> None:
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
            components = _credential_components(value)
            if (
                not short_transport_as_full_token
                or len(value) >= _MIN_SENSITIVE_SUBSTRING_LENGTH
            ):
                output.add(value)
            else:
                output.add(f"{name}: {value}")
            output.update(
                component
                for component in components
                if not short_transport_as_full_token
                or len(component) >= _MIN_SENSITIVE_SUBSTRING_LENGTH
            )


def _collect_cookie_values(
    cookies: Any,
    output: set[str],
    *,
    short_transport_as_full_token: bool = False,
) -> None:
    if not isinstance(cookies, list):
        return
    for cookie in cookies:
        if not isinstance(cookie, Mapping):
            continue
        name = cookie.get("name")
        value = cookie.get("value")
        if (
            isinstance(name, str)
            and (
                not short_transport_as_full_token
                or len(name) >= _MIN_SENSITIVE_SUBSTRING_LENGTH
            )
        ):
            output.add(name)
        if (
            isinstance(value, str)
            and (
                not short_transport_as_full_token
                or len(value) >= _MIN_SENSITIVE_SUBSTRING_LENGTH
            )
        ):
            output.add(value)
        if isinstance(name, str) and isinstance(value, str):
            output.add(f"{name}={value}")


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
    for value in sensitive_values:
        if len(value) >= _MIN_SENSITIVE_SUBSTRING_LENGTH:
            survived = value in rendered
        else:
            survived = (
                re.search(
                    rf"(?<!\w){re.escape(value)}(?!\w)",
                    rendered,
                )
                is not None
            )
        if survived:
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
        profile = OAContractPackProfile.model_validate(
            candidate_payloads["profile.json"],
            strict=True,
        )
        collection_model = (
            OAPendingWorkflowCollection
            if profile.capability_id == "oa.list_pending_workflows"
            else OASystemMessageCollection
        )
        collection_model.model_validate(candidate_payloads["sample.json"], strict=True)
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
    parser = _NoEchoArgumentParser(
        description="Offline OA HAR to sanitized Contract Pack converter.",
    )
    parser.add_argument("--input-har", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--profile-version", required=True)
    parser.add_argument(
        "--entry-index",
        action="append",
        dest="entry_indices",
        help=(
            "Zero-based HAR entry index. Repeat only for a multi-page "
            "pending-workflow capture."
        ),
    )
    return parser


def _parse_cli_args(argv: list[str] | None) -> argparse.Namespace:
    args: argparse.Namespace | None = None
    parse_failed = False
    try:
        args = _build_parser().parse_args(argv)
    except SanitizationError:
        parse_failed = True
    if parse_failed or args is None:
        raise SanitizationError("invalid_arguments")
    return args


def _parse_entry_indices(raw_values: Sequence[str] | None) -> list[int] | None:
    if raw_values is None:
        return None
    entry_indices: list[int] = []
    for raw_value in raw_values:
        entry_index = 0
        conversion_failed = False
        try:
            entry_index = int(raw_value, 10)
        except (TypeError, ValueError):
            conversion_failed = True
        if conversion_failed:
            raise SanitizationError("entry_index_invalid")
        entry_indices.append(entry_index)
    return entry_indices


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_cli_args(argv)
        sanitize_har_to_contract_pack(
            input_har=args.input_har,
            output_dir=args.output_dir,
            profile_version=args.profile_version,
            entry_indices=_parse_entry_indices(args.entry_indices),
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
