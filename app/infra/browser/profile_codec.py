"""Lossless JSON profile subset; unsupported structured IDB values fail closed."""

from __future__ import annotations

import math
from typing import cast
from urllib.parse import urlsplit

from app.infra.browser.browserless_wire import BrowserProviderError, json_bytes
from app.infra.browser.deployment_manifest import SyntheticSource

DIAGNOSTICS = (
    "skippedMalformedCookies",
    "skippedPrivateCookies",
    "skippedMalformedOrigins",
    "skippedPrivateOrigins",
    "truncatedOrigins",
    "skippedMalformedIdbDatabases",
    "truncatedIdbDatabases",
    "skippedMalformedIdbStores",
    "truncatedIdbEntries",
)
MAX_STATE_BYTES = 2_000_000  # Conservative decimal interpretation of the vendor's 2 MB limit.


def reject(reason: str = "shape") -> BrowserProviderError:
    return BrowserProviderError(
        "unsupported",
        "capture",
        "budget" if reason == "size" else "profile_loss",
    )


def mapping(
    value: object, required: set[str], optional: set[str] | None = None
) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise reject()
    result = cast(dict[str, object], value)
    if not required <= result.keys() or result.keys() - required - (optional or set()):
        raise reject()
    return result


def sequence(value: object, maximum: int = 10_000) -> list[object]:
    if not isinstance(value, list) or len(value) > maximum:
        raise reject("size")
    return cast(list[object], value)


def text(value: object, *, empty: bool = True) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise reject()
    return value


def key_path(value: object) -> None:
    if value is None or isinstance(value, str):
        return
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise reject()


def validate_json(value: object, depth: int = 0) -> None:
    if depth > 32:
        raise reject("size")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            validate_json(item, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            validate_json(item, depth + 1)
        return
    raise reject()


def validate_idb_key(value: object, depth: int = 0) -> None:
    if depth > 32:
        raise reject()
    if isinstance(value, str) or type(value) is int:
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for part in value:
            validate_idb_key(part, depth + 1)
        return
    raise reject()


def validate_state(state: object, source: SyntheticSource) -> dict[str, object]:
    """Validate before upload, including every cookie/origin/IDB entry, never truncate."""
    validate_json(state)
    try:
        if len(json_bytes(state)) > MAX_STATE_BYTES:
            raise reject("size")
    except (ValueError, UnicodeError, RecursionError):
        raise reject() from None
    root = mapping(state, {"cookies", "origins"})
    hosts = {urlsplit(origin).hostname for origin in source.origins}
    cookie_keys: set[tuple[str, str, str]] = set()
    for value in sequence(root["cookies"]):
        cookie = mapping(
            value,
            {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"},
            {"session"},
        )
        name, domain = text(cookie["name"], empty=False), text(cookie["domain"], empty=False)
        text(cookie["value"])
        path = text(cookie["path"], empty=False)
        if domain.lstrip(".") not in hosts or domain.startswith("..") or not path.startswith("/"):
            raise reject()
        if type(cookie["httpOnly"]) is not bool or type(cookie["secure"]) is not bool:
            raise reject()
        if "session" in cookie and type(cookie["session"]) is not bool:
            raise reject()
        if cookie["sameSite"] not in ("Strict", "Lax", "None"):
            raise reject()
        expires = cookie["expires"]
        if type(expires) not in (int, float) or not math.isfinite(cast(float, expires)):
            raise reject()
        identity = (name, domain, path)
        if identity in cookie_keys:
            raise reject()
        cookie_keys.add(identity)
    seen_origins: set[str] = set()
    for value in sequence(root["origins"], 50):
        origin = mapping(value, {"origin", "localStorage"}, {"indexedDBs"})
        address = text(origin["origin"])
        if address not in source.origins or address in seen_origins:
            raise reject()
        seen_origins.add(address)
        storage = origin["localStorage"]
        if not isinstance(storage, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in storage.items()
        ):
            raise reject()
        database_names: set[str] = set()
        for database_value in sequence(origin.get("indexedDBs", []), 5):
            database = mapping(database_value, {"name", "version", "objectStores"})
            name = text(database["name"])
            if name in database_names or type(database["version"]) is not int:
                raise reject()
            if database["version"] < 1:
                raise reject()
            database_names.add(name)
            store_names: set[str] = set()
            for store_value in sequence(database["objectStores"]):
                store = mapping(
                    store_value,
                    {"name", "keyPath", "autoIncrement", "entries", "indexes"},
                )
                store_name = text(store["name"])
                if store_name in store_names or type(store["autoIncrement"]) is not bool:
                    raise reject()
                store_names.add(store_name)
                key_path(store["keyPath"])
                index_names: set[str] = set()
                for index_value in sequence(store["indexes"]):
                    index = mapping(index_value, {"name", "keyPath", "multiEntry", "unique"})
                    index_name = text(index["name"])
                    if index_name in index_names:
                        raise reject()
                    index_names.add(index_name)
                    key_path(index["keyPath"])
                    if type(index["multiEntry"]) is not bool or type(index["unique"]) is not bool:
                        raise reject()
                for entry in sequence(store["entries"], 1000):
                    validated = mapping(entry, {"key", "value"})
                    validate_idb_key(validated["key"])
    return root


def playwright_key_path(item: dict[str, object]) -> object:
    if "keyPath" in item and "keyPathArray" in item:
        raise reject()
    result = item.get("keyPathArray", item.get("keyPath"))
    key_path(result)
    return result


def inline_key(value: object, path: object) -> object:
    if isinstance(path, list):
        return [inline_key(value, part) for part in path]
    if not isinstance(path, str):
        raise reject()
    if path == "":
        return value
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise reject()
        value = value[part]
    return value


def from_playwright(state: object, source: SyntheticSource) -> dict[str, object]:
    root = mapping(state, {"cookies", "origins"})
    origins: list[dict[str, object]] = []
    for value in sequence(root["origins"], 50):
        origin = mapping(value, {"origin", "localStorage"}, {"indexedDB"})
        storage: dict[str, str] = {}
        for item in sequence(origin["localStorage"]):
            pair = mapping(item, {"name", "value"})
            key = text(pair["name"])
            if key in storage:
                raise reject()
            storage[key] = text(pair["value"])
        databases: list[dict[str, object]] = []
        for item in sequence(origin.get("indexedDB", []), 5):
            database = mapping(item, {"name", "version", "stores"})
            stores: list[dict[str, object]] = []
            for store_item in sequence(database["stores"]):
                store = mapping(
                    store_item,
                    {"name", "autoIncrement", "records", "indexes"},
                    {"keyPath", "keyPathArray"},
                )
                path = playwright_key_path(store)
                # Structured/encoded records require a separately verified codec.
                # Reject them before mutation rather than degrading authentication.
                entries: list[dict[str, object]] = []
                for record_value in sequence(store["records"], 1000):
                    record = mapping(record_value, {"value"}, {"key"})
                    if path is None:
                        if "key" not in record:
                            raise reject()
                        record_key = record["key"]
                    else:
                        if "key" in record:
                            raise reject()
                        record_key = inline_key(record["value"], path)
                    entries.append({"key": record_key, "value": record["value"]})
                indexes: list[dict[str, object]] = []
                for index_value in sequence(store["indexes"]):
                    index = mapping(
                        index_value,
                        {"name", "multiEntry", "unique"},
                        {"keyPath", "keyPathArray"},
                    )
                    index_path = playwright_key_path(index)
                    if index_path is None:
                        raise reject()
                    indexes.append(
                        {
                            "name": index["name"],
                            "keyPath": index_path,
                            "multiEntry": index["multiEntry"],
                            "unique": index["unique"],
                        }
                    )
                stores.append(
                    {
                        "name": store["name"],
                        "keyPath": path,
                        "autoIncrement": store["autoIncrement"],
                        "indexes": indexes,
                        "entries": entries,
                    }
                )
            databases.append(
                {
                    "name": database["name"],
                    "version": database["version"],
                    "objectStores": stores,
                }
            )
        origins.append(
            {
                "origin": origin["origin"],
                "localStorage": storage,
                "indexedDBs": databases,
            }
        )
    return validate_state({"cookies": root["cookies"], "origins": origins}, source)


def validate_upload(result: object, name: str, state: dict[str, object]) -> None:
    metadata = mapping(
        result,
        {
            "id",
            "name",
            "cookieCount",
            "originCount",
            "lastUsedAt",
            "createdAt",
            "updatedAt",
            "diagnostics",
        },
    )
    if text(metadata["name"]) != name or not text(metadata["id"], empty=False):
        raise reject()
    for key in ("createdAt", "updatedAt"):
        text(metadata[key], empty=False)
    if metadata["lastUsedAt"] is not None:
        text(metadata["lastUsedAt"], empty=False)
    if (
        type(metadata["cookieCount"]) is not int
        or type(metadata["originCount"]) is not int
        or metadata["cookieCount"] != len(sequence(state["cookies"]))
        or metadata["originCount"] != len(sequence(state["origins"]))
    ):
        raise reject()
    diagnostics = mapping(metadata["diagnostics"], set(DIAGNOSTICS))
    if any(type(diagnostics[key]) is not int or diagnostics[key] != 0 for key in DIAGNOSTICS):
        raise reject()
