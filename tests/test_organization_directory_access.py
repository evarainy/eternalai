from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.organization_directory_access import (
    DirectoryAccessError,
    check_freshness,
    read_fresh_directory_view,
)
from app.ports.organization_directory import OrganizationDirectoryReadView

NOW = datetime(2026, 9, 14, tzinfo=UTC)


def test_freshness_exact_threshold_missing_and_future() -> None:
    for age in (172799, 172800):
        check_freshness(1, NOW - timedelta(seconds=age), NOW, NOW, 172800)
    for version, source, success, code in (
        (1, NOW - timedelta(seconds=172801), NOW, "organization_directory_stale"),
        (0, None, None, "organization_directory_missing"),
        (1, None, NOW, "organization_directory_unavailable"),
        (1, NOW, None, "organization_directory_unavailable"),
        (1, NOW + timedelta(seconds=1), NOW, "organization_directory_unavailable"),
        (1, NOW, NOW + timedelta(seconds=1), "organization_directory_unavailable"),
        (0, NOW, NOW, "organization_directory_unavailable"),
        (1, NOW.replace(tzinfo=None), NOW, "organization_directory_unavailable"),
    ):
        with pytest.raises(DirectoryAccessError) as error:
            check_freshness(version, source, success, NOW, 172800)
        assert error.value.code == code


def test_query_elapsed_time_is_counted_and_view_is_never_refetched() -> None:
    ticks = [0.0]

    class Directory:
        calls = 0

        async def read_view(self):
            self.calls += 1
            ticks[0] = 1.0
            return OrganizationDirectoryReadView(
                snapshot_version=1,
                source_fetched_at=NOW - timedelta(seconds=172799),
                last_success_at=NOW,
                observed_at=NOW,
                departments=(),
                memberships=(),
            )

    directory = Directory()
    view = asyncio.run(read_fresh_directory_view(directory, monotonic=lambda: ticks[0]))
    view.recheck()
    ticks[0] = 1.001
    with pytest.raises(DirectoryAccessError) as error:
        view.recheck()
    assert error.value.code == "organization_directory_stale"
    assert directory.calls == 1
