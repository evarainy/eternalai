"""Strict, no-echo extraction of the OA message-center HAR contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from scripts.smoke.errors import SmokeError

_REQUIRED_FORM_FIELDS = frozenset(
    {"id", "pagesize", "msgid", "mintime", "bizstate", "selectState"}
)
_EXPECTED_PAGE_SIZE = "20"
_MAX_HAR_BYTES = 32 * 1024 * 1024


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
                pairs.append((name, value))
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
    "extract_message_center_contract",
    "har_entry_count",
)
