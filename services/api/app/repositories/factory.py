from typing import Protocol

from services.api.app.persistence.db import build_engine
from services.api.app.repositories.base import SessionTaskRepository
from services.api.app.repositories.in_memory import InMemorySessionTaskRepository
from services.api.app.repositories.postgres import PostgresSessionTaskRepository


class _RepositorySettings(Protocol):
    database_url: str
    persistence_backend: str


def build_session_task_repository(settings: _RepositorySettings) -> SessionTaskRepository:
    if settings.persistence_backend == "memory":
        return InMemorySessionTaskRepository()
    if settings.persistence_backend == "postgres":
        return PostgresSessionTaskRepository(build_engine(settings.database_url))
    raise ValueError(f"Unsupported persistence backend: {settings.persistence_backend}")
