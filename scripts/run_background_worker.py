"""Start the background worker (``python -m app.worker``) with a whitelisted environment.

Usage (separate terminal): ``uv run python scripts/run_background_worker.py``.

Each ``WorkerSettings`` key is taken from the process environment first, then from the
repository root ``.env`` (same precedence and worktree resolution as
``scripts/check_dev_environment.py``); ``.env`` is opened only when a key is missing.
Only those keys plus ``SYSTEMROOT``/``PATH`` reach the worker, so no other secret enters
its environment. Only the database host, port and name are printed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from app.config import WORKER_ENVIRONMENT_KEYS  # noqa: E402
from app.db.config import normalize_database_url  # noqa: E402

_PLATFORM_KEYS = ("SYSTEMROOT", "PATH")
_DOTENV_ASSIGNMENT = re.compile(
    r"^(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$"
)


def _repository_env_path(repo_root: Path = REPO_ROOT) -> Path:
    git_marker = repo_root / ".git"
    if git_marker.is_dir() or not git_marker.is_file():
        return repo_root / ".env"
    marker = git_marker.read_text(encoding="utf-8").strip()
    prefix = "gitdir:"
    if not marker.casefold().startswith(prefix):
        return repo_root / ".env"
    git_dir = Path(marker[len(prefix) :].strip())
    if not git_dir.is_absolute():
        git_dir = (repo_root / git_dir).resolve()
    common_dir_file = git_dir / "commondir"
    if not common_dir_file.is_file():
        return repo_root / ".env"
    common_git_dir = (git_dir / common_dir_file.read_text(encoding="utf-8").strip()).resolve()
    return common_git_dir.parent / ".env"


def _dotenv_values(path: Path, names: set[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _DOTENV_ASSIGNMENT.fullmatch(line)
        if match is None or match.group("name") not in names:
            continue
        value = match.group("value").strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values.setdefault(match.group("name"), value)
    return values


def worker_environment() -> dict[str, str]:
    selected = {name: os.environ[name] for name in WORKER_ENVIRONMENT_KEYS if name in os.environ}
    missing = set(WORKER_ENVIRONMENT_KEYS) - selected.keys()
    if missing:
        selected.update(_dotenv_values(_repository_env_path(), missing))
    for name in _PLATFORM_KEYS:
        if name in os.environ:
            selected[name] = os.environ[name]
    return selected


def main() -> int:
    environment = worker_environment()
    database_url = environment.get("DATABASE_URL")
    if not database_url:
        print("background_worker_launch_failed code=database_url_missing", flush=True)
        return 2
    url = make_url(normalize_database_url(database_url))
    print(
        f"background_worker_launch database_host={url.host} database_port={url.port} "
        f"database_name={url.database}",
        flush=True,
    )
    process = subprocess.Popen([sys.executable, "-m", "app.worker"], cwd=REPO_ROOT, env=environment)
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            # Ctrl+C reaches the worker in the same console; wait for its graceful stop.
            continue


if __name__ == "__main__":
    raise SystemExit(main())
