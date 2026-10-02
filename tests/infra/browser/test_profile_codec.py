from __future__ import annotations

import copy
from uuid import uuid4

import pytest

from app.infra.browser.browserless_wire import BrowserProviderError
from app.infra.browser.profile_codec import DIAGNOSTICS, from_playwright, validate_upload
from tests.infra.browser.provider_factories import source, state


def test_complete_idb_json_values_and_local_storage_are_preserved() -> None:
    raw = state()
    raw["origins"][0]["localStorage"] = [{"name": "fixture", "value": "synthetic"}]
    before = copy.deepcopy(raw)
    result = from_playwright(raw, source())
    assert raw == before
    assert result["origins"][0]["localStorage"] == {"fixture": "synthetic"}
    store = result["origins"][0]["indexedDBs"][0]["objectStores"][0]
    assert store["entries"] == raw["origins"][0]["indexedDB"][0]["stores"][0]["records"]
    assert store["keyPath"] is None and store["autoIncrement"] is False


@pytest.mark.parametrize("compound", [False, True])
def test_playwright_inline_and_compound_idb_keys_and_indexes_preserved(compound) -> None:
    raw = state()
    store = raw["origins"][0]["indexedDB"][0]["stores"][0]
    del store["keyPath"]
    store["keyPathArray" if compound else "keyPath"] = ["owner.id", "id"] if compound else "id"
    store["records"] = [{"value": {"owner": {"id": "owner"}, "id": 1}}]
    store["indexes"] = [
        {
            "name": "lookup",
            "keyPathArray": ["owner.id", "id"],
            "multiEntry": False,
            "unique": True,
        }
    ]
    converted = from_playwright(raw, source())["origins"][0]["indexedDBs"][0]["objectStores"][0]
    assert converted["keyPath"] == (["owner.id", "id"] if compound else "id")
    assert converted["entries"][0]["key"] == (["owner", 1] if compound else 1)
    assert converted["entries"][0]["value"] == store["records"][0]["value"]
    assert converted["indexes"][0] == {
        "name": "lookup",
        "keyPath": ["owner.id", "id"],
        "multiEntry": False,
        "unique": True,
    }


def test_cookie_attributes_are_preserved_including_session_semantics() -> None:
    raw = state()
    cookie = {
        "name": "fixture",
        "value": uuid4().hex,
        "domain": "mock-oa.example.com",
        "path": "/",
        "expires": -1,
        "httpOnly": True,
        "secure": True,
        "sameSite": "Strict",
    }
    raw["cookies"] = [cookie]
    converted = from_playwright(raw, source())
    assert converted["cookies"] == raw["cookies"]
    assert converted["cookies"][0]["httpOnly"] is True
    assert converted["cookies"][0]["expires"] == -1


@pytest.mark.parametrize(
    "mutation",
    [
        "origin",
        "duplicate_origin",
        "duplicate_key",
        "encoded_record",
        "too_many_records",
        "extra_state",
        "nan",
        "too_many_databases",
        "size",
        "cookie_domain",
        "bool_expiry",
    ],
)
def test_partial_or_unregistered_profile_state_is_never_uploaded(mutation) -> None:
    raw = state()
    origin = raw["origins"][0]
    store = origin["indexedDB"][0]["stores"][0]
    if mutation == "origin":
        origin["origin"] = "https://other.example.com"
    elif mutation == "duplicate_origin":
        raw["origins"].append(copy.deepcopy(origin))
    elif mutation == "duplicate_key":
        origin["localStorage"] = [{"name": "x", "value": "a"}, {"name": "x", "value": "b"}]
    elif mutation == "encoded_record":
        store["records"] = [{"keyEncoded": {}, "valueEncoded": {}}]
    elif mutation == "too_many_records":
        store["records"] *= 1001
    elif mutation == "extra_state":
        raw["sessionStorage"] = {}
    elif mutation == "nan":
        store["records"][0]["value"] = float("nan")
    elif mutation == "too_many_databases":
        origin["indexedDB"] *= 6
    elif mutation == "size":
        origin["localStorage"] = [{"name": "large", "value": "x" * 2_000_001}]
    else:
        raw["cookies"] = [
            {
                "name": "fixture",
                "value": "synthetic",
                "domain": "example.com",
                "path": "/",
                "expires": True if mutation == "bool_expiry" else -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ]
        if mutation == "bool_expiry":
            raw["cookies"][0]["domain"] = "mock-oa.example.com"
    with pytest.raises(BrowserProviderError, match="unsupported"):
        from_playwright(raw, source())


@pytest.mark.parametrize("diagnostic", DIAGNOSTICS)
def test_every_upload_loss_diagnostic_blocks_success(diagnostic) -> None:
    captured = from_playwright(state(), source())
    response = {
        "id": "opaque",
        "name": "generation",
        "cookieCount": 0,
        "originCount": 1,
        "lastUsedAt": None,
        "createdAt": "2026-10-02T00:00:00Z",
        "updatedAt": "2026-10-02T00:00:00Z",
        "diagnostics": dict.fromkeys(DIAGNOSTICS, 0),
    }
    response["diagnostics"][diagnostic] = 1
    with pytest.raises(BrowserProviderError, match="profile_loss"):
        validate_upload(response, "generation", captured)
