"""Pure Work Object search contract shared by API and persistence.

Collapse only the explicit 30 whitespace characters, then apply ordinary lower.
Special-script casing is not guaranteed equivalent across Python, JS and SQL.
Titles use substring matching; source references and assignees use equality.
"""

from __future__ import annotations

import re
from typing import TypeAlias

SearchQuery: TypeAlias = str | None
SEARCH_WHITESPACE = (
    "\u0009\u000a\u000b\u000c\u000d\u001c\u001d\u001e\u001f\u0020"
    "\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
SEARCH_WHITESPACE_PATTERN = f"[{SEARCH_WHITESPACE}]+"
_WHITESPACE = re.compile(SEARCH_WHITESPACE_PATTERN)


def normalize_search_value(value: str) -> str:
    """Normalize a field, preserving an empty result as an empty string."""
    return _WHITESPACE.sub(" ", value).strip(" ").lower()


def normalize_search_query(value: SearchQuery) -> SearchQuery:
    """None and values containing only contract whitespace mean no filtering."""
    return None if value is None else normalize_search_value(value) or None


__all__ = (
    "SEARCH_WHITESPACE",
    "SEARCH_WHITESPACE_PATTERN",
    "SearchQuery",
    "normalize_search_query",
    "normalize_search_value",
)
