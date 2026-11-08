"""Worker side of the sheet fill kind (P2-SHEET-FILL-001).

The worker re-resolves the confirmed attachment for the requester's own bound chat
session, then orchestrates: ``fill_child extract`` (lookup keys and blank targets) -> one
OA pending read with the requester's own saved OA session and/or one fixed-document
question per row (cancellation is checked between questions) -> ``fill_child fill``
(write, save, read-back verification) -> publication. Workbook parsing happens only in the
isolated child. Document cells receive only ``DocumentQAResult.verbatim`` document lines,
never model text. Logs carry stage, job, counts, elapsed time and closed codes only:
never cell values, headers, questions, file names or document text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.background_jobs import JobExecution, JobKindSpec, JobOutcome
from app.config import OAReportWorkerSettings, WorkerDocumentQASettings
from app.file_artifacts import FileArtifactService
from app.infra.adapters.oa.contracts import OAPendingWorkflow
from app.infra.auth.postgresql import PostgreSQLPrincipalRoleReader
from app.infra.jobs.oa_pending_report import build_worker_pending_gateway, read_pending_workflows
from app.ports.background_jobs import BackgroundJobError
from app.ports.capability_gateway import CapabilityGatewayPort
from app.ports.file_artifacts import (
    CHAT_UPLOAD_MAX_BYTES,
    XLSX_MEDIA_TYPE,
    ChatAttachmentError,
    ChunkWriter,
    FileArtifactError,
    FileArtifactOwner,
)
from app.runtime.document_qa import DocumentQAService
from app.sheet_fill import SheetFillMapping, SheetFillPlan, sheet_fill_kind

_LOGGER = logging.getLogger("uvicorn.error.background_jobs")
_CHILD = (Path(__file__).resolve().parents[1] / "spreadsheets" / "fill_child.py").resolve()
_ENVIRONMENT = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH")
_HEADER = struct.Struct(">I")
CHILD_TIMEOUT_SECONDS = 120.0
_MAX_CHILD_OUTPUT_BYTES = 96 * 1024 * 1024
MAX_CELL_CHARACTERS = 32767
NOT_FOUND = "（未找到）"
MULTIPLE = "（匹配到多条）"
DOC_NO_EVIDENCE = "（文档中未找到）"
DOC_FAILED = "（文档问答失败）"
TOO_LONG = "（原文过长）"
_CHILD_FAILURES = {
    "source_changed": "sheet_fill_source_changed",
    "fidelity": "sheet_fill_fidelity_unsupported",
    "verify_failed": "sheet_fill_verify_failed",
}
# Closed mark categories -> result_summary counters.
_MARK_KEYS = {
    "not_found": "marked_not_found",
    "multiple": "marked_multiple",
    "doc_no_evidence": "marked_doc_no_evidence",
    "doc_failed": "marked_doc_failed",
    "too_long": "marked_too_long",
}


def _log(
    stage: str, job: object, *, rows: object = "-", filled: object = "-",
    marked: object = "-", started: float | None = None, code: str = "-",
) -> None:
    elapsed = "-" if started is None else int((monotonic() - started) * 1000)
    _LOGGER.info(
        "sheet_fill stage=%s job=%s rows=%s filled=%s marked=%s elapsed_ms=%s code=%s",
        stage, job, rows, filled, marked, elapsed, code,
    )


def _communicate(process: subprocess.Popen[bytes], payload: bytes) -> bytes:
    try:
        stdout, _ = process.communicate(payload, timeout=CHILD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    return stdout


async def run_fill_child(
    command: Mapping[str, Any], content: bytes
) -> tuple[dict[str, Any], bytes]:
    """One child pass; killed on timeout or when the job is cancelled.

    Popen plus a thread (not asyncio subprocesses): the worker loop is a Selector loop.
    """

    encoded = json.dumps(command, ensure_ascii=False).encode("utf-8")
    environment = {name: os.environ[name] for name in _ENVIRONMENT if name in os.environ}
    process = subprocess.Popen(
        [sys.executable, "-I", str(_CHILD)],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    work = asyncio.ensure_future(
        asyncio.to_thread(_communicate, process, _HEADER.pack(len(encoded)) + encoded + content)
    )
    try:
        stdout = await asyncio.shield(work)
    except asyncio.CancelledError:
        process.kill()
        await asyncio.wait({work})
        raise
    except subprocess.TimeoutExpired:
        raise BackgroundJobError("sheet_fill_child_failed") from None
    try:
        if process.returncode != 0 or len(stdout) > _MAX_CHILD_OUTPUT_BYTES:
            raise ValueError
        (length,) = _HEADER.unpack_from(stdout)
        result = json.loads(stdout[_HEADER.size:_HEADER.size + length])
        if not isinstance(result, dict):
            raise ValueError
    except (ValueError, struct.error):
        raise BackgroundJobError("sheet_fill_child_failed") from None
    code = result.get("result")
    if code != "ok":
        raise BackgroundJobError(_CHILD_FAILURES.get(str(code), "sheet_fill_child_failed"))
    return result, stdout[_HEADER.size + length:]


@dataclass(frozen=True)
class _Row:
    row: int
    key: str
    blank: tuple[bool, ...]


def _extracted(result: dict[str, Any], plan: SheetFillPlan) -> list[list[_Row]]:
    """Strict shape check of the extract result against the plan."""

    try:
        items = result["mappings"]
        if set(result) != {"result", "mappings"} or len(items) != len(plan.mappings):
            raise ValueError
        extracted: list[list[_Row]] = []
        for item, mapping in zip(items, plan.mappings, strict=True):
            rows = item["rows"]
            if item["sheet"] != mapping.sheet or len(rows) != (
                mapping.last_row - mapping.first_row + 1
            ):
                raise ValueError
            parsed: list[_Row] = []
            for expected, row in enumerate(rows, start=mapping.first_row):
                blank = tuple(row["blank"])
                if (
                    row["row"] != expected
                    or type(row["key"]) is not str
                    or len(blank) != len(mapping.fills)
                    or any(type(flag) is not bool for flag in blank)
                ):
                    raise ValueError
                parsed.append(_Row(expected, row["key"], blank))
            extracted.append(parsed)
    except (KeyError, TypeError, ValueError):
        raise BackgroundJobError("sheet_fill_child_failed") from None
    return extracted


def _child_mappings(plan: SheetFillPlan) -> list[dict[str, Any]]:
    return [
        {
            "sheet": mapping.sheet,
            "first_row": mapping.first_row,
            "last_row": mapping.last_row,
            "key_column": mapping.key_column,
            "columns": [fill.column for fill in mapping.fills],
        }
        for mapping in plan.mappings
    ]


def result_filename(original: str, finished_at: datetime) -> str:
    stem = original[:-5] if original.lower().endswith(".xlsx") else original
    return f"{stem[:180]}_已填写_{finished_at:%Y%m%d_%H%M}.xlsx"


class SheetFillHandler:
    def __init__(
        self,
        files: FileArtifactService,
        *,
        oa_gateway: CapabilityGatewayPort | None,
        role_reader: PostgreSQLPrincipalRoleReader | None,
        document_qa: DocumentQAService | None,
        clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        self._files = files
        self._oa_gateway = oa_gateway
        self._roles = role_reader
        self._document_qa = document_qa
        self._clock = clock

    async def __call__(self, params: BaseModel, execution: JobExecution) -> JobOutcome:
        assert isinstance(params, SheetFillPlan)
        started = monotonic()
        try:
            return await self._run(params, execution, started)
        except BackgroundJobError as error:
            _log("failed", execution.job_id, started=started, code=error.code)
            raise
        except asyncio.CancelledError:
            _log("failed", execution.job_id, started=started, code="cancelled")
            raise
        except Exception:
            _log("failed", execution.job_id, started=started, code="handler_failed")
            raise

    def _check_sources(self, plan: SheetFillPlan, tenant_id: str, ai_user_id: str) -> None:
        sources = {mapping.source for mapping in plan.mappings}
        if "document" in sources:
            if self._document_qa is None:
                raise BackgroundJobError("sheet_fill_document_unavailable")
            if not self._document_qa.permits(tenant_id=tenant_id, ai_user_id=ai_user_id):
                raise BackgroundJobError("sheet_fill_document_forbidden")
        if "oa_pending" in sources and (self._oa_gateway is None or self._roles is None):
            raise BackgroundJobError("oa_unavailable")

    async def _run(
        self, plan: SheetFillPlan, execution: JobExecution, started: float
    ) -> JobOutcome:
        owner, outputs, job_id = execution.owner, execution.outputs, execution.job_id
        if owner is None or job_id is None or owner.session_id is None:
            raise BackgroundJobError("report_session_required")
        if outputs is None:
            raise BackgroundJobError("file_artifacts_unavailable")
        self._check_sources(plan, owner.tenant_id, owner.ai_user_id)
        try:
            content = await self._files.read_chat_input(
                FileArtifactOwner(tenant_id=owner.tenant_id, ai_user_id=owner.ai_user_id),
                owner.session_id,
                plan.artifact_id,
            )
        except (ChatAttachmentError, FileArtifactError):
            raise BackgroundJobError("sheet_fill_source_changed") from None
        if len(content) > CHAT_UPLOAD_MAX_BYTES:
            raise BackgroundJobError("sheet_fill_source_changed")
        _log("read", job_id, started=started)

        mappings = _child_mappings(plan)
        extract, _ = await run_fill_child(
            {"mode": "extract", "sha256": plan.sha256, "mappings": mappings}, content
        )
        extracted = _extracted(extract, plan)
        total_rows = sum(len(rows) for rows in extracted)
        _log("extract", job_id, rows=total_rows, started=started)

        skipped_nonempty = 0
        # (mapping, row, blank fills) for rows with a lookup key and a blank target.
        work: list[tuple[SheetFillMapping, _Row, list[int]]] = []
        for mapping, rows in zip(plan.mappings, extracted, strict=True):
            for row in rows:
                if not row.key.strip():
                    continue
                skipped_nonempty += row.blank.count(False)
                targets = [index for index, blank in enumerate(row.blank) if blank]
                if targets:
                    work.append((mapping, row, targets))

        oa_index: dict[str, list[OAPendingWorkflow]] | None = None
        if any(mapping.source == "oa_pending" for mapping, _, _ in work):
            assert self._oa_gateway is not None and self._roles is not None
            collection = await read_pending_workflows(
                self._oa_gateway, self._roles, owner, task_id=f"sheet-fill-job:{job_id}"
            )
            oa_index = {}
            for item in collection.workflows:
                oa_index.setdefault(item.title.strip(), []).append(item)
            _log("oa_read", job_id, rows=len(collection.workflows), started=started)

        writes: list[dict[str, Any]] = []
        categories: list[str | None] = []  # None = filled value, else a mark category
        questions = 0
        for mapping, row, targets in work:
            key = row.key.strip()
            values: dict[int, tuple[str, str | None]] = {}
            if mapping.source == "oa_pending":
                assert oa_index is not None
                matches = oa_index.get(key, [])
                for index in targets:
                    if not matches:
                        values[index] = (NOT_FOUND, "not_found")
                    elif len(matches) > 1:
                        values[index] = (MULTIPLE, "multiple")
                    else:
                        values[index] = (getattr(matches[0], mapping.fills[index].field), None)
            else:
                assert self._document_qa is not None
                questions += 1
                answer = await self._document_qa.answer(
                    question=key, tenant_id=owner.tenant_id, ai_user_id=owner.ai_user_id,
                    trace_id=str(job_id),
                )
                if answer.outcome == "answered" and answer.verbatim:
                    text, category = "\n\n".join(answer.verbatim), None
                    if len(text) > MAX_CELL_CHARACTERS:
                        text, category = TOO_LONG, "too_long"
                elif answer.outcome == "failed":
                    text, category = DOC_FAILED, "doc_failed"
                else:
                    text, category = DOC_NO_EVIDENCE, "doc_no_evidence"
                for index in targets:
                    values[index] = (text, category)
            for index in targets:
                text, category = values[index]
                writes.append({
                    "sheet": mapping.sheet, "row": row.row,
                    "column": mapping.fills[index].column, "text": text,
                    "mark": category is not None,
                })
                categories.append(category)
        if questions:
            _log("doc_qa", job_id, rows=questions, started=started)

        fill, result = await run_fill_child(
            {"mode": "fill", "sha256": plan.sha256, "mappings": mappings, "writes": writes},
            content,
        )
        skipped = fill.get("skipped")
        if (
            not isinstance(skipped, list)
            or any(type(index) is not int or not 0 <= index < len(writes) for index in skipped)
            or fill.get("written") != len(writes) - len(set(skipped))
        ):
            raise BackgroundJobError("sheet_fill_child_failed")
        for index in set(skipped):
            categories[index] = "skipped"
        skipped_nonempty += len(set(skipped))
        counts = Counter(categories)
        filled = counts[None]
        marked = sum(counts[category] for category in _MARK_KEYS)
        _log("fill", job_id, rows=total_rows, filled=filled, marked=marked, started=started)
        _log("verify", job_id, rows=total_rows, filled=filled, marked=marked, started=started)

        async def write(emit: ChunkWriter) -> None:
            await emit(result)

        try:
            artifact_id = await outputs.publish(
                write, result_filename(plan.filename, self._clock()), XLSX_MEDIA_TYPE
            )
        except FileArtifactError as error:
            _LOGGER.warning("sheet_fill_publish_failed job=%s code=%s", job_id, error.code)
            raise BackgroundJobError("report_publish_failed") from None
        _log("publish", job_id, rows=total_rows, filled=filled, marked=marked, started=started)
        _log("done", job_id, rows=total_rows, filled=filled, marked=marked,
             started=started, code="ok")
        return JobOutcome(
            result_code="ok",
            result_summary={
                "artifact_id": str(artifact_id),
                "filled_cells": filled,
                "skipped_nonempty": skipped_nonempty,
                **{key: counts[category] for category, key in _MARK_KEYS.items()},
                "verified": True,
            },
        )


def build_document_qa(settings: WorkerDocumentQASettings) -> DocumentQAService:
    from app.infra.fixed_txt import build_document_qa_service
    from app.infra.llm.json_structured_output import JSONStructuredOutputProvider
    from app.infra.llm.openai_compatible import OpenAICompatibleLLMProvider

    return build_document_qa_service(
        settings.fixed_document,
        llm_provider=OpenAICompatibleLLMProvider(
            base_url=settings.llm_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
            top_p=settings.llm_top_p,
            top_k=settings.llm_top_k,
            enable_thinking=settings.llm_enable_thinking,
        ),
        structured_output=JSONStructuredOutputProvider(),
        model=settings.llm_model,
    )


def build_worker_sheet_fill_kind(
    files: FileArtifactService,
    sessions: async_sessionmaker[AsyncSession],
    *,
    oa_report: OAReportWorkerSettings | None,
    document_qa: WorkerDocumentQASettings | None,
) -> JobKindSpec:
    """Sources the worker is not configured for refuse only the jobs that use them."""

    return sheet_fill_kind(
        # Admission and tenant authorization run in the API; the worker only executes.
        "" if oa_report is None else oa_report.oa_read.source_tenant_id,
        handler=SheetFillHandler(
            files,
            oa_gateway=None if oa_report is None
            else build_worker_pending_gateway(oa_report, sessions),
            role_reader=None if oa_report is None
            else PostgreSQLPrincipalRoleReader(session_factory=sessions),
            document_qa=None if document_qa is None else build_document_qa(document_qa),
        ),
    )


__all__ = (
    "CHILD_TIMEOUT_SECONDS",
    "SheetFillHandler",
    "build_worker_sheet_fill_kind",
    "result_filename",
    "run_fill_child",
)
