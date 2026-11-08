"""OA pending-workflow report v1: fixed xlsx layout, in-memory build and read-back check.

The layout is code-owned (never chosen by a model). Every business value is written with
``write_string`` so no text becomes a formula, number or link. The summary sheet holds one
total formula with its cached value (DECISIONS 2026-11-02 item 5). ``verify_report`` reopens
the bytes with openpyxl ``read_only=True, data_only=True`` and compares every cell with the
source rows; it proves what was written and cached, not that Excel recalculated anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from io import BytesIO
from typing import NamedTuple

import xlsxwriter  # type: ignore[import-untyped]
from openpyxl import load_workbook  # type: ignore[import-untyped]

DATA_SHEET = "待办清单"
NOTE_SHEET = "说明"
HEADERS = ("序号", "标题", "状态", "接收时间", "创建时间", "流程类型编号")
_WIDTHS = (6, 60, 14, 20, 20, 14)
_TOTAL_FORMULA = f"=COUNTA('{DATA_SHEET}'!A:A)-1"
_SNAPSHOT_NOTE = "非事务一致快照：读取期间 OA 待办可能发生变化。"
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class ReportRow(NamedTuple):
    title: str
    status: str
    received_at: str
    created_at: str
    workflow_type_id: str


def report_filename(generated_at: datetime) -> str:
    return f"OA待办_{generated_at:%Y%m%d_%H%M}.xlsx"


def _note_rows(
    generated_at: datetime, read_started_at: datetime, read_finished_at: datetime
) -> tuple[tuple[str, str], ...]:
    return (
        ("生成时间", generated_at.strftime(_TIME_FORMAT)),
        ("读取开始时间", read_started_at.strftime(_TIME_FORMAT)),
        ("读取结束时间", read_finished_at.strftime(_TIME_FORMAT)),
        ("数据说明", _SNAPSHOT_NOTE),
    )


def build_report(
    rows: Sequence[ReportRow],
    *,
    generated_at: datetime,
    read_started_at: datetime,
    read_finished_at: datetime,
) -> bytes:
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    bold = workbook.add_format({"bold": True})
    sheet = workbook.add_worksheet(DATA_SHEET)
    for column, (header, width) in enumerate(zip(HEADERS, _WIDTHS, strict=True)):
        sheet.write_string(0, column, header, bold)
        sheet.set_column(column, column, width)
    for index, row in enumerate(rows, start=1):
        sheet.write_number(index, 0, index)
        for column, value in enumerate(row, start=1):
            sheet.write_string(index, column, value)
    sheet.freeze_panes(1, 0)
    sheet.autofilter(0, 0, len(rows), len(HEADERS) - 1)

    note = workbook.add_worksheet(NOTE_SHEET)
    note.set_column(0, 0, 14)
    note.set_column(1, 1, 50)
    notes = _note_rows(generated_at, read_started_at, read_finished_at)
    for index, (label, value) in enumerate(notes):
        note.write_string(index, 0, label, bold)
        note.write_string(index, 1, value)
    note.write_string(len(notes), 0, "总条数", bold)
    note.write_formula(len(notes), 1, _TOTAL_FORMULA, None, len(rows))
    workbook.close()
    return output.getvalue()


def verify_report(
    content: bytes,
    rows: Sequence[ReportRow],
    *,
    generated_at: datetime,
    read_started_at: datetime,
    read_finished_at: datetime,
) -> bool:
    """Every written cell and the cached total equal the source; never raises on mismatch."""

    expected_data = [HEADERS, *((index, *row) for index, row in enumerate(rows, start=1))]
    notes = _note_rows(generated_at, read_started_at, read_finished_at)
    expected_notes = [*notes, ("总条数", len(rows))]
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        if workbook.sheetnames != [DATA_SHEET, NOTE_SHEET]:
            return False
        data = [tuple(row) for row in workbook[DATA_SHEET].iter_rows(values_only=True)]
        note = [tuple(row) for row in workbook[NOTE_SHEET].iter_rows(values_only=True)]
    finally:
        workbook.close()
    return data == [tuple(row) for row in expected_data] and note == expected_notes


__all__ = (
    "DATA_SHEET",
    "HEADERS",
    "NOTE_SHEET",
    "ReportRow",
    "build_report",
    "report_filename",
    "verify_report",
)
