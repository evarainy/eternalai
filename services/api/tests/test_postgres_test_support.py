import pytest

from services.api.tests.postgres_test_support import (
    DEFAULT_TEST_DATABASE_URL,
    validate_test_database_url,
)


def test_validate_test_database_url_accepts_default_test_database_url() -> None:
    assert validate_test_database_url(DEFAULT_TEST_DATABASE_URL) == DEFAULT_TEST_DATABASE_URL


def test_validate_test_database_url_rejects_non_test_database_name() -> None:
    database_url = "postgresql://eternalai:eternalai@127.0.0.1:5432/eternalai"

    with pytest.raises(RuntimeError, match="must target a \\*_test database"):
        validate_test_database_url(database_url)


def test_validate_test_database_url_accepts_test_database_name() -> None:
    database_url = "postgresql://eternalai:eternalai@127.0.0.1:5432/eternalai_test"

    assert validate_test_database_url(database_url) == database_url
