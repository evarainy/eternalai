from urllib.parse import urlsplit


DEFAULT_TEST_DATABASE_URL = "postgresql://eternalai:eternalai@127.0.0.1:5432/eternalai_test"


def validate_test_database_url(database_url: str) -> str:
    database_name = urlsplit(database_url).path.removeprefix("/")
    if not database_name.endswith("_test"):
        raise RuntimeError(
            f"Refusing to run destructive Postgres tests against database '{database_name}'. TEST_DATABASE_URL must target a *_test database."
        )

    return database_url
