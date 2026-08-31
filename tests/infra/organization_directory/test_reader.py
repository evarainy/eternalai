from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from app.infra.organization_directory.importer import (
    build_directory_departments,
    build_directory_page,
)
from app.infra.organization_directory.postgresql import PostgreSQLOrganizationDirectory
from app.infra.organization_directory.reader import read_directory_snapshot
from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryError,
    OrganizationDirectoryPage,
    OrganizationDirectorySnapshot,
)

FETCHED_AT = datetime(2026, 8, 31, tzinfo=UTC)


def _departments() -> tuple[OrganizationDepartment, ...]:
    return build_directory_departments(
        [{"id": "dept-a", "pid": "", "name": "Synthetic department"}]
    )


def _user(user_id: str) -> dict[str, object]:
    return {
        "id": user_id,
        "departmentid": "dept-a",
        "orgid": "org-a",
        "subcompanyid1": "sub-a",
    }


def _page(
    current: int,
    *,
    next_page: int | None,
    is_end: bool,
    user_id: str,
) -> OrganizationDirectoryPage:
    return build_directory_page(
        current_page=current,
        next_page=next_page,
        is_end=is_end,
        user_rows=[_user(user_id)],
    )


class FakeDirectorySource:
    def __init__(
        self,
        *,
        counts: list[int],
        pages: dict[int, OrganizationDirectoryPage | Exception],
    ) -> None:
        self._counts = iter(counts)
        self._pages = pages
        self.requested_pages: list[int] = []

    async def fetch_departments(self) -> tuple[OrganizationDepartment, ...]:
        return _departments()

    async def fetch_authoritative_user_count(self) -> int:
        return next(self._counts)

    async def fetch_user_page(self, current_page: int) -> OrganizationDirectoryPage:
        self.requested_pages.append(current_page)
        result = self._pages[current_page]
        if isinstance(result, Exception):
            raise result
        return result


def _read(source: FakeDirectorySource) -> OrganizationDirectorySnapshot:
    return asyncio.run(read_directory_snapshot(source=source, fetched_at=FETCHED_AT))


def _assert_rejected_before_database_use(
    snapshot: OrganizationDirectorySnapshot,
) -> None:
    def forbidden_factory():
        raise AssertionError("incomplete snapshot must not access database")

    directory = PostgreSQLOrganizationDirectory(forbidden_factory)  # type: ignore[arg-type]
    with pytest.raises(OrganizationDirectoryError, match="incomplete"):
        asyncio.run(directory.replace_snapshot(snapshot))


def test_contiguous_pages_stable_counts_and_explicit_end_are_complete() -> None:
    source = FakeDirectorySource(
        counts=[2, 2],
        pages={
            1: _page(1, next_page=2, is_end=False, user_id="user-a"),
            2: _page(2, next_page=None, is_end=True, user_id="user-b"),
        },
    )

    snapshot = _read(source)

    assert snapshot.is_complete is True
    assert snapshot.returned_user_count == 2
    assert source.requested_pages == [1, 2]


def test_missing_page_is_incomplete_and_rejected() -> None:
    source = FakeDirectorySource(
        counts=[2, 2],
        pages={
            1: _page(1, next_page=3, is_end=False, user_id="user-a"),
            3: _page(3, next_page=None, is_end=True, user_id="user-b"),
        },
    )

    snapshot = _read(source)

    assert snapshot.is_complete is False
    assert source.requested_pages == [1, 3]
    _assert_rejected_before_database_use(snapshot)


def test_count_change_during_pagination_is_incomplete_and_rejected() -> None:
    source = FakeDirectorySource(
        counts=[1, 2],
        pages={1: _page(1, next_page=None, is_end=True, user_id="user-a")},
    )

    snapshot = _read(source)

    assert snapshot.authoritative_user_count_before == 1
    assert snapshot.authoritative_user_count_after == 2
    assert snapshot.is_complete is False
    _assert_rejected_before_database_use(snapshot)


def test_mid_pagination_failure_is_incomplete_and_rejected() -> None:
    source = FakeDirectorySource(
        counts=[1, 1],
        pages={
            1: _page(1, next_page=2, is_end=False, user_id="user-a"),
            2: TimeoutError("synthetic page failure"),
        },
    )

    snapshot = _read(source)

    assert snapshot.user_pages[-1].error_code == "page_fetch_failed"
    assert snapshot.is_complete is False
    _assert_rejected_before_database_use(snapshot)
