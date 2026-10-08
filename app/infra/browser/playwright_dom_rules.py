"""Trusted private DOM mappings. Never parsed from a Skill, model, or request."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from app.browser_skill.models import ObservationRequest


class DOMBatchError(ValueError):
    """Closed private failure classification, without DOM or transport text."""

    def __init__(self, code: Literal["stale", "denied", "invalid_response"]) -> None:
        self.code = code
        super().__init__(code)


_IDENTITY_BATCH = """(root, config) => {
  // eternalai.identity_batch.v1: actual Nodes in one live execution context.
  const previous = config.previous, current = config.current;
  if (!root.isConnected || current.some(node => !node || !node.isConnected))
    return {status: 'stale', same_region: false, indices: []};
  if (new Set(current).size !== current.length)
    return {status: 'invalid_response', same_region: false, indices: []};
  const same = config.region !== null && config.region === root && config.region.isConnected;
  const positions = new Map();
  if (same) {
    for (let i = 0; i < previous.length; i++) {
      const node = previous[i];
      if (positions.has(node))
        return {status: 'invalid_response', same_region: false, indices: []};
      if (node && node.isConnected) positions.set(node, i);
    }
  }
  const indices = current.map(node => {
    const i = positions.get(node);
    return i !== undefined && previous[i] === node && previous[i].isConnected ? i : -1;
  });
  return {status: 'ok', same_region: same, indices};
}"""


async def match_actual_nodes(
    root: Any, previous_region: Any | None,
    previous: tuple[Any, ...], current: tuple[Any, ...], *, maximum: int = 255,
) -> tuple[bool, tuple[int, ...]]:
    """Borrow every handle; own only the private value packet, never a Node ID."""
    if type(maximum) is not int or not 0 <= maximum <= 255 or (
        len(previous) > maximum or len(current) > maximum
    ):
        raise DOMBatchError("invalid_response")
    packet = await root.evaluate(_IDENTITY_BATCH, {
        "region": previous_region, "previous": list(previous), "current": list(current),
    })
    if (type(packet) is not dict or set(packet) != {"status", "same_region", "indices"}
            or type(packet["status"]) is not str or type(packet["same_region"]) is not bool
            or type(packet["indices"]) is not list):
        raise DOMBatchError("invalid_response")
    status, indices = packet["status"], packet["indices"]
    if status in {"stale", "invalid_response"}:
        if packet["same_region"] is not False or indices:
            raise DOMBatchError("invalid_response")
        raise DOMBatchError("stale" if status == "stale" else "invalid_response")
    if (status != "ok" or len(indices) != len(current)
            or any(type(i) is not int or not -1 <= i < len(previous) for i in indices)
            or len({i for i in indices if i >= 0}) != sum(i >= 0 for i in indices)
            or (not packet["same_region"] and any(i != -1 for i in indices))):
        raise DOMBatchError("invalid_response")
    return packet["same_region"], tuple(indices)


@dataclass(frozen=True, slots=True, repr=False)
class DOMValue:
    selector: str
    kind: Literal["text", "value", "attribute"] = "text"
    attribute: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.selector.strip()
            or len(self.selector) > 512
            or self.kind not in {"text", "value", "attribute"}
            or ((self.kind == "attribute") != (self.attribute is not None))
            or (self.attribute is not None and not self.attribute.isidentifier())
        ):
            raise ValueError("browser_dom_value_registration_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class DOMStep:
    step_id: str
    selector: str
    row_selector: str | None = None
    key: DOMValue | None = None

    def __post_init__(self) -> None:
        if (
            not self.step_id
            or not self.selector.strip()
            or len(self.selector) > 512
            or ((self.row_selector is None) != (self.key is None))
            or (
                self.row_selector is not None
                and (not self.row_selector.strip() or len(self.row_selector) > 512)
            )
        ):
            raise ValueError("browser_dom_step_registration_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class DOMRead:
    observation: ObservationRequest
    row_selector: str
    key: DOMValue
    tenant: DOMValue
    user: DOMValue
    fields: tuple[tuple[str, DOMValue], ...]
    maximum_rows: int = 128
    maximum_value_bytes: int = 4096
    object_type: DOMValue | None = None

    def __post_init__(self) -> None:
        if (
            not self.row_selector.strip()
            or len(self.row_selector) > 512
            or self.observation.expected_scope is not None
            or not 1 <= self.maximum_rows <= 255
            or not 1 <= self.maximum_value_bytes <= 16384
            or not 1 <= len(self.fields) <= 32
            or len({name for name, _ in self.fields}) != len(self.fields)
        ):
            raise ValueError("browser_dom_read_registration_invalid")


@dataclass(frozen=True, slots=True, repr=False)
class RegisteredDOMRules:
    skill_digest: str
    site_digest: str
    verifier_digest: str
    steps: tuple[DOMStep, ...]
    read: DOMRead

    def __post_init__(self) -> None:
        if (
            any(
                len(digest) != 64
                for digest in (self.skill_digest, self.site_digest, self.verifier_digest)
            )
            or not self.steps
            or len({step.step_id for step in self.steps}) != len(self.steps)
        ):
            raise ValueError("browser_dom_registration_invalid")


# Limits are applied before transferring private data out of the browser process.
_READ = """(root, config) => {
  if (!root.isConnected) throw new Error('detached');
  const nodes = root.querySelectorAll(config.selector);
  if (nodes.length !== 1) return null;
  const node = nodes[0];
  const value = config.kind === 'text' ? node.textContent :
    config.kind === 'value' ? node.value : node.getAttribute(config.attribute);
  if (typeof value !== 'string') return null;
  if (new TextEncoder().encode(value).length > config.limit) throw new Error('bound');
  return value;
}"""


async def read_private(root: Any, value: DOMValue, limit: int) -> str | None:
    """Only adapter comparison code may consume this result; exceptions are sanitized there."""
    result = await root.evaluate(
        _READ,
        {
            "selector": value.selector,
            "kind": value.kind,
            "attribute": value.attribute,
            "limit": limit,
        },
    )
    if result is None:
        return None
    if type(result) is not str or len(result.encode("utf-8")) > limit:
        raise ValueError("browser_dom_read_bound")
    return result


_READ_MANY = """(root, config) => {
  if (!root.isConnected) throw new Error('detached');
  let bytes = 0;
  return config.fields.map(field => {
    const nodes = root.querySelectorAll(field.selector);
    if (nodes.length !== 1) return null;
    const node = nodes[0];
    const value = field.kind === 'text' ? node.textContent :
      field.kind === 'value' ? node.value : node.getAttribute(field.attribute);
    if (typeof value !== 'string') return null;
    const size = new TextEncoder().encode(value).length;
    bytes += size;
    if (size > config.limit || bytes > config.limit * config.fields.length)
      throw new Error('bound');
    return value;
  });
}"""


async def read_private_many(
    root: Any, values: tuple[DOMValue, ...], limit: int,
) -> tuple[str | None, ...]:
    """One bounded round of registered fields; never a cached verifier read."""
    if not 1 <= len(values) <= 32:
        raise ValueError("browser_dom_field_bound")
    result = await root.evaluate(
        _READ_MANY,
        {"fields": [
            {"selector": field.selector, "kind": field.kind, "attribute": field.attribute}
            for field in values
        ], "limit": limit},
    )
    if not isinstance(result, list) or len(result) != len(values):
        raise ValueError("browser_dom_read_bound")
    if any(value is not None and (
        type(value) is not str or len(value.encode("utf-8")) > limit
    ) for value in result):
        raise ValueError("browser_dom_read_bound")
    return tuple(result)


_FILTER_BATCH = """(region, config) => {
  // eternalai.filter_batch.v1: ordered private packets, never partial success.
  const unique = new Set(config.nodes);
  return config.nodes.map((node, index) => {
    const fatal = code => ({index, state: 'fatal', value: null, code});
    try {
      if (unique.size !== config.nodes.length || !region.isConnected ||
          !node || !node.isConnected) return fatal('invalid_response');
      if (!region.contains(node)) return fatal('denied');
      if (!node.matches(config.selector))
        return {index, state: 'no_match', value: null, code: null};
      let value = null;
      if (config.key !== null) {
        const row = node.closest(config.row_selector);
        if (!row || !region.contains(row)) return fatal('denied');
        if (!row.isConnected) return fatal('invalid_response');
        const field = config.key, fields = row.querySelectorAll(field.selector);
        if (fields.length === 1) {
          const item = fields[0];
          const raw = field.kind === 'text' ? item.textContent :
            field.kind === 'value' ? item.value : item.getAttribute(field.attribute);
          if (typeof raw === 'string') {
            if (new TextEncoder().encode(raw).length > config.limit)
              return fatal('invalid_response');
            value = raw;
          }
        }
      }
      return {index, state: 'match', value, code: null};
    } catch (_) { return fatal('invalid_response'); }
  });
}"""


async def filter_private_candidates(
    region: Any, nodes: tuple[Any, ...], selector: str,
    row_selector: str | None, key: DOMValue | None, limit: int,
) -> tuple[tuple[bool, str | None], ...]:
    """One fixed selector/ancestor/key round; sealed comparison stays in Python."""
    if len(nodes) > 255 or type(limit) is not int or not 1 <= limit <= 16384:
        raise DOMBatchError("invalid_response")
    if not nodes:
        return ()
    packet = await region.evaluate(_FILTER_BATCH, {
        "nodes": list(nodes), "selector": selector, "row_selector": row_selector,
        "key": (None if key is None else {
            "selector": key.selector, "kind": key.kind, "attribute": key.attribute,
        }), "limit": limit,
    })
    if type(packet) is not list or len(packet) != len(nodes):
        raise DOMBatchError("invalid_response")
    results: list[tuple[bool, str | None]] = []
    fatal: Literal["denied", "invalid_response"] | None = None
    for index, item in enumerate(packet):
        if (type(item) is not dict or set(item) != {"index", "state", "value", "code"}
                or type(item["index"]) is not int or item["index"] != index
                or type(item["state"]) is not str):
            raise DOMBatchError("invalid_response")
        state, value, code = item["state"], item["value"], item["code"]
        if state == "fatal":
            if value is not None or type(code) is not str or code not in {"denied", "invalid_response"}:
                raise DOMBatchError("invalid_response")
            if fatal is None:
                fatal = "denied" if code == "denied" else "invalid_response"
        elif (state not in {"match", "no_match"} or code is not None
              or (state == "no_match" and value is not None)
              or (value is not None and (type(value) is not str
                                         or len(value.encode("utf-8")) > limit))):
            raise DOMBatchError("invalid_response")
        results.append((state == "match", value))
    if fatal is not None:
        raise DOMBatchError(fatal)
    return tuple(results)


ROW_IDENTITY_MATCH = """(region, node, row, config, strict) => {
  // eternalai.row_identity.v1: preserve conditional key -> owner -> type access.
  if (!row || !region.contains(row)) return false;
  if (config.selector && !node.matches(config.selector)) return false;
  if (node.closest(config.row_selector) !== row) return false;
  const read = field => {
    if (!row.isConnected) {
      if (strict) throw new Error('detached');
      return null;
    }
    const nodes = row.querySelectorAll(field.selector);
    if (nodes.length !== 1) return null;
    const item = nodes[0];
    const value = field.kind === 'text' ? item.textContent :
      field.kind === 'value' ? item.value : item.getAttribute(field.attribute);
    if (typeof value !== 'string') return null;
    if (new TextEncoder().encode(value).length > field.limit) {
      if (strict) throw new Error('bound');
      return null;
    }
    return value;
  };
  const fields = config.fields;
  if (!fields.length || read(fields[0]) !== fields[0].expected) return false;
  if (fields.length >= 3) {
    const tenant = read(fields[1]), user = read(fields[2]);
    if (tenant !== fields[1].expected || user !== fields[2].expected) return false;
  }
  return fields.length < 4 || read(fields[3]) === fields[3].expected;
}"""


_ROW_IDENTITY_EVALUATE = (
    "(region, config) => { const matches = " + ROW_IDENTITY_MATCH
    + "; return matches(region, config.node, config.row, config, true); }"
)


async def private_row_identity(
    region: Any, node: Any, row: Any, config: dict[str, Any],
) -> bool:
    """Borrow all handles; apply the same fixed identity rule as the final barrier."""
    if len(config["fields"]) not in {1, 3, 4}:
        raise DOMBatchError("invalid_response")
    result = await region.evaluate(
        _ROW_IDENTITY_EVALUATE,
        {**config, "node": node, "row": row},
    )
    if type(result) is not bool:
        raise DOMBatchError("invalid_response")
    return result


async def bounded_children(root: Any, selector: str, maximum: int) -> list[Any]:
    count = await root.evaluate(
        "(root, selector) => root.querySelectorAll(selector).length", selector
    )
    if type(count) is not int or not 0 <= count <= maximum:
        raise ValueError("browser_dom_children_bound")
    children = await root.query_selector_all(selector)
    if len(children) != count or len(children) > maximum:
        for child in children:
            try:
                await child.dispose()
            except (asyncio.CancelledError, Exception):
                # Preserve the already detected snapshot failure while releasing
                # the rest of this function's newly allocated handles.
                pass
        raise ValueError("browser_dom_children_changed")
    return list(children)
