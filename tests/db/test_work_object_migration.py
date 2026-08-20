from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPO_ROOT / "alembic" / "versions" / "20260819_120000_work_objects.py"
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
    spec = importlib.util.spec_from_file_location("work_object_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Work Object migration must be importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_only_creates_the_work_object_table_and_its_index() -> None:
    migration = _load_migration()
    recorder = OperationsRecorder()
    migration.op = recorder

    migration.upgrade()

    assert migration.revision == "20260819_120000"
    assert migration.down_revision == "20260731_090000"
    assert [call[0] for call in recorder.calls] == ["create_table", "create_index"]
    table_call = recorder.calls[0]
    assert table_call[1][0] == "work_objects"
    columns = {
        argument.name: argument
        for argument in table_call[1][1:]
        if isinstance(argument, sa.Column)
    }
    assert set(columns) == {
        "work_object_id",
        "source_system",
        "source_kind",
        "source_ref",
        "assignee_ai_user_id",
        "assignee_display_name",
        "due_at",
        "source_title",
        "source_status",
        "source_received_at",
        "source_created_at",
        "source_workflow_type_id",
        "source_fetched_at",
        "handling_mark",
        "handling_marked_by_ai_user_id",
        "handling_marked_at",
        "task_record_id",
        "created_at",
        "updated_at",
    }
    assert isinstance(columns["due_at"].type, sa.DateTime)
    assert columns["due_at"].nullable is True
    assert isinstance(columns["source_fetched_at"].type, sa.DateTime)
    assert columns["source_fetched_at"].type.timezone is True


def test_downgrade_only_removes_objects_created_by_this_migration() -> None:
    migration = _load_migration()
    recorder = OperationsRecorder()
    migration.op = recorder

    migration.downgrade()

    assert recorder.calls == [
        (
            "drop_index",
            ("ix_work_objects_assignee_fetched",),
            {"table_name": "work_objects"},
        ),
        ("drop_table", ("work_objects",), {}),
    ]
