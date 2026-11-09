"""Sheet fill child: two passes over one confirmed upload (P2-SHEET-FILL-001).

Runs as ``python -I fill_child.py`` with a minimal environment and imports only the
standard library and openpyxl (never the ``app`` package). stdin carries a 4-byte
big-endian JSON length, the JSON command, then the original workbook bytes; stdout
carries a 4-byte JSON length, the JSON result, then (``fill`` only) the result workbook.
Nothing touches the disk and nothing is printed besides the protocol; stderr is dropped
by the parent.

* ``extract``: checks the bytes against the plan digest and returns, per mapping and row,
  the lookup key text (cut to 2048 characters) and whether each target cell is blank.
* ``fill``: checks the digest again, writes each text into a still blank target cell as a
  string (``data_type='s'``, so "=..." never becomes a formula), shades marks, saves to
  memory, reopens the result read-only and verifies every written cell (value and string
  type) and every non-blank target cell (value and type unchanged).

Blank means ``value is None or value == ""``; cells inside a merged range other than its
top-left cell are never blank. Results are closed codes only, never cell text.
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import warnings
import zipfile
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO
from typing import Any

MAX_KEY_CHARACTERS = 2048
MAX_CELL_CHARACTERS = 32767
MARK_COLOR = "FFFFF2CC"
_HEADER = struct.Struct(">I")
_PLAIN = (str, int, float, bool, datetime, date, time, timedelta, type(None))
# Same object rule as inspect_child --headers (VML comment drawings are kept by openpyxl).
_UNSUPPORTED_OBJECT_PREFIXES = (
    "xl/drawings/",
    "xl/charts/",
    "xl/pivottables/",
    "xl/pivotcache/",
    "xl/media/",
    "xl/ctrlprops/",
)


class Refused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _column_index(letters: str) -> int:
    if not (1 <= len(letters) <= 2 and letters.isascii() and letters.isupper()):
        raise Refused("invalid")
    index = 0
    for character in letters:
        index = index * 26 + (ord(character) - ord("A") + 1)
    return index


def _unsupported_objects(content: bytes) -> bool:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        for name in archive.namelist():
            lowered = name.lower()
            if lowered.startswith(_UNSUPPORTED_OBJECT_PREFIXES) and not (
                lowered.startswith("xl/drawings/")
                and (lowered.endswith(".vml") or lowered.endswith(".vml.rels"))
            ):
                return True
    return False


def _peek(sheet: Any, row: int, column: int) -> Any:
    """The stored cell without creating one; None when the cell does not exist."""

    return sheet._cells.get((row, column))


def _is_merged(cell: Any) -> bool:
    from openpyxl.cell.cell import MergedCell  # type: ignore[import-untyped]

    return isinstance(cell, MergedCell)


def _blank(cell: Any) -> bool:
    if cell is None:
        return True
    if _is_merged(cell):
        return False
    return cell.value is None or cell.value == ""


def _snapshot(value: Any, data_type: str) -> tuple[str, Any]:
    return data_type, value if isinstance(value, _PLAIN) else type(value).__name__


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def normalize_text(text: str) -> str:
    """What a worksheet string can hold: no XML-illegal controls, "\\n" line ends."""

    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    normalized: str = ILLEGAL_CHARACTERS_RE.sub(
        "", text.replace("\r\n", "\n").replace("\r", "\n")
    )
    return normalized


def _load(command: dict[str, Any], content: bytes) -> Any:
    if hashlib.sha256(content).hexdigest() != command["sha256"]:
        raise Refused("source_changed")
    if _unsupported_objects(content):
        raise Refused("fidelity")
    from openpyxl import load_workbook  # type: ignore[import-untyped]

    return load_workbook(BytesIO(content), data_only=False)


def _mappings(command: dict[str, Any], workbook: Any) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for item in command["mappings"]:
        sheet_name = item["sheet"]
        if sheet_name not in workbook.sheetnames:
            raise Refused("source_changed")
        sheet = workbook[sheet_name]
        if not hasattr(sheet, "_cells"):
            raise Refused("source_changed")
        first, last = int(item["first_row"]), int(item["last_row"])
        if not 2 <= first <= last:
            raise Refused("invalid")
        mappings.append(
            {
                "sheet": sheet,
                "name": sheet_name,
                "first": first,
                "last": last,
                "key": _column_index(item["key_column"]),
                "columns": [_column_index(column) for column in item["columns"]],
            }
        )
    return mappings


def extract(command: dict[str, Any], content: bytes) -> dict[str, Any]:
    workbook = _load(command, content)
    result: list[dict[str, Any]] = []
    for mapping in _mappings(command, workbook):
        sheet = mapping["sheet"]
        rows: list[dict[str, Any]] = []
        for row in range(mapping["first"], mapping["last"] + 1):
            key_cell = _peek(sheet, row, mapping["key"])
            key = "" if key_cell is None or _is_merged(key_cell) else _text(key_cell.value)
            rows.append(
                {
                    "row": row,
                    "key": key[:MAX_KEY_CHARACTERS],
                    "blank": [_blank(_peek(sheet, row, column)) for column in mapping["columns"]],
                }
            )
        result.append({"sheet": mapping["name"], "rows": rows})
    return {"result": "ok", "mappings": result}


def _save_in_memory(workbook: Any) -> bytes:
    """``Workbook.save`` without touching the disk.

    openpyxl 3.1.5 ``ExcelWriter.write_worksheet`` serialises each sheet into a
    ``NamedTemporaryFile(delete=False)`` and zips it from the path; a killed child would
    leave that file behind. ``WorksheetWriter`` accepts a ``BytesIO`` target, so the sheet
    XML and the ZIP archive both stay in memory here.
    """

    from openpyxl.drawing.spreadsheet_drawing import (  # type: ignore[import-untyped]
        SpreadsheetDrawing,
    )
    from openpyxl.worksheet._writer import WorksheetWriter  # type: ignore[import-untyped]
    from openpyxl.writer.excel import ExcelWriter  # type: ignore[import-untyped]

    class MemoryExcelWriter(ExcelWriter):  # type: ignore[misc]
        def write_worksheet(self, ws: Any) -> None:
            # Same steps as ExcelWriter.write_worksheet (non write-only branch).
            ws._drawing = SpreadsheetDrawing()
            ws._drawing.charts = ws._charts
            ws._drawing.images = ws._images
            writer = WorksheetWriter(ws, out=BytesIO())
            writer.write()
            ws._rels = writer._rels
            self._archive.writestr(ws.path[1:], writer.read())
            self.manifest.append(ws)

    output = BytesIO()
    workbook.properties.modified = datetime.now(UTC).replace(tzinfo=None)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        MemoryExcelWriter(workbook, archive).write_data()
    return output.getvalue()


def fill(command: dict[str, Any], content: bytes) -> tuple[dict[str, Any], bytes]:
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill  # type: ignore[import-untyped]

    workbook = _load(command, content)
    mappings = _mappings(command, workbook)
    by_sheet = {mapping["name"]: mapping for mapping in mappings}
    # Non-blank target cells must survive unchanged (value and type).
    kept: dict[tuple[str, int, int], tuple[str, Any]] = {}
    for mapping in mappings:
        sheet = mapping["sheet"]
        for row in range(mapping["first"], mapping["last"] + 1):
            for column in mapping["columns"]:
                cell = _peek(sheet, row, column)
                if not _blank(cell):
                    kept[(mapping["name"], row, column)] = _snapshot(cell.value, cell.data_type)

    mark_fill = PatternFill(fill_type="solid", start_color=MARK_COLOR, end_color=MARK_COLOR)
    written: dict[tuple[str, int, int], str] = {}
    skipped: list[int] = []
    for index, write in enumerate(command["writes"]):
        target = by_sheet.get(write["sheet"])
        row, column = int(write["row"]), _column_index(write["column"])
        if (
            target is None
            or not target["first"] <= row <= target["last"]
            or column not in target["columns"]
            or (write["sheet"], row, column) in written
            or type(write["mark"]) is not bool
        ):
            raise Refused("invalid")
        text = normalize_text(write["text"])
        if not text or len(text) > MAX_CELL_CHARACTERS:
            raise Refused("invalid")
        sheet = target["sheet"]
        if not _blank(_peek(sheet, row, column)):
            skipped.append(index)
            continue
        cell = sheet.cell(row=row, column=column)
        cell.value = text
        # openpyxl infers "f" for "=..." and "e" for error literals; force a plain string.
        cell.data_type = "s"
        if write["mark"]:
            cell.fill = mark_fill
        written[(write["sheet"], row, column)] = text

    result = _save_in_memory(workbook)

    reopened = load_workbook(BytesIO(result), read_only=True, data_only=False)
    checked = 0
    try:
        for mapping in mappings:
            columns = mapping["columns"]
            low, high = min(columns), max(columns)
            for row_index, cells in enumerate(
                reopened[mapping["name"]].iter_rows(
                    min_row=mapping["first"], max_row=mapping["last"],
                    min_col=low, max_col=high,
                ),
                start=mapping["first"],
            ):
                for column in columns:
                    cell = cells[column - low]
                    key = (mapping["name"], row_index, column)
                    if key in written:
                        if cell.value != written[key] or cell.data_type != "s":
                            raise Refused("verify_failed")
                        checked += 1
                    elif key in kept:
                        if _snapshot(cell.value, cell.data_type) != kept[key]:
                            raise Refused("verify_failed")
    finally:
        reopened.close()
    if checked != len(written):
        raise Refused("verify_failed")
    return {"result": "ok", "written": len(written), "skipped": skipped}, result


def _read_input() -> tuple[dict[str, Any], bytes]:
    data = sys.stdin.buffer.read()
    (length,) = _HEADER.unpack_from(data)
    command = json.loads(data[_HEADER.size:_HEADER.size + length].decode("utf-8"))
    if not isinstance(command, dict):
        raise Refused("invalid")
    return command, data[_HEADER.size + length:]


def _write_output(result: dict[str, Any], content: bytes = b"") -> None:
    encoded = json.dumps(result, ensure_ascii=True).encode("ascii")
    sys.stdout.buffer.write(_HEADER.pack(len(encoded)) + encoded + content)
    sys.stdout.buffer.flush()


def main() -> int:
    warnings.simplefilter("ignore")
    try:
        command, content = _read_input()
        mode = command.get("mode")
        if mode == "extract":
            _write_output(extract(command, content))
        elif mode == "fill":
            _write_output(*fill(command, content))
        else:
            raise Refused("invalid")
    except Refused as refused:
        _write_output({"result": refused.code})
    except Exception:
        # Malformed package or XML, openpyxl failures, bad commands: never their text.
        _write_output({"result": "unreadable"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
