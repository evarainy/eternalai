"""Reset the fixed local test database and reapply the latest Alembic schema."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

EXPECTED_DATABASE = "eternalai_test"
EXPECTED_PORT = 15432
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
REPO_ROOT = Path(__file__).resolve().parents[1]


class UnsafeTestDatabaseUrlError(ValueError):
    """Raised before a reset when the supplied URL is not the fixed local test DB."""


def validate_test_database_url(database_url: str) -> str:
    """Return a URL only when it targets the exact fixed local test database.

    No connection is opened here. Query parameters are rejected so they cannot override
    the parsed network target through driver-specific connection options.
    """

    try:
        parsed_url = make_url(database_url)
    except (ArgumentError, TypeError, ValueError) as error:
        raise UnsafeTestDatabaseUrlError("DATABASE_URL is not a valid SQLAlchemy URL") from error

    if parsed_url.get_backend_name() != "postgresql":
        raise UnsafeTestDatabaseUrlError("DATABASE_URL must use a PostgreSQL backend")
    if parsed_url.database != EXPECTED_DATABASE:
        raise UnsafeTestDatabaseUrlError(
            f"Refusing to reset database {parsed_url.database!r}; expected {EXPECTED_DATABASE!r}"
        )
    if parsed_url.host not in ALLOWED_HOSTS:
        raise UnsafeTestDatabaseUrlError(
            "Refusing to reset a non-local database host "
            f"{parsed_url.host!r}; allowed hosts are 127.0.0.1 and localhost"
        )
    if parsed_url.port != EXPECTED_PORT:
        raise UnsafeTestDatabaseUrlError(
            f"Refusing to reset database port {parsed_url.port!r}; expected {EXPECTED_PORT}"
        )
    if parsed_url.query:
        raise UnsafeTestDatabaseUrlError(
            "Refusing DATABASE_URL query parameters; they can override connection targets"
        )

    return database_url


def reset_public_schema(database_url: str) -> None:
    """Drop all test data by recreating the public schema."""

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def upgrade_schema(database_url: str) -> None:
    """Apply the repository's latest Alembic migrations to the reset database."""

    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        check=True,
        cwd=REPO_ROOT,
        env=environment,
    )


def reset_test_database(
    database_url: str,
    *,
    schema_reset: Callable[[str], None] = reset_public_schema,
    schema_upgrade: Callable[[str], None] = upgrade_schema,
) -> None:
    """Safely reset the fixed test database, then rebuild its schema."""

    safe_database_url = validate_test_database_url(database_url)
    schema_reset(safe_database_url)
    schema_upgrade(safe_database_url)


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("Refusing to reset: DATABASE_URL must be set.", file=sys.stderr)
        return 2

    try:
        reset_test_database(database_url)
    except UnsafeTestDatabaseUrlError as error:
        print(f"Refusing to reset test database: {error}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as error:
        print(f"Alembic upgrade failed with exit code {error.returncode}.", file=sys.stderr)
        return error.returncode or 1

    print("Fixed local test database reset and migrated to head.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
