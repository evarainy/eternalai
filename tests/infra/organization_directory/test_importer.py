from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.infra.organization_directory.importer import build_directory_snapshot
from app.ports.organization_directory import OrganizationDirectoryError

FETCHED_AT = datetime(2026, 8, 31, tzinfo=UTC)


def _departments() -> list[dict[str, object]]:
    return [
        {"id": "dept-root", "pid": "", "name": "Synthetic root", "psubcompanyid": "org-a"},
        {
            "id": "dept-child",
            "pid": "dept-root",
            "name": "Synthetic child",
            "psubcompanyid": "org-a",
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


def test_builds_complete_structural_snapshot() -> None:
    snapshot = build_directory_snapshot(
        department_rows=_departments(),
        user_rows=[_user()],
        authoritative_user_count=1,
        fetched_at=FETCHED_AT,
    )

    assert snapshot.is_complete is True
    assert snapshot.returned_user_count == 1
    assert snapshot.memberships[0].department_id == "dept-child"
    assert snapshot.departments[1].parent_department_id == "dept-root"


def test_count_mismatch_marks_snapshot_incomplete() -> None:
    snapshot = build_directory_snapshot(
        department_rows=_departments(),
        user_rows=[_user()],
        authoritative_user_count=2,
        fetched_at=FETCHED_AT,
    )

    assert snapshot.is_complete is False
    assert snapshot.returned_user_count == 1
    assert snapshot.authoritative_user_count == 2


def test_department_cycle_fails_closed() -> None:
    with pytest.raises(OrganizationDirectoryError, match="cycle"):
        build_directory_snapshot(
            department_rows=[
                {"id": "dept-a", "pid": "dept-b", "name": "Synthetic A"},
                {"id": "dept-b", "pid": "dept-a", "name": "Synthetic B"},
            ],
            user_rows=[],
            authoritative_user_count=0,
            fetched_at=FETCHED_AT,
        )


def test_all_rendered_span_and_query_credential_fields_are_excluded() -> None:
    snapshot = build_directory_snapshot(
        department_rows=_departments(),
        user_rows=[
            _user(
                idspan="<span>synthetic user</span>",
                departmentidspan="<span>synthetic department</span>",
                randomField0span="<span>synthetic random</span>",
                sessionkey="synthetic-secret-one",
                dataKey="synthetic-secret-two",
            )
        ],
        authoritative_user_count=1,
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
        build_directory_snapshot(
            department_rows=_departments(),
            user_rows=[_user(dataKey=credential, departmentid="")],
            authoritative_user_count=1,
            fetched_at=FETCHED_AT,
        )

    rendered = repr(exc_info.value) + caplog.text
    assert credential not in rendered
    assert "datakey" not in rendered.casefold()
