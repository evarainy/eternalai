"""Run the xlsx inspection child and accept only a bounded result (FOUNDATION_PLAN §3.4).

The child gets only the variables an interpreter needs to start, never credentials. This
strips the environment only: the child still runs as the same OS user (J5, debt Q3).
``inspect_workbook`` blocks; callers run it in a thread. ``subprocess.run`` kills the
child when the timeout expires.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.ports.file_artifacts import FileArtifactError, WorkbookHeaders

INSPECTION_TIMEOUT_SECONDS = 20.0
_CHILD = Path(__file__).with_name("inspect_child.py").resolve()
_ENVIRONMENT = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH")
_MAX_OUTPUT_BYTES = 16 * 1024
_MAX_SHEETS = 64
_MAX_SHEET_NAME = 31
_MAX_INSPECTION_BYTES = 6 * 1024
_MAX_HEADER_OUTPUT_BYTES = 2 * 1024 * 1024
_HEADER_COLUMNS = 50
_HEADER_CELL_CHARACTERS = 65  # 64 plus the truncation mark
_REJECTIONS = frozenset(
    {
        "chat_upload_type_invalid",
        "chat_upload_encrypted",
        "chat_upload_macro_or_external",
        "chat_upload_zip_limit",
        "chat_upload_unreadable",
    }
)


def _dimension(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError
    return value


def _sheets(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > _MAX_SHEETS:
        raise ValueError
    sheets: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"name", "max_row", "max_column"}:
            raise ValueError
        name = item["name"]
        if type(name) is not str or not 0 < len(name) <= _MAX_SHEET_NAME:
            raise ValueError
        sheets.append(
            {
                "name": name,
                "max_row": _dimension(item["max_row"]),
                "max_column": _dimension(item["max_column"]),
            }
        )
    return sheets


def inspect_workbook(
    path: Path, timeout: float = INSPECTION_TIMEOUT_SECONDS
) -> dict[str, object]:
    """Return ``{"sheets": [...]}`` or raise ``FileArtifactError(<§3.4 code>)``."""

    environment = {name: os.environ[name] for name in _ENVIRONMENT if name in os.environ}
    try:
        completed = subprocess.run(
            [sys.executable, "-I", str(_CHILD), str(path)],
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise FileArtifactError("chat_upload_inspection_timeout") from None
    try:
        if completed.returncode != 0 or len(completed.stdout) > _MAX_OUTPUT_BYTES:
            raise ValueError
        output = json.loads(completed.stdout)
        if not isinstance(output, dict):
            raise ValueError
        result = output.get("result")
        if result in _REJECTIONS and set(output) == {"result"}:
            raise FileArtifactError(str(result))
        if result != "ok" or set(output) != {"result", "sheets"}:
            raise ValueError
        inspection: dict[str, object] = {"sheets": _sheets(output["sheets"])}
    except (ValueError, TypeError):
        raise FileArtifactError("chat_upload_unreadable") from None
    # The column is bounded by a CHECK (pg_column_size <= 8192); refuse before sealing.
    if len(json.dumps(inspection, ensure_ascii=False).encode("utf-8")) > _MAX_INSPECTION_BYTES:
        raise FileArtifactError("chat_upload_zip_limit")
    return inspection


def _run_child(arguments: list[str], timeout: float) -> bytes:
    environment = {name: os.environ[name] for name in _ENVIRONMENT if name in os.environ}
    completed = subprocess.run(
        [sys.executable, "-I", str(_CHILD), *arguments],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError
    return completed.stdout


def read_workbook_headers(
    path: Path, timeout: float = INSPECTION_TIMEOUT_SECONDS
) -> WorkbookHeaders:
    """Row 1 of every sheet of an already inspected upload; blocks, run it in a thread.

    Raises ``FileArtifactError("chat_upload_unreadable")`` for any malformed child result
    and ``FileArtifactError("chat_upload_inspection_timeout")`` on timeout.
    """

    try:
        stdout = _run_child(["--headers", str(path)], timeout)
        if len(stdout) > _MAX_HEADER_OUTPUT_BYTES:
            raise ValueError
        output = json.loads(stdout)
        if (
            not isinstance(output, dict)
            or output.get("result") != "ok"
            or set(output) != {"result", "headers", "objects"}
            or type(output["objects"]) is not bool
            or not isinstance(output["headers"], list)
            or len(output["headers"]) > _MAX_SHEETS
        ):
            raise ValueError
        headers: dict[str, tuple[str, ...]] = {}
        for item in output["headers"]:
            if not isinstance(item, dict) or set(item) != {"name", "cells"}:
                raise ValueError
            name, cells = item["name"], item["cells"]
            if (
                type(name) is not str
                or not 0 < len(name) <= _MAX_SHEET_NAME
                or name in headers
                or not isinstance(cells, list)
                or len(cells) > _HEADER_COLUMNS
                or any(
                    type(cell) is not str or len(cell) > _HEADER_CELL_CHARACTERS
                    for cell in cells
                )
            ):
                raise ValueError
            headers[name] = tuple(cells)
    except subprocess.TimeoutExpired:
        raise FileArtifactError("chat_upload_inspection_timeout") from None
    except (ValueError, TypeError):
        raise FileArtifactError("chat_upload_unreadable") from None
    return WorkbookHeaders(headers=headers, unsupported_objects=output["objects"])


__all__ = ("INSPECTION_TIMEOUT_SECONDS", "inspect_workbook", "read_workbook_headers")
