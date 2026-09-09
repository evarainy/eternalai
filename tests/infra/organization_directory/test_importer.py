from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.infra.organization_directory.importer import (
    build_directory_departments,
    build_directory_page,
    build_directory_snapshot,
)
from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryError,
    OrganizationDirectoryPage,
)

FETCHED_AT = datetime(2026, 8, 31, tzinfo=UTC)


@pytest.mark.parametrize("job_title", [None, "75", 380])
def test_projects_raw_jobtitle_without_rendered_label(job_title: str | int | None) -> None:
    page = _page(user_rows=[_user(jobtitle=job_title, jobtitlespan="<span>manager</span>")])
    assert page.memberships[0].job_title == (str(job_title) if job_title is not None else None)
    assert "jobtitlespan" not in page.model_dump_json()
    assert "manager" not in page.model_dump_json()


@pytest.mark.parametrize("job_title", [True, False, 75.0, [], {}, -1])
def test_jobtitle_projection_rejects_non_id_json_values(job_title: object) -> None:
    with pytest.raises(OrganizationDirectoryError, match="invalid organization directory row"):
        _page(user_rows=[_user(jobtitle=job_title)])


def _departments() -> list[dict[str, object]]:
    return [
        {"id": "dept-root", "pid": "", "name": "Synthetic root", "psubcompanyid": "sub-a"},
        {
            "id": "dept-child",
            "pid": "dept-root",
            "name": "Synthetic child",
            "psubcompanyid": "sub-a",
        },
    ]


def _user(**extra: object) -> dict[str, object]:
    return {
        "id": "user-a",
        "departmentid": "dept-child",
        "orgid": "org-a",
        "subcompanyid1": "sub-a",
        **extra,
    }


def _page(
    *, user_rows: list[dict[str, object]] | None = None
) -> OrganizationDirectoryPage:
    return build_directory_page(
        current_page=1,
        next_page=None,
        is_end=True,
        user_rows=[_user()] if user_rows is None else user_rows,
    )


def test_builds_complete_structural_snapshot() -> None:
    snapshot = build_directory_snapshot(
        departments=build_directory_departments(_departments()),
        user_pages=[_page()],
        authoritative_user_count_before=1,
        authoritative_user_count_after=1,
        fetched_at=FETCHED_AT,
    )

    assert snapshot.is_complete is True
    assert snapshot.returned_user_count == 1
    assert snapshot.memberships[0].department_id == "dept-child"
    assert snapshot.departments[1].parent_department_id == "dept-root"
    assert snapshot.departments[1].subcompany_id == "sub-a"
    assert snapshot.memberships[0].organization_id == "org-a"
    assert "organization_id" not in OrganizationDepartment.model_fields


def test_count_mismatch_marks_snapshot_incomplete() -> None:
    snapshot = build_directory_snapshot(
        departments=build_directory_departments(_departments()),
        user_pages=[_page()],
        authoritative_user_count_before=2,
        authoritative_user_count_after=2,
        fetched_at=FETCHED_AT,
    )

    assert snapshot.is_complete is False
    assert snapshot.returned_user_count == 1
    assert snapshot.authoritative_user_count_before == 2


def test_department_cycle_fails_closed() -> None:
    with pytest.raises(OrganizationDirectoryError, match="cycle"):
        build_directory_departments(
            [
                {"id": "dept-a", "pid": "dept-b", "name": "Synthetic A"},
                {"id": "dept-b", "pid": "dept-a", "name": "Synthetic B"},
            ]
        )


def test_all_rendered_span_and_query_credential_fields_are_excluded() -> None:
    page = build_directory_page(
        current_page=1,
        next_page=None,
        is_end=True,
        user_rows=[
            _user(
                idspan="<span>synthetic user</span>",
                departmentidspan="<span>synthetic department</span>",
                randomField0span="<span>synthetic random</span>",
                sessionkey="synthetic-secret-one",
                dataKey="synthetic-secret-two",
            )
        ],
    )
    snapshot = build_directory_snapshot(
        departments=build_directory_departments(_departments()),
        user_pages=[page],
        authoritative_user_count_before=1,
        authoritative_user_count_after=1,
        fetched_at=FETCHED_AT,
    )

    rendered = snapshot.model_dump_json()
    assert "span" not in rendered.casefold()
    assert "sessionkey" not in rendered.casefold()
    assert "datakey" not in rendered.casefold()
    assert "synthetic-secret" not in rendered


def test_snapshot_repr_and_error_do_not_contain_query_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    credential = "synthetic-secret-not-for-output"
    with pytest.raises(OrganizationDirectoryError) as exc_info:
        build_directory_page(
            current_page=1,
            next_page=None,
            is_end=True,
            user_rows=[_user(dataKey=credential, departmentid="")],
        )

    rendered = repr(exc_info.value) + caplog.text
    assert credential not in rendered
    assert "datakey" not in rendered.casefold()
