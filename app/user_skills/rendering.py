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


def render_result(
    target: RunTarget,
    inputs: Mapping[str, Any],
    run: DefinitionRunResult | None,
    *,
    run_id: str,
    error_code: ErrorCode | None,
    queried_at: datetime,
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
        if total > MAX_DISPLAY_ROWS:
            rows = rows[:MAX_DISPLAY_ROWS]
            completeness = f"共 {total} 行，此处显示前 {MAX_DISPLAY_ROWS} 行，完整内容请导出"
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
    return result


__all__ = ("MAX_DISPLAY_ROWS", "derive_status", "reason_label", "render_result")
