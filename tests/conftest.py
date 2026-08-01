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


@pytest.fixture(autouse=True)
def _declare_testing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the test-only environment exception explicit for every test."""
    monkeypatch.setenv("ENV", "testing")
    monkeypatch.delenv("PHASE0_MOCK_MODE", raising=False)
