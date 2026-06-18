"""Assert pgvector extension version for the P0-SPIKE-003 experiment."""

from __future__ import annotations

import os
import re
import sys

import psycopg

MINIMUM_VERSION = (0, 8, 2)


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL must be set")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _parse_version(value: str) -> tuple[int, int, int]:
    parts = [int(part) for part in re.findall(r"\d+", value)[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def main() -> int:
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            row = cur.fetchone()

    if row is None:
        print("pgvector_extversion_missing: vector extension is not installed", file=sys.stderr)
        return 1

    extversion = row[0]
    parsed = _parse_version(extversion)
    if parsed < MINIMUM_VERSION:
        print(
            f"pgvector_extversion_failed: {extversion} < 0.8.2",
            file=sys.stderr,
        )
        return 1

    print(f"pgvector_extversion_ok: {extversion} >= 0.8.2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
