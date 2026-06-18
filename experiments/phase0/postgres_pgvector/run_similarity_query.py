"""Run a deterministic pgvector similarity query for P0-SPIKE-003."""

from __future__ import annotations

import os
import sys

import psycopg

DOCUMENTS = [
    ("alpha", "[1,0,0]"),
    ("beta", "[0,1,0]"),
    ("gamma", "[0,0,1]"),
]


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL must be set")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def main() -> int:
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE pgvector_spike_documents RESTART IDENTITY")
            for label, vector_value in DOCUMENTS:
                cur.execute(
                    """
                    INSERT INTO pgvector_spike_documents (label, embedding)
                    VALUES (%s, %s::vector)
                    """,
                    (label, vector_value),
                )

            cur.execute(
                """
                SELECT label, embedding <-> %s::vector AS distance
                FROM pgvector_spike_documents
                ORDER BY embedding <-> %s::vector
                LIMIT 1
                """,
                ("[1,0,0]", "[1,0,0]"),
            )
            row = cur.fetchone()
        conn.commit()

    if row is None:
        print("similarity_query_failed: no rows returned", file=sys.stderr)
        return 1

    label, distance = row
    if label != "alpha" or float(distance) != 0.0:
        print(
            f"similarity_query_failed: expected alpha distance 0.0, got {label} {distance}",
            file=sys.stderr,
        )
        return 1

    print(f"similarity_query_ok: nearest={label} distance={distance}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
