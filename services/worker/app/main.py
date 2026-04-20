import json
import logging
from functools import lru_cache

from services.runtime_config import WorkerServiceSettings


class WorkerSettings(WorkerServiceSettings):
    """Backward-compatible worker settings alias with shared runtime defaults."""


@lru_cache
def get_settings() -> WorkerSettings:
    return WorkerSettings()


def bootstrap_worker() -> dict[str, str]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    logging.getLogger("eternalai.worker").info("Worker bootstrap initialized")
    return {
        "status": "ok",
        "service": "worker",
        "queue": settings.worker_queue,
    }


def main() -> None:
    state = bootstrap_worker()
    print(json.dumps(state))


if __name__ == "__main__":
    main()
