"""Execute fail-closed paginated reads for the OA organization directory."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.infra.organization_directory.importer import build_directory_snapshot
from app.ports.organization_directory import (
    OrganizationDirectoryError,
    OrganizationDirectoryPage,
    OrganizationDirectorySnapshot,
    OrganizationDirectorySourcePort,
)


async def read_directory_snapshot(
    *,
    source: OrganizationDirectorySourcePort,
    fetched_at: datetime,
) -> OrganizationDirectorySnapshot:
    """Read every user page and preserve enough evidence to prove completeness."""
    try:
        departments = await source.fetch_departments()
        count_before = await source.fetch_authoritative_user_count()
    except Exception:
        raise OrganizationDirectoryError("organization directory read failed") from None
    if (
        not isinstance(count_before, int)
        or isinstance(count_before, bool)
        or count_before < 0
    ):
        raise OrganizationDirectoryError("invalid authoritative directory count")

    pages: list[OrganizationDirectoryPage] = []
    requested_page = 1
    requested_pages: set[int] = set()
    maximum_page_attempts = count_before + 1
    while (
        requested_page not in requested_pages
        and len(pages) < maximum_page_attempts
    ):
        requested_pages.add(requested_page)
        try:
            page = await source.fetch_user_page(requested_page)
        except Exception:
            pages.append(
                OrganizationDirectoryPage(
                    current_page=requested_page,
                    next_page=None,
                    is_end=False,
                    memberships=(),
                    error_code="page_fetch_failed",
                )
            )
            break
        pages.append(page)
        if page.error_code is not None or page.is_end or page.next_page is None:
            break
        requested_page = page.next_page

    count_error_code: Literal["authoritative_count_after_failed"] | None = None
    try:
        count_after = await source.fetch_authoritative_user_count()
    except Exception:
        count_after = None
        count_error_code = "authoritative_count_after_failed"

    return build_directory_snapshot(
        departments=departments,
        user_pages=pages,
        authoritative_user_count_before=count_before,
        authoritative_user_count_after=count_after,
        count_error_code=count_error_code,
        fetched_at=fetched_at,
    )


__all__ = ("read_directory_snapshot",)
