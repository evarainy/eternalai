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


def test_projects_only_raw_lastname() -> None:
    page = _page(user_rows=[_user(lastname="  Synthetic person  ", lastnamespan="Other")])
    assert page.memberships[0].display_name == "Synthetic person"
    assert "Synthetic person" not in repr(page)


@pytest.mark.parametrize("name", [None, "", "   ", "\u2003"])
def test_missing_name_preserves_membership_and_count(name: object) -> None:
    page = _page(user_rows=[_user(lastname=name)])
    snapshot = build_directory_snapshot(
        departments=build_directory_departments(_departments()), user_pages=[page],
        authoritative_user_count_before=1, authoritative_user_count_after=1,
        fetched_at=FETCHED_AT,
    )
    assert snapshot.is_complete is True
    assert snapshot.returned_user_count == 1
    assert [(m.user_id, m.display_name) for m in snapshot.memberships] == [("user-a", None)]


@pytest.mark.parametrize("name", [1, True, {}, "<Synthetic>", "Synthetic\x00", "x" * 201])
def test_invalid_name_rejects_whole_page_without_values(name: object) -> None:
    with pytest.raises(OrganizationDirectoryError) as error:
        _page(user_rows=[_user(id="valid", lastname="Synthetic good"), _user(lastname=name)])
    assert str(error.value) == "invalid organization directory name"
    for valid in ("x", "x" * 200):
        assert _page(user_rows=[_user(lastname=valid)]).memberships[0].display_name == valid


@pytest.mark.parametrize("value", [0, "0", None, ""])
def test_zero_jobtitle_normalizes_to_absent_and_bad_values_reject_batch(value: object) -> None:
    page = _page(user_rows=[_user(jobtitle=value)])
    assert len(page.memberships) == 1
    assert page.memberships[0].job_title is None
    for invalid in (-1, 0.0, 75.0, False, True, {}, [], "bad", "01", "+1", " 1", "1 "):
        with pytest.raises(OrganizationDirectoryError, match="invalid organization directory row"):
            _page(user_rows=[_user(id="good", jobtitle=75), _user(jobtitle=invalid)])
    for valid in (75, "75"):
        assert _page(user_rows=[_user(jobtitle=valid)]).memberships[0].job_title == "75"


def test_conflicting_names_for_one_user_reject_snapshot() -> None:
    def snapshot(other: str):
        return build_directory_snapshot(
            departments=build_directory_departments(_departments()),
            user_pages=[_page(user_rows=[
                _user(lastname="Synthetic one"),
                _user(departmentid="dept-root", lastname=other),
            ])],
            authoritative_user_count_before=2, authoritative_user_count_after=2,
            fetched_at=FETCHED_AT,
        )
    assert len(snapshot("Synthetic one").memberships) == 2
    with pytest.raises(OrganizationDirectoryError, match="inconsistent organization memberships"):
        snapshot("Synthetic two")


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
