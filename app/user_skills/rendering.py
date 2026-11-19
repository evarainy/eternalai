"""Deterministic result tables of one run (``data.user_skill`` ``kind == "result"``).

Rows and cells come only from projected leaf data by definition references; the
model never writes or rewrites them. Failed or skipped items keep their row with
a status and no dereferenced result values.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from app.ports.capability_gateway import ErrorCode
from app.user_skills.definition import ListRows
from app.workflow.definition_run import DefinitionReferenceError, resolve_ref
from app.workflow.models import DefinitionRef, DefinitionRunResult

if TYPE_CHECKING:
    from app.user_skills.runner import RunTarget

MAX_DISPLAY_ROWS = 500
RunStatus = Literal["completed", "partial", "failed"]

# User-facing reasons; codes stay in logs and the closed error set.
REASON_LABELS: dict[str, str] = {
    "adapter_timeout": "查询超时",
    "adapter_http_500": "系统暂时不可用",
    "adapter_error": "系统返回异常",
    "adapter_empty_response": "系统没有返回内容",
    "adapter_payload_invalid": "返回内容不符合要求",
    "adapter_missing_required_field": "缺少需要的数据",
    "capability_not_found": "依赖的查询已不可用",
    "capability_candidate_stale": "依赖的查询已变化",
    "identity_unbound": "需要先授权登录该系统",
    "identity_expired": "该系统登录已失效，请重新授权",
    "identity_revoked": "该系统授权已撤销，请重新授权",
    "needs_binding_scope": "需要先选择查询范围",
    "policy_denied": "无权限",
    "upstream_permission_denied": "业务系统无权限",
    "internal_error": "处理失败",
}
_STATUS_REASON = {"not_executed": "未执行"}


def reason_label(code: str | None) -> str:
    return REASON_LABELS.get(code or "", "处理失败")


def derive_status(run: DefinitionRunResult) -> RunStatus:
    if run.status in {"failed", "over_limit"}:
        return "failed"
    any_rows = False
    any_gap = False
    for outcome in run.steps.values():
        if outcome.status == "ok":
            any_rows = True
        if outcome.status != "ok" and run.status == "deadline":
            any_gap = True
        for item in outcome.items or ():
            if item.status != "ok":
                any_gap = True
    if run.status == "deadline":
        return "partial" if any_rows else "failed"
    if run.failed_assertion is not None and run.failed_assertion[1] == "complete":
        return "partial"
    return "partial" if any_gap else "completed"


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float, str)):
        return str(value)
    return ""


def _value(path: tuple[str | int, ...], source: Any) -> Any:
    try:
        return resolve_ref(DefinitionRef("loop_item", path), {}, {}, source)
    except DefinitionReferenceError:
        return None


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


# A user check that did not pass closes the run as failed with these closed codes.
CHECK_TRACE_CODES: dict[str, ErrorCode] = {
    "non_empty": "adapter_payload_invalid",
    "max_count": "adapter_payload_invalid",
}


def user_check_failure(target: RunTarget, run: DefinitionRunResult) -> str | None:
    """The reason when a user's ``non_empty``/``max_count`` check stopped the run.

    Only for a non-loop step whose checked value really is a list; a missing
    path or a non-list stays a data contract error, a loop limit keeps its own
    meaning, and ``complete`` is never a user check.
    """

    if run.failed_assertion is None:
        return None
    step_id, op = run.failed_assertion
    if op not in CHECK_TRACE_CODES:
        return None
    step = next((s for s in target.definition.steps if s.step_id == step_id), None)
    if step is None or step.for_each is not None:
        return None
    assertion = next((a for a in step.assertions if a.op == op), None)
    outcome = run.steps.get(step_id)
    if assertion is None or outcome is None or outcome.data is None:
        return None
    value = _value(tuple(assertion.ref.path), outcome.data)
    if not isinstance(value, list):
        return None
    if op == "non_empty":
        return "结果为空，未通过「列表不能为空」检查"
    return f"结果有 {len(value)} 条，超过「最多 {assertion.value} 条」检查"


def render_result(
    target: RunTarget,
    inputs: Mapping[str, Any],
    run: DefinitionRunResult | None,
    *,
    run_id: str,
    error_code: ErrorCode | None,
    queried_at: datetime,
    max_rows: int | None = MAX_DISPLAY_ROWS,
) -> dict[str, Any]:
    definition = target.definition
    labels = {param.name: param.label for param in definition.params}
    result: dict[str, Any] = {
        "kind": "result",
        "run_id": run_id,
        "skill_id": target.skill_id,
        "version": target.version,
        "name": target.name,
        "inputs": [
            {"label": labels[name], "value": _cell(value)}
            for name, value in inputs.items()
            if name in labels
        ],
        # The caller's own values by parameter name, for re-running or exporting.
        "input": {name: value for name, value in inputs.items() if name in labels},
        "queried_at": queried_at.isoformat(),
        "tables": [],
    }
    if run is None:
        result.update(status="failed", error_code=error_code,
                      reason_label=reason_label(error_code))
        return result
    status = derive_status(run)
    result["status"] = status
    if status == "failed":
        result["error_code"] = error_code or "internal_error"
        result["reason_label"] = (
            "列表超过上限，未执行后续逐项查询" if run.status == "over_limit"
            else reason_label(error_code)
        )
    if run.status == "empty":
        result["notice_label"] = "没有查到数据"
    projected = {
        step_id: outcome.data
        for step_id, outcome in run.steps.items()
        if outcome.status == "ok" and outcome.data is not None
    }
    steps = {step.step_id: step for step in definition.steps}
    mismatch = False
    for table in definition.output.tables:
        columns = [{"label": column.label} for column in table.columns]
        rows: list[dict[str, Any]] = []
        counts = {"ok": 0, "not_found": 0, "failed": 0, "not_executed": 0}
        completeness: str | None = None
        rows_spec = table.rows
        if isinstance(rows_spec, ListRows):
            ref = rows_spec.list
            source = projected.get(ref.step_id)
            records = None if source is None else _value(tuple(ref.path), source)
            if source is not None and not isinstance(records, list):
                # The saved list is missing from this result: not "no data".
                mismatch = True
            if not isinstance(records, list):
                records = []
            for record in records:
                rows.append({
                    "cells": [_cell(_value(tuple(c.ref.path), record)) for c in table.columns],
                    "status": "ok",
                })
                counts["ok"] += 1
            if run.failed_assertion == (ref.step_id, "complete"):
                completeness = "列表可能不完整"
        else:
            step = steps[rows_spec.for_each_step]
            outcome = run.steps.get(step.step_id)
            assert step.for_each is not None
            items_ref = step.for_each.items
            source = projected.get(items_ref.step_id)
            items = None if source is None else _value(tuple(items_ref.path), source)
            if not isinstance(items, list):
                items = []
            item_outcomes = {o.index: o for o in (outcome.items or ())} if outcome else {}
            for position, item in enumerate(items):
                item_outcome = item_outcomes.get(position)
                item_status = "not_executed" if item_outcome is None else item_outcome.status
                data = item_outcome.data if item_outcome is not None else None
                cells: list[str] = []
                result_values: list[Any] = []
                for column in table.columns:
                    if column.ref.source == "loop_item":
                        cells.append(_cell(_value(tuple(column.ref.path), item)))
                    elif item_status == "ok" and data is not None:
                        value = _value(tuple(column.ref.path), data)
                        result_values.append(value)
                        cells.append(_cell(value))
                    else:
                        cells.append("")
                row_status: str = item_status
                if item_status == "ok" and result_values and all(map(_empty, result_values)):
                    row_status = "not_found"
                row: dict[str, Any] = {"cells": cells, "status": row_status}
                if row_status == "failed":
                    row["reason_label"] = reason_label(
                        item_outcome.error_code if item_outcome else None
                    )
                elif row_status == "not_executed":
                    row["reason_label"] = _STATUS_REASON["not_executed"]
                rows.append(row)
                counts[row_status] += 1
        total = len(rows)
        if max_rows is not None and total > max_rows:
            rows = rows[:max_rows]
            completeness = f"共 {total} 行，此处显示前 {max_rows} 行，完整内容请导出"
        entry: dict[str, Any] = {
            "table_id": table.table_id,
            "title": table.title,
            "columns": columns,
            "rows": rows,
            "counts": counts,
        }
        if completeness is not None:
            entry["completeness_label"] = completeness
        result["tables"].append(entry)
    check = user_check_failure(target, run)
    if check is not None:
        # The check stopped the run: its reason wins over gaps the stop left elsewhere.
        assert run.failed_assertion is not None
        result.pop("notice_label", None)
        result.update(status="failed", check_failed=True, reason_label=check,
                      error_code=CHECK_TRACE_CODES[run.failed_assertion[1]])
        return result
    if mismatch:
        result.pop("notice_label", None)
        result.update(status="failed", error_code="adapter_payload_invalid",
                      reason_label=reason_label("adapter_payload_invalid"))
    return result


__all__ = (
    "CHECK_TRACE_CODES",
    "MAX_DISPLAY_ROWS",
    "derive_status",
    "reason_label",
    "render_result",
    "user_check_failure",
)
