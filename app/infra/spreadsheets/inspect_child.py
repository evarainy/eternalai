"""xlsx inspection child (FOUNDATION_PLAN §3.4 steps 1-4).

Runs as ``python -I inspect_child.py <path>`` with a minimal environment. It imports only
the standard library, defusedxml and openpyxl (never the ``app`` package) and writes one
ASCII JSON object to stdout: ``{"result": "ok", "sheets": [...]}`` or
``{"result": "<chat_upload_* code>"}``. openpyxl 3.1.5 switches its ``fromstring`` and
``iterparse`` to defusedxml when defusedxml is installed.

``python -I inspect_child.py --headers <path>`` reads an already inspected upload: row 1
of every worksheet (first 50 columns, each text cut to 64 characters plus "…") and
whether the package holds DrawingML, charts, pivots or media that an openpyxl round trip
would drop: ``{"result": "ok", "headers": [{"name", "cells"}], "objects": bool}``.
"""

from __future__ import annotations

import json
import posixpath
import sys
import zipfile
from typing import Any

import defusedxml.ElementTree as SafeET  # type: ignore[import-untyped]

MAX_ENTRIES = 2000
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_ENTRY_BYTES = 50 * 1024 * 1024
MAX_RATIO = 100
MAX_SHEETS = 64
READ_CHUNK = 64 * 1024
_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENCRYPTED_STREAM = "EncryptedPackage".encode("utf-16-le")
_MAIN = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
_CT_NS = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_OFFICE_DOCUMENT = "/officeDocument"
_HYPERLINK = "/hyperlink"
_REQUIRED = ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml")
_FORBIDDEN_PREFIXES = ("xl/externallinks/", "xl/embeddings/", "xl/activex/", "customui/")
_FORBIDDEN_NAMES = ("xl/connections.xml",)
# A part is forbidden for what it declares to be, wherever in the package it is stored.
# Relationship types are compared by last segment: Transitional and Strict differ in the prefix.
_FORBIDDEN_RELATIONSHIPS = frozenset(
    {
        "oleobject",
        "package",
        "externallink",
        "externallinklong",
        "externallinkpath",
        "xlexternallinkpath",
        "xlpathmissing",
        "xlstartup",
        "xlalternatestartup",
        "xllibrary",
        "control",
        "ctrlprop",
        "activexcontrol",
        "activexcontrolbinary",
        "vbaproject",
        "vbaprojectsignature",
        "xlmacrosheet",
        "xlintlmacrosheet",
        "connections",
        "extensibility",
    }
)
_FORBIDDEN_TYPE_MARKERS = (
    "macroenabled",
    "macrosheet",
    "vbaproject",
    "oleobject",
    "activex",
    "externallink",
    "controlproperties",
    "spreadsheetml.connections",
    "customui",
)
# Whole documents embedded as package parts; the workbook's own main type is a different value.
_FORBIDDEN_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-excel",
        "application/msword",
        "application/vnd.ms-powerpoint",
    }
)


class Rejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _check_container(path: str) -> None:
    with open(path, "rb") as handle:
        head = handle.read(len(_CFB_MAGIC))
        if head != _CFB_MAGIC:
            return
        # Password-protected OOXML is an OLE compound file carrying EncryptedPackage;
        # any other compound file (.xls, .et, .doc) is simply the wrong type.
        if _ENCRYPTED_STREAM in handle.read():
            raise Rejected("chat_upload_encrypted")
    raise Rejected("chat_upload_type_invalid")


def _check_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise Rejected("chat_upload_zip_limit")
    entries: dict[str, zipfile.ZipInfo] = {}
    declared_total = 0
    for info in infos:
        name = info.filename
        if info.flag_bits & 0x1:
            raise Rejected("chat_upload_encrypted")
        if (
            "\\" in name
            or name.startswith("/")
            or ".." in name.split("/")
            or (len(name) > 1 and name[1] == ":")
            or name in entries
        ):
            raise Rejected("chat_upload_unreadable")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise Rejected("chat_upload_unreadable")
        declared_total += info.file_size
        if info.file_size > MAX_ENTRY_BYTES or declared_total > MAX_TOTAL_BYTES:
            raise Rejected("chat_upload_zip_limit")
        if info.file_size > MAX_RATIO * max(info.compress_size, 1):
            raise Rejected("chat_upload_zip_limit")
        entries[name] = info
    for required in _REQUIRED:
        if required not in entries:
            raise Rejected("chat_upload_type_invalid")
    for name in entries:
        lowered = name.lower()
        if (
            "vbaproject" in lowered
            or "customui" in lowered
            or lowered.startswith(_FORBIDDEN_PREFIXES)
            or lowered in _FORBIDDEN_NAMES
        ):
            raise Rejected("chat_upload_macro_or_external")
    return entries


def _inflate_all(archive: zipfile.ZipFile, entries: dict[str, zipfile.ZipInfo]) -> None:
    """Count what actually inflates, independent of the declared sizes (and check CRCs)."""

    total = 0
    for info in entries.values():
        produced = 0
        with archive.open(info) as member:
            while chunk := member.read(READ_CHUNK):
                produced += len(chunk)
                total += len(chunk)
                if produced > MAX_ENTRY_BYTES or total > MAX_TOTAL_BYTES:
                    raise Rejected("chat_upload_zip_limit")
                if produced > MAX_RATIO * max(info.compress_size, 1):
                    raise Rejected("chat_upload_zip_limit")


def _parse(archive: zipfile.ZipFile, name: str) -> Any:
    with archive.open(name) as member:
        return SafeET.parse(member, forbid_dtd=True).getroot()


def _part_name(base_dir: str, target: str) -> str:
    joined = target.lstrip("/") if target.startswith("/") else posixpath.join(base_dir, target)
    return posixpath.normpath(joined)


def _check_parts(archive: zipfile.ZipFile, entries: dict[str, zipfile.ZipInfo]) -> None:
    overrides: dict[str, str] = {}
    defaults: dict[str, str] = {}
    for element in _parse(archive, "[Content_Types].xml"):
        content_type = element.get("ContentType", "")
        lowered = content_type.strip().lower()
        if lowered in _FORBIDDEN_TYPES or any(
            marker in lowered for marker in _FORBIDDEN_TYPE_MARKERS
        ):
            raise Rejected("chat_upload_macro_or_external")
        if element.tag == _CT_NS + "Override":
            overrides[element.get("PartName", "").lstrip("/")] = content_type
        elif element.tag == _CT_NS + "Default":
            defaults[element.get("Extension", "").lower()] = content_type

    main_part = None
    for rels_name in [name for name in entries if name.lower().endswith(".rels")]:
        # "xl/_rels/workbook.xml.rels" describes targets relative to "xl".
        source_dir = posixpath.dirname(posixpath.dirname(rels_name))
        for relationship in _parse(archive, rels_name).iter(_REL_NS + "Relationship"):
            kind = relationship.get("Type", "").strip()
            if kind.rstrip("/").rsplit("/", 1)[-1].lower() in _FORBIDDEN_RELATIONSHIPS:
                raise Rejected("chat_upload_macro_or_external")
            if relationship.get("TargetMode", "").strip().lower() == "external":
                if not kind.endswith(_HYPERLINK):
                    raise Rejected("chat_upload_macro_or_external")
                continue
            if rels_name == "_rels/.rels" and kind.endswith(_OFFICE_DOCUMENT):
                main_part = _part_name(source_dir, relationship.get("Target", ""))
    if main_part is None or main_part not in entries:
        raise Rejected("chat_upload_type_invalid")
    extension = posixpath.splitext(main_part)[1].lstrip(".").lower()
    if overrides.get(main_part, defaults.get(extension, "")) != _MAIN:
        raise Rejected("chat_upload_type_invalid")

    sheets = _parse(archive, main_part).find(_SHEET_NS + "sheets")
    if sheets is not None and len(sheets.findall(_SHEET_NS + "sheet")) > MAX_SHEETS:
        raise Rejected("chat_upload_zip_limit")


def _sheet_head_size(sheet: Any) -> None:
    """Read only the head of a sheet: <dimension> precedes <sheetData> when present.

    openpyxl's own lookup waits for the *end* of <sheetData> when <dimension> is missing,
    which parses the whole sheet; a missing size stays None here instead.
    """

    from openpyxl.utils.cell import range_boundaries  # type: ignore[import-untyped]

    source = sheet._get_source()
    try:
        for _event, element in SafeET.iterparse(source, events=("start",), forbid_dtd=True):
            if element.tag == _SHEET_NS + "dimension":
                (
                    sheet._min_column,
                    sheet._min_row,
                    sheet._max_column,
                    sheet._max_row,
                ) = range_boundaries(element.get("ref", ""))
                return
            if element.tag == _SHEET_NS + "sheetData":
                return
    finally:
        source.close()


def _read_sheets(path: str) -> list[dict[str, object]]:
    from openpyxl import load_workbook  # type: ignore[import-untyped]
    from openpyxl.reader import excel  # type: ignore[import-untyped]

    class HeadSizedWorksheet(excel.ReadOnlyWorksheet):  # type: ignore[misc]
        _get_size = _sheet_head_size

    # openpyxl 3.1.5 builds read-only sheets through this module-level name.
    excel.ReadOnlyWorksheet = HeadSizedWorksheet
    # A path would be checked by extension (".part" is refused); a file object is not.
    with open(path, "rb") as handle:
        workbook = load_workbook(handle, read_only=True, data_only=False)
        try:
            sheets: list[dict[str, object]] = []
            for name in workbook.sheetnames:
                sheet = workbook[name]
                # Read-only sheets report the stored <dimension>, None when it is absent;
                # chartsheets have no cells. Nothing is scanned to compute a size.
                sheets.append(
                    {
                        "name": name,
                        "max_row": getattr(sheet, "max_row", None),
                        "max_column": getattr(sheet, "max_column", None),
                    }
                )
        finally:
            workbook.close()
    if len(sheets) > MAX_SHEETS:
        raise Rejected("chat_upload_zip_limit")
    return sheets


HEADER_COLUMNS = 50
HEADER_CELL_CHARACTERS = 64
TRUNCATION_MARK = "…"
# Parts an openpyxl load/save would drop or rewrite. Legacy VML drawings only carry cell
# comments here (controls are refused at upload) and openpyxl rewrites those itself.
_UNSUPPORTED_OBJECT_PREFIXES = (
    "xl/drawings/",
    "xl/charts/",
    "xl/pivottables/",
    "xl/pivotcache/",
    "xl/media/",
    "xl/ctrlprops/",
)


def unsupported_objects(names: list[str]) -> bool:
    for name in names:
        lowered = name.lower()
        if lowered.startswith(_UNSUPPORTED_OBJECT_PREFIXES) and not (
            lowered.startswith("xl/drawings/")
            and (lowered.endswith(".vml") or lowered.endswith(".vml.rels"))
        ):
            return True
    return False


def cell_text(value: object) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _header_text(value: object) -> str:
    text = cell_text(value)
    if len(text) > HEADER_CELL_CHARACTERS:
        return text[:HEADER_CELL_CHARACTERS] + TRUNCATION_MARK
    return text


def read_headers(path: str) -> dict[str, object]:
    from openpyxl import load_workbook
    from openpyxl.reader import excel

    class HeadSizedWorksheet(excel.ReadOnlyWorksheet):  # type: ignore[misc]
        _get_size = _sheet_head_size

    excel.ReadOnlyWorksheet = HeadSizedWorksheet
    try:
        with zipfile.ZipFile(path) as archive:
            objects = unsupported_objects(archive.namelist())
        headers: list[dict[str, object]] = []
        with open(path, "rb") as handle:
            workbook = load_workbook(handle, read_only=True, data_only=False)
            try:
                for name in workbook.sheetnames[:MAX_SHEETS]:
                    sheet = workbook[name]
                    cells: list[str] = []
                    if hasattr(sheet, "iter_rows"):
                        for row in sheet.iter_rows(
                            min_row=1, max_row=1, min_col=1, max_col=HEADER_COLUMNS,
                            values_only=True,
                        ):
                            cells = [_header_text(value) for value in row]
                    while cells and not cells[-1].strip():
                        cells.pop()
                    headers.append({"name": name, "cells": cells})
            finally:
                workbook.close()
        return {"result": "ok", "headers": headers, "objects": objects}
    except Exception:
        return {"result": "chat_upload_unreadable"}


def inspect(path: str) -> dict[str, object]:
    try:
        _check_container(path)
        with zipfile.ZipFile(path) as archive:
            entries = _check_entries(archive)
            _check_parts(archive, entries)
            _inflate_all(archive, entries)
        return {"result": "ok", "sheets": _read_sheets(path)}
    except Rejected as rejected:
        return {"result": rejected.code}
    except zipfile.BadZipFile:
        return {"result": "chat_upload_type_invalid"}
    except Exception:
        # Malformed XML, forbidden DTDs or entities, bad CRCs, openpyxl failures.
        return {"result": "chat_upload_unreadable"}


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--headers":
        result = read_headers(sys.argv[2])
    elif len(sys.argv) == 2:
        result = inspect(sys.argv[1])
    else:
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=True))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
