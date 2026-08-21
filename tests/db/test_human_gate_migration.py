from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPO_ROOT
    / "alembic"
    / "versions"
    / "20260821_090000_human_gate_bindings.py"
)


class OperationsRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def create_table(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("create_table", args, kwargs))

    def create_index(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("create_index", args, kwargs))

    def drop_index(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("drop_index", args, kwargs))

    def drop_table(self, *args: object, **kwargs: object) -> None:
        self.calls.append(("drop_table", args, kwargs))


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("human_gate_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Human gate migration must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_adds_only_two_new_tables_and_one_index() -> None:
    migration = _load_migration()
    recorder = OperationsRecorder()
    migration.op = recorder

    migration.upgrade()

    assert migration.revision == "20260821_090000"
    assert migration.down_revision == "20260819_120000"
    assert [call[0] for call in recorder.calls] == [
        "create_table",
        "create_table",
        "create_index",
    ]
    assert recorder.calls[0][1][0] == "task_version_binding_manifests"
    assert recorder.calls[1][1][0] == "human_gate_requests"
    manifest_columns = {
        item.name: item
        for item in recorder.calls[0][1][1:]
        if isinstance(item, sa.Column)
    }
    request_columns = {
        item.name: item
        for item in recorder.calls[1][1][1:]
        if isinstance(item, sa.Column)
    }
    assert set(manifest_columns) == {
        "task_id",
        "manifest_digest",
        "bindings",
        "locked_at",
    }
    assert set(request_columns) == {
        "request_id",
        "task_id",
        "requested_for_ai_user_id",
        "requested_session_id",
        "requested_tenant_id",
        "action_digest",
        "request_digest",
        "binding_manifest_digest",
        "requested_at",
        "expires_at",
        "decision",
        "decided_by_ai_user_id",
        "decided_session_id",
        "decided_tenant_id",
        "decided_at",
    }
    assert request_columns["decision"].nullable is True
    assert request_columns["decided_at"].type.timezone is True
    assert request_columns["expires_at"].type.timezone is True


def test_downgrade_removes_only_objects_created_by_this_revision() -> None:
    migration = _load_migration()
    recorder = OperationsRecorder()
    migration.op = recorder

    migration.downgrade()

    assert recorder.calls == [
        (
            "drop_index",
            ("ix_human_gate_requests_task_id",),
            {"table_name": "human_gate_requests"},
        ),
        ("drop_table", ("human_gate_requests",), {}),
        ("drop_table", ("task_version_binding_manifests",), {}),
    ]
