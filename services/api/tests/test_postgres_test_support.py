import pytest

from services.api.tests.postgres_test_support import get_required_test_database_url


def test_get_required_test_database_url_requires_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL must be set"):
        get_required_test_database_url()


def test_get_required_test_database_url_rejects_non_test_database_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://eternalai:eternalai@127.0.0.1:5432/eternalai")

    with pytest.raises(RuntimeError, match="must target a \\*_test database"):
        get_required_test_database_url()


def test_get_required_test_database_url_accepts_test_database_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://eternalai:eternalai@127.0.0.1:5432/eternalai_test")

    assert get_required_test_database_url() == "postgresql://eternalai:eternalai@127.0.0.1:5432/eternalai_test"
