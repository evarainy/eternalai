from __future__ import annotations

import pytest

from scripts.reset_test_db import UnsafeTestDatabaseUrlError, reset_test_database

SAFE_DATABASE_URL = (
    "postgresql+psycopg://eternalai:change_me_test@127.0.0.1:15432/eternalai_test"
)


@pytest.mark.parametrize(
    "unsafe_database_url",
    [
        "postgresql+psycopg://eternalai:change_me_test@127.0.0.1:15432/eternalai",
        "postgresql+psycopg://eternalai:change_me_test@db.example.test:15432/eternalai_test",
        "postgresql+psycopg://eternalai:change_me_test@localhost.evil:15432/eternalai_test",
        "postgresql+psycopg://eternalai:change_me_test@127.0.0.1:15432/eternalai_test?host=db.example.test",
        "postgresql+psycopg://eternalai:change_me_test@127.0.0.1:55432/eternalai_test",
        "not a database URL",
    ],
)
def test_reset_rejects_unsafe_url_before_any_ddl(unsafe_database_url: str) -> None:
    ddl_calls: list[str] = []
    migration_calls: list[str] = []

    with pytest.raises(UnsafeTestDatabaseUrlError):
        reset_test_database(
            unsafe_database_url,
            schema_reset=ddl_calls.append,
            schema_upgrade=migration_calls.append,
        )

    assert ddl_calls == []
    assert migration_calls == []


def test_reset_passes_only_the_validated_url_to_ddl_and_migrations() -> None:
    ddl_calls: list[str] = []
    migration_calls: list[str] = []

    reset_test_database(
        SAFE_DATABASE_URL,
        schema_reset=ddl_calls.append,
        schema_upgrade=migration_calls.append,
    )

    assert ddl_calls == [SAFE_DATABASE_URL]
    assert migration_calls == [SAFE_DATABASE_URL]
