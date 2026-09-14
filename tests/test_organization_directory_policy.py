import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.event_loop import make_event_loop
from app.organization_directory_policy import (
    DispatchDepartmentPolicyInspection,
    check_dispatch_department_policy,
    inspect_dispatch_department_policy,
)
from app.ports import work_object_scope as policy
from app.ports.organization_directory import (
    OrganizationDepartment,
    OrganizationDirectoryReadError,
    OrganizationDirectoryReadView,
)


class PolicyDirectory:
    def __init__(self, ids):
        now = datetime.now(UTC)
        self.view = OrganizationDirectoryReadView(
            snapshot_version=1,
            source_fetched_at=now,
            last_success_at=now,
            observed_at=now,
            departments=tuple(
                OrganizationDepartment(department_id=x, display_name="Synthetic") for x in ids
            ),
            memberships=(),
        )
        self.reads = 0
        self.missing = False

    async def read_view(self):
        self.reads += 1
        if self.missing:
            raise OrganizationDirectoryReadError("organization_directory_missing")
        return self.view


def test_full_snapshot_set_drift_including_equal_size_replacement(monkeypatch):
    prison = policy._PRISON_AREA_DEPARTMENT_IDS
    allowed = frozenset({"office-approved"})
    monkeypatch.setattr(policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", allowed)
    clean = prison | allowed
    assert inspect_dispatch_department_policy(department_ids=clean).is_clean
    result = inspect_dispatch_department_policy(
        department_ids=(clean - {"572"}) | {"new-unregistered"}
    )
    assert result.model_dump() == {
        "unregistered_ids": frozenset({"new-unregistered"}), "missing_allowed_ids": frozenset(),
        "missing_prison_ids": frozenset({"572"}), "conflict_ids": frozenset(),
    }
    assert not result.is_clean
    assert "new-unregistered" not in repr(result) and "572" not in repr(result)
    assert inspect_dispatch_department_policy(department_ids=prison).missing_allowed_ids == allowed
    monkeypatch.setattr(policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", allowed | {"572"})
    result = inspect_dispatch_department_policy(department_ids=clean)
    assert result.conflict_ids == frozenset({"572"}) and not result.is_clean
    monkeypatch.setattr(policy, "_CROSS_DEPARTMENT_DISPATCH_ALLOWED_IDS", frozenset())
    assert inspect_dispatch_department_policy(department_ids=prison).is_clean
    assert inspect_dispatch_department_policy(department_ids=clean).unregistered_ids == allowed
    with pytest.raises(ValidationError):
        DispatchDepartmentPolicyInspection.model_validate({**result.model_dump(), "extra": True})
    with pytest.raises(ValidationError):
        result.conflict_ids = frozenset()


def test_policy_reads_full_view_and_rechecks_freshness(monkeypatch):
    import app.organization_directory_access as access
    directory = PolicyDirectory(policy._PRISON_AREA_DEPARTMENT_IDS)
    with asyncio.Runner(loop_factory=make_event_loop) as runner:
        assert runner.run(check_dispatch_department_policy(directory)) is True
    assert directory.reads == 1
    original = access.FreshDirectoryView.recheck
    checks = []

    def expires(self):
        checks.append(True)
        if len(checks) == 2:
            raise access.DirectoryAccessError("organization_directory_stale")
        original(self)

    monkeypatch.setattr(access.FreshDirectoryView, "recheck", expires)
    with asyncio.Runner(loop_factory=make_event_loop) as runner:
        assert runner.run(check_dispatch_department_policy(directory)) is False
    assert len(checks) == 2 and directory.reads == 2


@pytest.mark.parametrize("case", ["missing", "stale", "bad-metadata", "future", "duplicate"])
def test_invalid_snapshot_never_reports_clean(case):
    directory = PolicyDirectory(policy._PRISON_AREA_DEPARTMENT_IDS)
    view = directory.view
    updates = {
        "missing": {},
        "stale": {"observed_at": view.observed_at + timedelta(days=3)},
        "bad-metadata": {"last_success_at": None},
        "future": {"source_fetched_at": view.observed_at + timedelta(days=1)},
        "duplicate": {"departments": view.departments + view.departments},
    }[case]
    directory.missing = case == "missing"
    directory.view = view.model_copy(update=updates)
    with asyncio.Runner(loop_factory=make_event_loop) as runner:
        assert runner.run(check_dispatch_department_policy(directory)) is False
    assert directory.reads == 1
