"""Strict, no-echo extraction of the OA smoke HAR contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote_plus, urlsplit

from scripts.smoke.errors import SmokeError

_REQUIRED_FORM_FIELDS = frozenset(
    {"id", "pagesize", "msgid", "mintime", "bizstate", "selectState"}
)
_EXPECTED_PAGE_SIZE = "20"
_MAX_HAR_BYTES = 32 * 1024 * 1024
_TODO_VIEW_CONDITION = "5"
_TODO_SESSION_KEY_LENGTH = 69
_TODO_SPLIT_CONFIG_FIELDS = (
    "actiontype",
    "hideNoDataTab",
    "method",
    "officalType",
    "viewScope",
)


@dataclass(frozen=True, repr=False, slots=True)
class MessageCenterContract:
    """Sensitive values extracted in memory and never rendered."""

    source_entry_index: int
    matching_entry_count: int
    base_url: str
    endpoint_path: str
    bizstate: str
    select_state: str

    def __repr__(self) -> str:
        return "MessageCenterContract(structural_only=True)"


@dataclass(frozen=True, repr=False, slots=True)
class TodoListContract:
    """Value-bearing to-do configuration with only structural ``repr`` output."""

    split_page_key_source_entry_index: int
    counts_source_entry_index: int
    datas_source_entry_indices: tuple[int, ...]
    matching_sequence_count: int
    base_url: str
    split_page_key_path: str
    counts_path: str
    datas_path: str
    actiontype: str
    hide_no_data_tab: str
    method: str
    offical_type: str
    view_scope: str
    sort_params: str
    authoritative_count_matches: bool

    def __repr__(self) -> str:
        return "TodoListContract(structural_only=True)"


def extract_message_center_contract(path: Path) -> MessageCenterContract:
    """Extract one unambiguous message-center signature without echoing values."""

    payload = _load_har(path)
    log = payload.get("log")
    if not isinstance(log, Mapping):
        raise SmokeError("har_log_missing")
    entries = log.get("entries")
    if not isinstance(entries, list):
        raise SmokeError("har_entries_missing")

    candidates: list[tuple[int, tuple[str, str, str, str]]] = []
    for index, entry in enumerate(entries):
        signature = _candidate_signature(entry)
        if signature is not None:
            candidates.append((index, signature))
    if not candidates:
        raise SmokeError("message_center_entry_not_found")

    signatures = {candidate[1] for candidate in candidates}
    if len(signatures) != 1:
        raise SmokeError("message_center_entry_ambiguous")
    source_index, signature = candidates[0]
    base_url, endpoint_path, bizstate, select_state = signature
    return MessageCenterContract(
        source_entry_index=source_index,
        matching_entry_count=len(candidates),
        base_url=base_url,
        endpoint_path=endpoint_path,
        bizstate=bizstate,
        select_state=select_state,
    )


def extract_todo_list_contract(path: Path) -> TodoListContract:
    """Extract the to-do contract without retaining raw HAR data in exceptions."""

    contract: TodoListContract | None = None
    error_code: str | None = None
    try:
        contract = _extract_todo_list_contract_sensitive(path)
    except SmokeError as exc:
        error_code = str(exc)
    except Exception:
        error_code = "todo_list_contract_extraction_failed"
    if error_code is not None:
        raise SmokeError(error_code) from None
    if contract is None:
        raise SmokeError("todo_list_contract_extraction_failed")
    return contract


def _extract_todo_list_contract_sensitive(path: Path) -> TodoListContract:
    """Process raw HAR data behind the public no-traceback-data boundary."""

    payload = _load_har(path)
    log = payload.get("log")
    if not isinstance(log, Mapping):
        raise SmokeError("har_log_missing")
    entries = log.get("entries")
    if not isinstance(entries, list):
        raise SmokeError("har_entries_missing")

    candidates: list[TodoListContract] = []
    for index, entry in enumerate(entries):
        split = _todo_split_candidate(entry)
        if split is None:
            continue
        (
            base_url,
            split_path,
            actiontype,
            hide_no_data_tab,
            method,
            offical_type,
            view_scope,
            session_key,
        ) = split
        counts: list[tuple[int, str, int]] = []
        datas: list[tuple[int, str, str, int]] = []
        for linked_index, linked_entry in enumerate(entries):
            linked = _todo_linked_candidate(
                linked_entry,
                base_url=base_url,
                session_key=session_key,
            )
            if linked is None:
                continue
            kind, endpoint_path, sort_params, record_or_count = linked
            if kind == "counts":
                counts.append((linked_index, endpoint_path, record_or_count))
            else:
                datas.append(
                    (
                        linked_index,
                        endpoint_path,
                        sort_params,
                        record_or_count,
                    )
                )
        # The query credential is deliberately excluded from every retained object.
        del session_key
        if len(counts) != 1 or not datas:
            continue
        counts_paths = {item[1] for item in counts}
        datas_paths = {item[1] for item in datas}
        sort_values = {item[2] for item in datas}
        if len(counts_paths) != 1 or len(datas_paths) != 1 or len(sort_values) != 1:
            raise SmokeError("todo_list_entry_ambiguous")
        authoritative_count = counts[0][2]
        record_count = sum(item[3] for item in datas)
        if authoritative_count != record_count:
            raise SmokeError("todo_list_authoritative_count_mismatch")
        candidates.append(
            TodoListContract(
                split_page_key_source_entry_index=index,
                counts_source_entry_index=counts[0][0],
                datas_source_entry_indices=tuple(item[0] for item in datas),
                matching_sequence_count=0,
                base_url=base_url,
                split_page_key_path=split_path,
                counts_path=counts[0][1],
                datas_path=datas[0][1],
                actiontype=actiontype,
                hide_no_data_tab=hide_no_data_tab,
                method=method,
                offical_type=offical_type,
                view_scope=view_scope,
                sort_params=datas[0][2],
                authoritative_count_matches=True,
            )
        )

    if not candidates:
        raise SmokeError("todo_list_entry_not_found")
    signatures = {_todo_contract_signature(candidate) for candidate in candidates}
    if len(signatures) != 1:
        raise SmokeError("todo_list_entry_ambiguous")
    first = candidates[0]
    return TodoListContract(
        split_page_key_source_entry_index=first.split_page_key_source_entry_index,
        counts_source_entry_index=first.counts_source_entry_index,
        datas_source_entry_indices=first.datas_source_entry_indices,
        matching_sequence_count=len(candidates),
        base_url=first.base_url,
        split_page_key_path=first.split_page_key_path,
        counts_path=first.counts_path,
        datas_path=first.datas_path,
        actiontype=first.actiontype,
        hide_no_data_tab=first.hide_no_data_tab,
        method=first.method,
        offical_type=first.offical_type,
        view_scope=first.view_scope,
        sort_params=first.sort_params,
        authoritative_count_matches=True,
    )


def har_entry_count(path: Path) -> int:
    """Return only the number of HAR entries."""

    payload = _load_har(path)
    log = payload.get("log")
    entries = log.get("entries") if isinstance(log, Mapping) else None
    if not isinstance(entries, list):
        raise SmokeError("har_entries_missing")
    return len(entries)


def _load_har(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > _MAX_HAR_BYTES:
            raise SmokeError("har_too_large")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except SmokeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SmokeError("har_unreadable") from None
    if not isinstance(payload, dict):
        raise SmokeError("har_root_invalid")
    return payload


def _candidate_signature(entry: Any) -> tuple[str, str, str, str] | None:
    if not isinstance(entry, Mapping):
        return None
    request = entry.get("request")
    if not isinstance(request, Mapping):
        return None
    raw_url = request.get("url")
    if not isinstance(raw_url, str):
        return None
    form = _read_form(request.get("postData"))
    if form is None or not _REQUIRED_FORM_FIELDS.issubset(form):
        return None
    if form["pagesize"] != _EXPECTED_PAGE_SIZE:
        raise SmokeError("message_center_page_size_mismatch")
    if form["msgid"] != "0" or form["mintime"] != "0":
        raise SmokeError("message_center_initial_cursor_mismatch")

    parsed = urlsplit(raw_url)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
        or "\\" in parsed.path
    ):
        raise SmokeError("message_center_url_invalid")
    base_url = f"{parsed.scheme.lower()}://{parsed.netloc}"
    return base_url, parsed.path, form["bizstate"], form["selectState"]


def _todo_split_candidate(
    entry: Any,
) -> tuple[str, str, str, str, str, str, str, str] | None:
    request = _request_mapping(entry)
    if request is None or not _raw_path_endswith(request, "/splitPageKey"):
        return None
    endpoint = _safe_request_endpoint(request)
    if endpoint is None:
        return None
    required = frozenset((*_TODO_SPLIT_CONFIG_FIELDS, "viewcondition"))
    form = _read_selected_form(
        request.get("postData"),
        required,
        duplicate_error="todo_list_form_duplicate",
    )
    if form is None or set(form) != required:
        return None
    if form["viewcondition"] != _TODO_VIEW_CONDITION:
        return None
    response = _response_json(entry)
    if response is None:
        return None
    session_key = response.get("sessionkey")
    if (
        not isinstance(session_key, str)
        or len(session_key) != _TODO_SESSION_KEY_LENGTH
        or session_key != session_key.strip()
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in session_key
        )
    ):
        return None
    return (
        endpoint[0],
        endpoint[1],
        form["actiontype"],
        form["hideNoDataTab"],
        form["method"],
        form["officalType"],
        form["viewScope"],
        session_key,
    )


def _todo_linked_candidate(
    entry: Any,
    *,
    base_url: str,
    session_key: str,
) -> tuple[str, str, str, int] | None:
    request = _request_mapping(entry)
    if request is None:
        return None
    is_counts = _raw_path_endswith(request, "/counts")
    is_datas = _raw_path_endswith(request, "/datas")
    if not is_counts and not is_datas:
        return None
    endpoint = _safe_request_endpoint(request)
    if endpoint is None or endpoint[0] != base_url:
        return None
    if is_counts:
        form = _read_selected_form(
            request.get("postData"),
            frozenset({"dataKey"}),
            duplicate_error="todo_list_form_duplicate",
        )
        if form is None or form.get("dataKey") != session_key:
            return None
        response = _response_json(entry)
        count = response.get("count") if response is not None else None
        if (
            response is None
            or response.get("status") is not True
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            return None
        return "counts", endpoint[1], "", count
    form = _read_selected_form(
        request.get("postData"),
        frozenset({"current", "dataKey", "sortParams"}),
        duplicate_error="todo_list_form_duplicate",
    )
    if form is None or form.get("dataKey") != session_key:
        return None
    current = form.get("current", "")
    if not current.isdecimal() or int(current) < 1:
        return None
    response = _response_json(entry)
    records = response.get("datas") if response is not None else None
    if response is None or response.get("status") is not True or not isinstance(records, list):
        return None
    return "datas", endpoint[1], form.get("sortParams", ""), len(records)


def _request_mapping(entry: Any) -> Mapping[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None
    request = entry.get("request")
    return request if isinstance(request, Mapping) else None


def _raw_path_endswith(request: Mapping[str, Any], suffix: str) -> bool:
    raw_url = request.get("url")
    if not isinstance(raw_url, str):
        return False
    try:
        return urlsplit(raw_url).path.endswith(suffix)
    except ValueError:
        return False


def _safe_request_endpoint(request: Mapping[str, Any]) -> tuple[str, str] | None:
    raw_url = request.get("url")
    if not isinstance(raw_url, str):
        return None
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
        or "\\" in parsed.path
    ):
        raise SmokeError("todo_list_url_invalid")
    return f"{parsed.scheme.lower()}://{parsed.netloc}", parsed.path


def _response_json(entry: Any) -> Mapping[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None
    response = entry.get("response")
    content = response.get("content") if isinstance(response, Mapping) else None
    text = content.get("text") if isinstance(content, Mapping) else None
    if not isinstance(text, str):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _read_selected_form(
    post_data: Any,
    fields: frozenset[str],
    *,
    duplicate_error: str,
) -> dict[str, str] | None:
    if not isinstance(post_data, Mapping):
        return None
    raw_params = post_data.get("params")
    pairs: list[tuple[str, str]] = []
    if isinstance(raw_params, list):
        for item in raw_params:
            if not isinstance(item, Mapping):
                continue
            name = item.get("name")
            value = item.get("value")
            if isinstance(name, str) and isinstance(value, str):
                pairs.append((name, unquote_plus(value)))
    elif isinstance(post_data.get("text"), str):
        try:
            pairs = parse_qsl(
                post_data["text"],
                keep_blank_values=True,
                strict_parsing=True,
            )
        except ValueError:
            return None
    else:
        return None

    result: dict[str, str] = {}
    for name, value in pairs:
        if name not in fields:
            continue
        if name in result:
            raise SmokeError(duplicate_error)
        if value != value.strip() or not value or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        ):
            raise SmokeError("todo_list_form_invalid")
        result[name] = value
    return result


def _todo_contract_signature(contract: TodoListContract) -> tuple[str, ...]:
    return (
        contract.base_url,
        contract.split_page_key_path,
        contract.counts_path,
        contract.datas_path,
        contract.actiontype,
        contract.hide_no_data_tab,
        contract.method,
        contract.offical_type,
        contract.view_scope,
        contract.sort_params,
    )


def _read_form(post_data: Any) -> dict[str, str] | None:
    if not isinstance(post_data, Mapping):
        return None
    pairs: list[tuple[str, str]] = []
    raw_params = post_data.get("params")
    if isinstance(raw_params, list):
        for item in raw_params:
            if not isinstance(item, Mapping):
                continue
            name = item.get("name")
            value = item.get("value")
            if isinstance(name, str) and isinstance(value, str):
                pairs.append((unquote_plus(name), unquote_plus(value)))
    elif isinstance(post_data.get("text"), str):
        try:
            pairs = parse_qsl(
                post_data["text"],
                keep_blank_values=True,
                strict_parsing=True,
            )
        except ValueError:
            return None
    else:
        return None

    result: dict[str, str] = {}
    for name, value in pairs:
        if name not in _REQUIRED_FORM_FIELDS:
            continue
        if name in result:
            raise SmokeError("message_center_form_duplicate")
        if value != value.strip() or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        ):
            raise SmokeError("message_center_form_invalid")
        result[name] = value
    return result


__all__ = (
    "MessageCenterContract",
    "TodoListContract",
    "extract_message_center_contract",
    "extract_todo_list_contract",
    "har_entry_count",
)
