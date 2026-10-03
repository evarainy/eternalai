"""Trusted private DOM mappings. Never parsed from a Skill, model, or request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.browser_skill.models import ObservationRequest


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


async def bounded_children(root: Any, selector: str, maximum: int) -> list[Any]:
    count = await root.evaluate(
        "(root, selector) => root.querySelectorAll(selector).length", selector
    )
    if type(count) is not int or not 0 <= count <= maximum:
        raise ValueError("browser_dom_children_bound")
    children = await root.query_selector_all(selector)
    if len(children) != count or len(children) > maximum:
        for child in children:
            await child.dispose()
        raise ValueError("browser_dom_children_changed")
    return list(children)
