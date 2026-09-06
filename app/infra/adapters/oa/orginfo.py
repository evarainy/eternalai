"""Bounded extraction of unit and department labels from OA ``result.orginfo``.

OA returns the organization placement as a short HTML ``<a>`` fragment: the
visible link text is the label, and the numeric id sits inside an ``onclick``
attribute (``javascript:viewSubCompany(<id>)`` / ``viewDepartment(<id>)``).
There is no flat field and no JSON sub-object, so the fragment has to be parsed.

Three properties matter more than convenience here, because this is the one
place where upstream-controlled markup enters the backend:

* **No new dependency.**  The standard-library :mod:`html.parser` gives exactly
  the two events we need (start-tag attributes and character data).  It builds
  no DOM, fetches nothing, expands no external entities and executes no script.
* **Everything is bounded.**  Input length, anchor count and label length all
  have hard ceilings, so a malformed or hostile fragment cannot turn into work.
* **Nothing raw escapes.**  The only values that leave this module are a plain
  text label (rejected if it still contains ``<`` or ``>`` after character-
  reference decoding) and a digit-only id.  The original markup never travels
  further, and ambiguity is answered with failure rather than a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

MAX_ORGINFO_LENGTH = 8192
MAX_ANCHOR_COUNT = 16
MAX_LABEL_LENGTH = 64

_UNIT_ONCLICK = re.compile(r"^\s*javascript:viewSubCompany\(\s*(\d{1,18})\s*\)\s*;?\s*$")
_DEPARTMENT_ONCLICK = re.compile(r"^\s*javascript:viewDepartment\(\s*(\d{1,18})\s*\)\s*;?\s*$")
_WHITESPACE_RUN = re.compile(r"[\s　]+")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")

_UNIT = "unit"
_DEPARTMENT = "department"


@dataclass(frozen=True, slots=True)
class OrgInfo:
    """One successfully parsed placement; the unit half is best-effort."""

    department_name: str
    department_id: str
    unit_name: str | None
    unit_id: str | None


class _OrgInfoParser(HTMLParser):
    """Single-pass anchor collector that refuses to guess on ambiguity."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.failed = False
        self.anchors: list[tuple[str, str, str]] = []
        self._kind: str | None = None
        self._identifier: str | None = None
        self._text: list[str] = []
        self._text_length = 0
        self._anchor_count = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if self.failed:
            return
        if self._kind is not None:
            # A nested element inside a collected anchor makes "which text is
            # the label" ambiguous. Refuse rather than pick one.
            self.failed = True
            return
        if tag != "a":
            return
        self._anchor_count += 1
        if self._anchor_count > MAX_ANCHOR_COUNT:
            self.failed = True
            return
        onclick: str | None = None
        seen_onclick = False
        for name, value in attrs:
            if name.lower() != "onclick":
                continue
            if seen_onclick:
                self.failed = True
                return
            seen_onclick = True
            onclick = value
        if onclick is None:
            return
        unit = _UNIT_ONCLICK.fullmatch(onclick)
        if unit is not None:
            self._kind = _UNIT
            self._identifier = unit.group(1)
        else:
            department = _DEPARTMENT_ONCLICK.fullmatch(onclick)
            if department is None:
                # Some other link in the fragment; ignore it, do not fail.
                return
            self._kind = _DEPARTMENT
            self._identifier = department.group(1)
        self._text = []
        self._text_length = 0

    def handle_endtag(self, tag: str) -> None:
        if self.failed or tag != "a" or self._kind is None:
            return
        self.anchors.append((self._kind, self._identifier or "", "".join(self._text)))
        self._kind = None
        self._identifier = None
        self._text = []
        self._text_length = 0

    def handle_data(self, data: str) -> None:
        if self.failed or self._kind is None:
            return
        self._text_length += len(data)
        if self._text_length > MAX_LABEL_LENGTH * 4:
            self.failed = True
            return
        self._text.append(data)


def normalize_label(raw: str) -> str | None:
    """Fold whitespace and reject anything that is not a plain short label."""

    text = _WHITESPACE_RUN.sub(" ", raw).strip()
    if not text or len(text) > MAX_LABEL_LENGTH:
        return None
    if _CONTROL_CHARACTER.search(text) is not None:
        return None
    if "<" in text or ">" in text:
        # Character references already decoded; a surviving angle bracket means
        # the fragment carried escaped markup. Show nothing rather than that.
        return None
    return text


def parse_orginfo(value: object) -> OrgInfo | None:
    """Return the placement, or ``None`` for any malformed or ambiguous input."""

    try:
        if not isinstance(value, str):
            return None
        if not value or len(value) > MAX_ORGINFO_LENGTH:
            return None
        parser = _OrgInfoParser()
        try:
            parser.feed(value)
            parser.close()
        except Exception:
            return None
        if parser.failed:
            return None
        # A trailing anchor that never closed is simply dropped: the parser
        # only appends on the closing tag, so no half-collected label survives.
        units: list[tuple[str, str]] = []
        departments: list[tuple[str, str]] = []
        for kind, identifier, raw_text in parser.anchors:
            label = normalize_label(raw_text)
            if label is None:
                return None
            bucket = units if kind == _UNIT else departments
            bucket.append((label, identifier))
        if len(departments) != 1:
            # Zero means we found nothing to show; more than one means a
            # multi-department shape we have no evidence for. Both fail closed.
            return None
        department_name, department_id = departments[0]
        unit_name, unit_id = units[0] if len(units) == 1 else (None, None)
        return OrgInfo(
            department_name=department_name,
            department_id=department_id,
            unit_name=unit_name,
            unit_id=unit_id,
        )
    except Exception:
        return None


__all__ = (
    "MAX_ANCHOR_COUNT",
    "MAX_LABEL_LENGTH",
    "MAX_ORGINFO_LENGTH",
    "OrgInfo",
    "normalize_label",
    "parse_orginfo",
)
