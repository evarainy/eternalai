"""Verify schema artifacts created by the P0-SPIKE-003 Alembic migration."""

from __future__ import annotations

import os
import sys

import psycopg

TABLE_NAME = "pgvector_spike_documents"
INDEX_NAME = "ix_pgvector_spike_documents_embedding_hnsw"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL must be set")
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def main() -> int:
    with psycopg.connect(_database_url()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.pgvector_spike_documents')::text")
            table_row = cur.fetchone()

            cur.execute(
                """
                SELECT a.attname, format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute AS a
                JOIN pg_class AS c ON c.oid = a.attrelid
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relname = %s
                  AND a.attname = 'embedding'
                  AND NOT a.attisdropped
                """,
                (TABLE_NAME,),
            )
            column_row = cur.fetchone()

            cur.execute(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = %s
                  AND indexname = %s
                """,
                (TABLE_NAME, INDEX_NAME),
            )
            index_row = cur.fetchone()

    if table_row is None or table_row[0] not in {TABLE_NAME, f"public.{TABLE_NAME}"}:
        print("schema_failed: table public.pgvector_spike_documents is missing", file=sys.stderr)
        return 1

    if column_row is None or column_row[1] != "vector(3)":
        print(f"schema_failed: embedding vector(3) column missing, got {column_row}", file=sys.stderr)
        return 1

    if index_row is None:
        print("schema_failed: vector index is missing", file=sys.stderr)
        return 1

    index_name, index_def = index_row
    normalized_index_def = index_def.lower()
    if "using hnsw" not in normalized_index_def or "embedding" not in normalized_index_def:
        print(f"schema_failed: unexpected index definition: {index_def}", file=sys.stderr)
        return 1

    print(f"table_exists_ok: public.{TABLE_NAME}")
    print(f"vector_column_ok: {column_row[0]} {column_row[1]}")
    print(f"vector_index_ok: {index_name}")
    print(f"vector_index_definition: {index_def}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
