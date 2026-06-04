"""Task store persistence errors."""

from __future__ import annotations


class TaskNotFoundError(RuntimeError):
    """Raised when a task_id is not found in the store."""


class DuplicateTaskError(RuntimeError):
    """Raised when create_task is called with a task_id that already exists."""
