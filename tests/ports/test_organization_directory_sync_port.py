from __future__ import annotations

import inspect
from typing import get_type_hints

from app.ports.organization_directory_sync import (
    DirectorySourceError,
    OrganizationDirectorySyncLease,
    OrganizationDirectorySyncPort,
)


def test_source_error_codes_cannot_echo_unknown_input():
    error = DirectorySourceError("synthetic-private-error")
    assert error.code == "source_unavailable"
    assert str(error) == "source_unavailable"


def test_sync_lease_is_pinned_and_acquisition_is_an_async_context_manager():
    assert set(
        name for name in vars(OrganizationDirectorySyncPort) if not name.startswith("_")
    ) == {
        "read_status",
        "try_acquire",
    }
    assert not inspect.iscoroutinefunction(OrganizationDirectorySyncPort.try_acquire)
    assert "AsyncContextManager" in str(
        get_type_hints(OrganizationDirectorySyncPort.try_acquire)["return"]
    )
    assert set(
        name for name in vars(OrganizationDirectorySyncLease) if not name.startswith("_")
    ) == {
        "read_status",
        "start_attempt",
        "replace_snapshot",
        "mark_failed",
    }
    for name in ("read_status", "start_attempt", "replace_snapshot", "mark_failed"):
        assert inspect.iscoroutinefunction(getattr(OrganizationDirectorySyncLease, name))
