"""Chat sheet filling: approved field catalog, plan v1 and its shared admission.

The model only proposes "column -> approved lookup key / field" mappings over row 1
headers of one chat attachment (row 1 is the fixed header row). ``validate_plan`` turns a
proposal into a plan bound to the attachment's digest, sheets, row ranges and header
texts; the chat tool and a direct ``POST /background-jobs`` go through the same
``SheetFillAdmission`` (owner, bound session, purpose, expiry, digest, headers, object
fidelity, OA binding and the fixed-document allow list). Cell values never come from the
model: the worker looks them up deterministically and writes only blank target cells.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app.background_jobs import (
    JobHandler,
    JobKindSpec,
    executed_by_worker,
    oa_binding_refusal,
)
from app.file_artifacts import FileArtifactService
from app.ports.auth import Principal
from app.ports.file_artifacts import (
    ChatAttachmentError,
    ChatInputInspection,
    FileArtifactError,
    FileArtifactOwner,
)
from app.ports.identity_mapping import IdentityMappingPort
from app.runtime.document_qa import DocumentQAService

SHEET_FILL_KIND = "sheet.fill_xlsx"
SheetFillSource = Literal["oa_pending", "document"]
SheetFillField = Literal[
    "status", "received_at", "created_at", "workflow_type_id", "todo_id", "quote"
]
# source -> (lookup key, fillable fields); a server constant the model can only pick from.
FIELD_CATALOG: Mapping[str, tuple[str, tuple[str, ...]]] = {
    "oa_pending": ("title", ("status", "received_at", "created_at", "workflow_type_id", "todo_id")),
    "document": ("question", ("quote",)),
}
MAX_MAPPINGS = 5
MAX_FILLS = 5
OA_MAX_ROWS = 2000
DOCUMENT_MAX_QUESTIONS = 30
HEADER_ROW = 1
MAX_EXCEL_ROW = 1048576
_COLUMN = re.compile(r"^[A-Z]{1,2}$")
_HEADER_COLUMNS = 50
_SHA256_HEX = r"^[0-9a-f]{64}$"

# Fixed, recoverable texts for the model; they never echo user data.
FIDELITY_MESSAGE = "表格含图表、图片等对象，首版暂不支持，请另存为仅含数据的表格后重试"
_SOURCE_CHANGED_MESSAGE = "附件已失效或不属于本次对话，请让用户重新上传后再试。"
_INVALID_MESSAGES = {
    "sheet": "工作表名称不在该附件中，请使用 untrusted_attachments 里的工作表名称。",
    "duplicate_sheet": "每个工作表只能有一个映射。",
    "source": (
        "source 只能是 oa_pending（本人 OA 待办，按标题查）"
        "或 document（固定文档问答，按问题查）。"
    ),
    "rows": "行范围需满足 2 ≤ first_row ≤ last_row ≤ 工作表行数（第 1 行是表头）。",
    "key_column": "key_column 必须是第 1 行表头非空的列字母。",
    "fill_column": "fills 的列必须是第 1 行表头非空的列字母，互不重复且不能等于 key_column。",
    "field": "field 不在所选来源的可填字段内。",
    "attachment": "attachment_id 不是本轮附件。",
    "coverage": (
        "untrusted_attachments 中还有含表头和数据行的工作表既不在 mappings 也不在 skipped_sheets："
        "能按 oa_pending 或 document 填写的放进 mappings，确实不需要填写的列入 skipped_sheets，"
        "再重新调用。"
    ),
}
_SIZE_UNKNOWN_MESSAGE = "该工作表的行列数未知，暂不能填写；请让用户另存后重新上传。"
_TOO_MANY_ROWS_MESSAGE = (
    "数据行超过上限：OA 待办每表最多 2000 行，文档问答每次合计最多 30 个问题；请缩小行范围。"
)


class SheetFillRejected(Exception):
    """A proposal or plan refused with a closed code and a fixed model-facing text."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


def column_index(letters: str) -> int:
    index = 0
    for character in letters:
        index = index * 26 + (ord(character) - ord("A") + 1)
    return index


# ---- Plan v1 (job params; the confirmation card shows and resubmits it) --------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SheetFillTarget(_Strict):
    column: StrictStr = Field(pattern=r"^[A-Z]{1,2}$")
    header: StrictStr = Field(min_length=1, max_length=65)
    field: SheetFillField


class SheetFillMapping(_Strict):
    sheet: StrictStr = Field(min_length=1, max_length=31)
    first_row: StrictInt = Field(ge=2, le=MAX_EXCEL_ROW)
    last_row: StrictInt = Field(ge=2, le=MAX_EXCEL_ROW)
    source: SheetFillSource
    key_column: StrictStr = Field(pattern=r"^[A-Z]{1,2}$")
    key_header: StrictStr = Field(min_length=1, max_length=65)
    fills: tuple[SheetFillTarget, ...] = Field(min_length=1, max_length=MAX_FILLS)


class SheetFillPlan(_Strict):
    plan_version: Literal[1]
    artifact_id: UUID
    sha256: StrictStr = Field(pattern=_SHA256_HEX)
    filename: StrictStr = Field(min_length=1, max_length=255)
    mappings: tuple[SheetFillMapping, ...] = Field(min_length=1, max_length=MAX_MAPPINGS)


def parse_sheet_fill_plan(raw: Mapping[str, Any]) -> BaseModel:
    return SheetFillPlan.model_validate(dict(raw))


# ---- Model proposal (tool input; checked field by field with fixed texts) ------------


class SheetFillProposalFill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: StrictStr = Field(max_length=8)
    field: StrictStr = Field(max_length=32)


class SheetFillProposalMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet: StrictStr = Field(max_length=64)
    first_row: StrictInt | None = None
    last_row: StrictInt | None = None
    source: StrictStr = Field(max_length=32)
    key_column: StrictStr = Field(max_length=8)
    fills: list[SheetFillProposalFill] = Field(min_length=1, max_length=MAX_FILLS)


class SheetFillProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attachment_id: UUID
    mappings: list[SheetFillProposalMapping] = Field(min_length=1, max_length=MAX_MAPPINGS)
    # Chat proposals only: sheets the model deliberately leaves blank (not part of the plan).
    skipped_sheets: list[StrictStr] = Field(default_factory=list, max_length=100)


PROPOSAL_INPUT_SCHEMA: dict[str, Any] = {  # fixed, code-owned schema
    "type": "object",
    "properties": {
        "attachment_id": {
            "type": "string", "description": "untrusted_attachments 中的 attachment_id",
        },
        "mappings": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_MAPPINGS,
            "items": {
                "type": "object",
                "properties": {
                    "sheet": {"type": "string", "description": "工作表名称"},
                    "first_row": {
                        "type": "integer", "minimum": 2, "description": "可省略，默认第 2 行",
                    },
                    "last_row": {
                        "type": "integer", "minimum": 2, "description": "可省略，默认最后一行",
                    },
                    "source": {"type": "string", "enum": ["oa_pending", "document"]},
                    "key_column": {"type": "string", "description": "查找键所在列字母，如 A"},
                    "fills": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_FILLS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string", "description": "要填写的列字母"},
                                "field": {
                                    "type": "string",
                                    "enum": [
                                        "status", "received_at", "created_at",
                                        "workflow_type_id", "todo_id", "quote",
                                    ],
                                },
                            },
                            "required": ["column", "field"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["sheet", "source", "key_column", "fills"],
                "additionalProperties": False,
            },
        },
        "skipped_sheets": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "不需要填写的工作表名称；每个有数据行的工作表都必须出现在 mappings 或这里"
            ),
        },
    },
    "required": ["attachment_id", "mappings"],
    "additionalProperties": False,
}


def _invalid(reason: str) -> SheetFillRejected:
    return SheetFillRejected("sheet_fill_proposal_invalid", _INVALID_MESSAGES[reason])


def _header_at(header: tuple[str, ...], letters: str) -> str | None:
    if _COLUMN.fullmatch(letters) is None:
        return None
    index = column_index(letters)
    if index > min(len(header), _HEADER_COLUMNS):
        return None
    text = header[index - 1]
    return text if text.strip() else None


def validate_plan(proposal: SheetFillProposal, inspection: ChatInputInspection) -> SheetFillPlan:
    """Pure check of a proposal against one inspected attachment; raises SheetFillRejected."""

    descriptor = inspection.descriptor
    if proposal.attachment_id != descriptor.artifact_id:
        raise _invalid("attachment")
    if inspection.unsupported_objects:
        raise SheetFillRejected("sheet_fill_fidelity_unsupported", FIDELITY_MESSAGE)
    sheets = {sheet.name: sheet for sheet in descriptor.sheets}
    seen: set[str] = set()
    questions = 0
    mappings: list[SheetFillMapping] = []
    for item in proposal.mappings:
        sheet = sheets.get(item.sheet)
        if sheet is None:
            raise _invalid("sheet")
        if item.sheet in seen:
            raise _invalid("duplicate_sheet")
        seen.add(item.sheet)
        if sheet.max_row is None or sheet.max_column is None or sheet.header is None:
            raise SheetFillRejected("sheet_fill_sheet_size_unknown", _SIZE_UNKNOWN_MESSAGE)
        catalog = FIELD_CATALOG.get(item.source)
        if catalog is None:
            raise _invalid("source")
        first = HEADER_ROW + 1 if item.first_row is None else item.first_row
        last = sheet.max_row if item.last_row is None else item.last_row
        if not HEADER_ROW + 1 <= first <= last <= sheet.max_row:
            raise _invalid("rows")
        rows = last - first + 1
        if item.source == "oa_pending" and rows > OA_MAX_ROWS:
            raise SheetFillRejected("sheet_fill_too_many_rows", _TOO_MANY_ROWS_MESSAGE)
        if item.source == "document":
            questions += rows
        key_column = item.key_column.strip().upper()
        key_header = _header_at(sheet.header, key_column)
        if key_header is None:
            raise _invalid("key_column")
        targets: list[SheetFillTarget] = []
        columns = {key_column}
        for fill in item.fills:
            column = fill.column.strip().upper()
            header = _header_at(sheet.header, column)
            if header is None or column in columns:
                raise _invalid("fill_column")
            columns.add(column)
            if fill.field not in catalog[1]:
                raise _invalid("field")
            targets.append(SheetFillTarget.model_validate(
                {"column": column, "header": header, "field": fill.field}
            ))
        mappings.append(SheetFillMapping(
            sheet=item.sheet, first_row=first, last_row=last,
            source=cast(SheetFillSource, item.source),  # checked against FIELD_CATALOG
            key_column=key_column, key_header=key_header, fills=tuple(targets),
        ))
    if questions > DOCUMENT_MAX_QUESTIONS:
        raise SheetFillRejected("sheet_fill_too_many_rows", _TOO_MANY_ROWS_MESSAGE)
    return SheetFillPlan(
        plan_version=1,
        artifact_id=descriptor.artifact_id,
        sha256=inspection.sha256,
        filename=descriptor.filename,
        mappings=tuple(mappings),
    )


def _require_coverage(proposal: SheetFillProposal, inspection: ChatInputInspection) -> None:
    """Every sheet with a header and data rows is either mapped or explicitly skipped."""

    named = {item.sheet for item in proposal.mappings} | set(proposal.skipped_sheets)
    for sheet in inspection.descriptor.sheets:
        has_rows = sheet.max_row is not None and sheet.max_row > HEADER_ROW
        has_header = sheet.header is not None and any(text.strip() for text in sheet.header)
        if has_rows and has_header and sheet.name not in named:
            raise _invalid("coverage")


def proposal_from_plan(plan: SheetFillPlan) -> SheetFillProposal:
    return SheetFillProposal(
        attachment_id=plan.artifact_id,
        mappings=[
            SheetFillProposalMapping(
                sheet=mapping.sheet,
                first_row=mapping.first_row,
                last_row=mapping.last_row,
                source=mapping.source,
                key_column=mapping.key_column,
                fills=[
                    SheetFillProposalFill(column=fill.column, field=fill.field)
                    for fill in mapping.fills
                ],
            )
            for mapping in plan.mappings
        ],
    )


_SOURCE_REFUSAL_MESSAGES = {
    "oa_unbound": "用户尚未在软件中心授权 OA，不能用 OA 待办填表；请提示用户先授权 OA。",
    "oa_unavailable": "OA 暂不可用，不能用 OA 待办填表；请提示用户稍后重试。",
    "sheet_fill_document_forbidden": "当前用户不能使用固定文档问答填表。",
}


class SheetFillAdmission:
    """Checks shared by the chat proposal tool and the job admission (API process)."""

    def __init__(
        self,
        *,
        files: FileArtifactService,
        identity_mapping: IdentityMappingPort | None,
        document_qa: DocumentQAService | None,
    ) -> None:
        self._files = files
        self._identity_mapping = identity_mapping
        self._document_qa = document_qa

    async def _inspect(
        self, principal: Principal, session_id: str, artifact_id: UUID
    ) -> ChatInputInspection:
        owner = FileArtifactOwner(
            tenant_id=principal.org_ctx.tenant_id, ai_user_id=principal.ai_user_id
        )
        try:
            return await self._files.inspect_chat_input(owner, session_id, artifact_id)
        except (ChatAttachmentError, FileArtifactError):
            raise SheetFillRejected(
                "sheet_fill_source_changed", _SOURCE_CHANGED_MESSAGE
            ) from None

    async def _source_refusal(self, principal: Principal, plan: SheetFillPlan) -> str | None:
        sources = {mapping.source for mapping in plan.mappings}
        if "document" in sources and (
            self._document_qa is None
            or not self._document_qa.permits(
                tenant_id=principal.org_ctx.tenant_id, ai_user_id=principal.ai_user_id
            )
        ):
            return "sheet_fill_document_forbidden"
        if "oa_pending" in sources:
            return await oa_binding_refusal(self._identity_mapping, principal)
        return None

    async def propose(
        self, principal: Principal, session_id: str, proposal: SheetFillProposal
    ) -> SheetFillPlan:
        inspection = await self._inspect(principal, session_id, proposal.attachment_id)
        plan = validate_plan(proposal, inspection)
        _require_coverage(proposal, inspection)
        refused = await self._source_refusal(principal, plan)
        if refused is not None:
            raise SheetFillRejected(refused, _SOURCE_REFUSAL_MESSAGES[refused])
        return plan

    async def admit(
        self, principal: Principal, session_id: str | None, params: BaseModel
    ) -> str | None:
        if session_id is None:
            return "report_session_required"
        assert isinstance(params, SheetFillPlan)
        try:
            inspection = await self._inspect(principal, session_id, params.artifact_id)
            if inspection.sha256 != params.sha256:
                return "sheet_fill_source_changed"
            plan = validate_plan(proposal_from_plan(params), inspection)
        except SheetFillRejected as rejected:
            if rejected.code == "sheet_fill_proposal_invalid":
                return "sheet_fill_source_changed"
            return rejected.code
        if plan != params:
            # Header texts, file name or defaults differ from what the card showed.
            return "sheet_fill_source_changed"
        return await self._source_refusal(principal, plan)


def sheet_fill_kind(
    tenant_id: str,
    *,
    admission: SheetFillAdmission | None = None,
    handler: JobHandler = executed_by_worker,
) -> JobKindSpec:
    """Read-only for external systems; the worker registers it without an admission."""

    return JobKindSpec(
        name=SHEET_FILL_KIND,
        external_effect="read_only",
        parse_params=parse_sheet_fill_plan,
        handler=handler,
        authorize=lambda principal: principal.org_ctx.tenant_id == tenant_id,
        admit=None if admission is None else admission.admit,
    )


__all__ = (
    "DOCUMENT_MAX_QUESTIONS",
    "FIDELITY_MESSAGE",
    "FIELD_CATALOG",
    "OA_MAX_ROWS",
    "PROPOSAL_INPUT_SCHEMA",
    "SHEET_FILL_KIND",
    "SheetFillAdmission",
    "SheetFillMapping",
    "SheetFillPlan",
    "SheetFillProposal",
    "SheetFillRejected",
    "SheetFillTarget",
    "column_index",
    "parse_sheet_fill_plan",
    "proposal_from_plan",
    "sheet_fill_kind",
    "validate_plan",
)
