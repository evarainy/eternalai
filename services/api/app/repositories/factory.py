from typing import Protocol

from services.api.app.repositories.base import SessionTaskRepository
from services.api.app.repositories.in_memory import InMemorySessionTaskRepository


class _RepositorySettings(Protocol):
    persistence_backend: str


def build_session_task_repository(settings: _RepositorySettings) -> SessionTaskRepository:
    if settings.persistence_backend == "memory":
        return InMemorySessionTaskRepository()
    raise ValueError(f"Unsupported persistence backend: {settings.persistence_backend}")
