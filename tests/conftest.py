from __future__ import annotations

import base64
import os
import re
from pathlib import Path

import pytest

_TEST_KEY_B64 = base64.b64encode(b"test-only-key-material-32-bytes!").decode("ascii")
_DOTENV_ASSIGNMENT = re.compile(
    r"^(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$"
)

# Pytest imports application modules during collection, before fixtures run.
# These values are inert test configuration: all upstream network calls remain
# replaced by fixtures, while the module-level app still builds every dependency.
os.environ["ENV"] = "testing"
os.environ["REDIS_URL"] = "redis://redis.invalid:6379/0"
os.environ["OA_SOURCE_PROFILE_ID"] = "synthetic-oa"
os.environ["OA_TENANT_ID"] = "default"
os.environ["OA_BASE_URL"] = "https://oa.invalid"
os.environ["OA_CREDENTIAL_TTL_S"] = "3600"
os.environ["SESSION_COOKIE_TTL_S"] = "3600"
os.environ["CSRF_ALLOWED_ORIGINS"] = "https://testserver"
os.environ["LLM_BASE_URL"] = "https://vllm.invalid/v1"
os.environ["LLM_MODEL"] = "qwen3.5-27b"
os.environ["ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64"] = _TEST_KEY_B64
os.environ["ETERNALAI_IDENTITY_HMAC_KEY_B64"] = _TEST_KEY_B64
os.environ["ETERNALAI_SESSION_SIGNING_KEY_B64"] = _TEST_KEY_B64
os.environ["ETERNALAI_SESSION_BINDING_KEY_B64"] = _TEST_KEY_B64


def _repository_env_path() -> Path:
    worktree_root = Path(__file__).resolve().parents[1]
    git_marker = worktree_root / ".git"
    if git_marker.is_dir():
        return worktree_root / ".env"
    if not git_marker.is_file():
        return worktree_root / ".env"
    marker = git_marker.read_text(encoding="utf-8").strip()
    prefix = "gitdir:"
    if not marker.casefold().startswith(prefix):
        return worktree_root / ".env"
    git_dir = Path(marker[len(prefix) :].strip())
    if not git_dir.is_absolute():
        git_dir = (worktree_root / git_dir).resolve()
    common_dir_file = git_dir / "commondir"
    if not common_dir_file.is_file():
        return worktree_root / ".env"
    common_git_dir = (
        git_dir / common_dir_file.read_text(encoding="utf-8").strip()
    ).resolve()
    return common_git_dir.parent / ".env"


def _load_missing_environment(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _DOTENV_ASSIGNMENT.fullmatch(line)
        if match is None:
            continue
        name = match.group("name")
        value = match.group("value").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


_load_missing_environment(_repository_env_path())


def _route_xdist_worker_database() -> None:
    """Point each CI pytest-xdist worker at its own pre-migrated database.

    CI creates and migrates ``eternalai_test_gw<N>`` before pytest starts. This runs at
    import time, before application modules snapshot DATABASE_URL, and only inside
    xdist workers; single-process runs are untouched. The DSN is never printed.
    """
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if worker is None:
        return
    raw_url = os.environ.get("DATABASE_URL")
    url = None
    if raw_url is not None and re.fullmatch(r"gw[0-9]+", worker):
        try:
            url = make_url(raw_url)
        except (ArgumentError, ValueError):
            url = None
    if (
        url is None
        or url.get_backend_name() != "postgresql"
        or (url.host, url.port, url.database) != ("127.0.0.1", 15432, "eternalai_test")
        or url.query
    ):
        raise RuntimeError("xdist_worker_database_target_invalid") from None
    os.environ["DATABASE_URL"] = url.set(database=f"eternalai_test_{worker}").render_as_string(
        hide_password=False
    )


_route_xdist_worker_database()


@pytest.fixture(scope="module")
def migrated_database_url() -> str:
    """Prepare real DB tests with the existing Alembic upgrade-to-head path."""
    from alembic.config import Config
    from sqlalchemy.engine import make_url

    from alembic import command
    from app.db.config import normalize_database_url

    database_url = os.environ.get("DATABASE_URL")
    assert database_url, "DATABASE_URL must be set by the test runner environment"
    url = make_url(normalize_database_url(database_url))
    assert url.host == "127.0.0.1" and url.port == 15432, (
        "DB tests require PostgreSQL at 127.0.0.1:15432"
    )
    repo_root = Path(__file__).resolve().parents[1]
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "alembic"))
    command.upgrade(config, "head")
    return database_url


def pytest_configure(config: pytest.Config) -> None:
    """Install the test-only Runtime schema observer after test env setup."""

    from tests.architecture import runtime_schema_observer

    plugin_name = "p4-runtime-schema-observer"
    if config.pluginmanager.hasplugin(plugin_name):
        return
    runtime_schema_observer.pytest_configure(config)
    config.pluginmanager.register(runtime_schema_observer, plugin_name)


@pytest.fixture(autouse=True)
def _declare_testing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the test-only environment exception explicit for every test."""
    monkeypatch.setenv("ENV", "testing")
    monkeypatch.delenv("PHASE0_MOCK_MODE", raising=False)
