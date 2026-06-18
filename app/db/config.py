from __future__ import annotations

import os
from collections.abc import Mapping

POSTGRESQL_SCHEME = "postgresql" + "://"
PSYCOPG_SCHEME = "postgresql+psycopg" + "://"


def normalize_database_url(database_url: str) -> str:
    """Return a SQLAlchemy URL that uses the approved psycopg driver."""
    candidate = database_url.strip()
    if not candidate:
        raise RuntimeError("DATABASE_URL must not be empty")
    if candidate.startswith(POSTGRESQL_SCHEME):
        return PSYCOPG_SCHEME + candidate[len(POSTGRESQL_SCHEME) :]
    return candidate


def get_database_url(environment: Mapping[str, str] | None = None) -> str:
    source = os.environ if environment is None else environment
    value = source.get("DATABASE_URL")
    if value is None or not value.strip():
        raise RuntimeError("DATABASE_URL is required")
    return normalize_database_url(value)
