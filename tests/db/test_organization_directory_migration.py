from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = REPO_ROOT / "alembic" / "versions" / "20260831_120000_organization_directory.py"


class OperationsRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def __getattr__(self, name: str):
        def record(*args: object, **kwargs: object) -> None:
            self.calls.append((name, args, kwargs))
        return record


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "organization_directory_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_creates_only_new_directory_tables_and_indexes() -> None:
    migration = _load_migration()
    recorder = OperationsRecorder()
    migration.op = recorder

    migration.upgrade()

    assert migration.down_revision == "20260827_180000"
    assert [call[0] for call in recorder.calls] == [
        "create_table", "create_index", "create_table", "create_index"
    ]
    assert [recorder.calls[index][1][0] for index in (0, 2)] == [
        "organization_departments", "organization_user_memberships"
    ]
    department_columns = {
        item.name for item in recorder.calls[0][1][1:] if isinstance(item, sa.Column)
    }
    membership_columns = {
        item.name for item in recorder.calls[2][1][1:] if isinstance(item, sa.Column)
    }
    assert "manager_id" not in department_columns | membership_columns
    assert department_columns == {
        "department_id", "parent_department_id", "display_name", "organization_id", "fetched_at"
    }
    assert membership_columns == {
        "user_id", "department_id", "organization_id", "subcompany_id", "fetched_at"
    }


def test_downgrade_only_removes_directory_objects() -> None:
    migration = _load_migration()
    recorder = OperationsRecorder()
    migration.op = recorder

    migration.downgrade()

    assert [call[0] for call in recorder.calls] == [
        "drop_index", "drop_table", "drop_index", "drop_table"
    ]
    assert recorder.calls[1][1] == ("organization_user_memberships",)
    assert recorder.calls[3][1] == ("organization_departments",)
