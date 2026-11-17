"""Personal flow export: one sheet per result table, built in memory and read back.

The layout is code-owned. Every value is written with ``write_string`` so no text
becomes a formula, number or link. ``verify_user_skill_report`` reopens the bytes
and compares every cell with the rendered tables.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from io import BytesIO
from typing import Any

import xlsxwriter  # type: ignore[import-untyped]
from openpyxl import load_workbook  # type: ignore[import-untyped]

STATUS_HEADER = "状态"
STATUS_LABELS = {"ok": "成功", "not_found": "未找到", "failed": "失败", "not_executed": "未执行"}
NOTE_SHEET = "说明"
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_SHEET_FORBIDDEN = re.compile(r"[\[\]:*?/\\]")


def _sheet_name(title: str, index: int, used: set[str]) -> str:
    base = _SHEET_FORBIDDEN.sub("_", title).strip() or f"表{index}"
    name = base[:28]
    while name in used:
        name = f"{base[:25]}_{index}"
        index += 1
    used.add(name)
    return name


def _rows(table: Mapping[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table["rows"]:
        status = STATUS_LABELS.get(str(row.get("status")), str(row.get("status")))
        reason = row.get("reason_label")
        rows.append([*map(str, row["cells"]), status if not reason else f"{status}：{reason}"])
    return rows


def build_user_skill_report(
    tables: Sequence[Mapping[str, Any]], *, name: str, inputs: Sequence[Mapping[str, Any]],
    queried_at: datetime,
) -> bytes:
    buffer = BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {"in_memory": True, "strings_to_formulas": False,
                                            "strings_to_numbers": False, "strings_to_urls": False})
    bold = workbook.add_format({"bold": True})
    used: set[str] = {NOTE_SHEET}
    for index, table in enumerate(tables, start=1):
        sheet = workbook.add_worksheet(_sheet_name(str(table["title"]), index, used))
        headers = [str(column["label"]) for column in table["columns"]] + [STATUS_HEADER]
        for col, header in enumerate(headers):
            sheet.write_string(0, col, header, bold)
            sheet.set_column(col, col, 24)
        for row_index, row in enumerate(_rows(table), start=1):
            for col, value in enumerate(row):
                sheet.write_string(row_index, col, value)
    note = workbook.add_worksheet(NOTE_SHEET)
    lines = [f"功能：{name}", f"查询时间：{queried_at.strftime(_TIME_FORMAT)}",
             *(f"{item['label']}：{item['value']}" for item in inputs),
             "说明：导出时重新查询了一次，内容以查询时间为准。"]
    for row_index, line in enumerate(lines):
        note.write_string(row_index, 0, line)
    note.set_column(0, 0, 60)
    workbook.close()
    return buffer.getvalue()


def verify_user_skill_report(content: bytes, tables: Sequence[Mapping[str, Any]]) -> bool:
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        sheets = [ws for ws in workbook.worksheets if ws.title != NOTE_SHEET]
        if len(sheets) != len(tables):
            return False
        for sheet, table in zip(sheets, tables, strict=True):
            expected_headers = [str(c["label"]) for c in table["columns"]] + [STATUS_HEADER]
            values = [
                ["" if cell is None else str(cell) for cell in row]
                for row in sheet.iter_rows(values_only=True)
            ]
            if not values or values[0][: len(expected_headers)] != expected_headers:
                return False
            body = [row[: len(expected_headers)] for row in values[1:]]
            if body != _rows(table):
                return False
        return True
    finally:
        workbook.close()


def user_skill_report_filename(name: str, generated_at: datetime) -> str:
    safe = re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "功能"
    return f"{safe[:30]}_{generated_at:%Y%m%d_%H%M}.xlsx"


__all__ = ("build_user_skill_report", "user_skill_report_filename", "verify_user_skill_report")
