"""Save proposal: value-free summary → model choices → program checks → card.

The program, not the model, computes every argument's candidate sources from
this session's record (equal values are clues only; the user confirms each
non-typed binding on the card). Fixed values and parameter schemas come only
from the leaf capability contract, and no persisted text may contain a value
seen in the record.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ports.capability_registry import CapabilitySpec
from app.user_skills.definition import (
    FORMAT,
    HARD_MAX_ITEMS,
    LABEL_MAX,
    NAME_MAX,
    PURPOSE_MAX,
    RULES_MAX,
    UserSkillDefinition,
    text_slots,
)
from app.user_skills.session_store import RecordEntry

Path = tuple[str | int, ...]
_SCHEMA_KEYS = ("type", "format", "minLength", "maxLength", "pattern", "minimum", "maximum",
                "enum")
MAX_LIST_ITEMS = 50
_PARTIAL_WINDOW = 5
_PARTIAL_SAMPLE_MIN = 8


class ProposalRejected(ValueError):
    def __init__(self, code: str, label: str) -> None:
        super().__init__(code)
        self.code = code
        self.label = label


# ---------------------------------------------------------------- model input
class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Short = Annotated[str, Field(min_length=1, max_length=LABEL_MAX)]
PathIn = Annotated[list[str | int], Field(max_length=8)]


class ModelStep(_M):
    record_seqs: Annotated[list[int], Field(min_length=1, max_length=HARD_MAX_ITEMS)]
    label: Short


class ModelColumn(_M):
    source: Literal["row", "loop_item", "item_result"]
    path: PathIn
    label: Short


class ModelTable(_M):
    title: Short
    step: int = Field(ge=0, le=5)
    list_path: PathIn | None = None
    columns: Annotated[list[ModelColumn], Field(min_length=1, max_length=20)]


class ModelPreference(_M):
    step: int = Field(ge=0, le=5)
    arg: Annotated[str, Field(min_length=1, max_length=100)]
    choice: Literal["param", "fixed", "step_output", "loop_item"]
    label: Short | None = None


class ModelFieldLabel(_M):
    step: int = Field(ge=0, le=5)
    path: PathIn
    label: Short


class ModelProposal(_M):
    name: Annotated[str, Field(min_length=1, max_length=NAME_MAX)]
    purpose: Annotated[str, Field(min_length=1, max_length=PURPOSE_MAX)]
    rules: Annotated[str, Field(min_length=1, max_length=RULES_MAX)]
    steps: Annotated[list[ModelStep], Field(min_length=1, max_length=6)]
    tables: Annotated[list[ModelTable], Field(min_length=1, max_length=3)]
    preferences: Annotated[list[ModelPreference], Field(max_length=60)] = []
    field_labels: Annotated[list[ModelFieldLabel], Field(max_length=60)] = []


def model_proposal_schema() -> dict[str, Any]:
    """Input schema of the ``propose_user_skill`` tool (native tool call)."""

    return ModelProposal.model_json_schema()


# -------------------------------------------------------------- value walking
def _scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) and value is not None


def scalar_paths(value: Any, prefix: Path = ()) -> Iterator[tuple[Path, Any]]:
    """Scalar leaves reachable through objects only (lists are not crossed)."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from scalar_paths(item, (*prefix, str(key)))
    elif _scalar(value):
        yield prefix, value


def list_paths(value: Any, prefix: Path = ()) -> Iterator[tuple[Path, list[Any]]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from list_paths(item, (*prefix, str(key)))
    elif isinstance(value, list) and value and all(isinstance(i, Mapping) for i in value):
        yield prefix, value


def get_path(value: Any, path: Sequence[str | int]) -> Any:
    for segment in path:
        if isinstance(segment, int) and isinstance(value, list) and 0 <= segment < len(value):
            value = value[segment]
        elif isinstance(segment, str) and isinstance(value, Mapping) and segment in value:
            value = value[segment]
        else:
            return _MISSING
    return value


_MISSING = object()


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, Mapping):
        return "object"
    return "string"


def _norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split()).casefold()


# ------------------------------------------------------------------- summary
def summarize(
    records: Sequence[RecordEntry],
    specs: Mapping[str, CapabilitySpec],
    user_texts: Sequence[str],
) -> dict[str, Any]:
    """Value-free summary for the model: names, types, paths, lengths, hints."""

    normalized_user = [_norm(text) for text in user_texts]
    items: list[dict[str, Any]] = []
    hints: list[str] = []
    for record in records:
        spec = specs.get(record.capability_id)
        output = [
            {"path": list(path), "type": _type_name(value)}
            for path, value in scalar_paths(record.data)
        ]
        lists = []
        for path, values in list_paths(record.data):
            fields = sorted({".".join(map(str, p)) for v in values for p, _ in scalar_paths(v)})
            lists.append({"path": list(path), "length": len(values), "item_fields": fields[:40]})
        items.append(
            {
                "seq": record.seq,
                "query": spec.name if spec is not None else "unknown",
                "arguments": [
                    {"name": name, "type": _type_name(value)}
                    for name, value in record.arguments.items()
                ],
                "output_fields": output[:60],
                "output_lists": lists[:10],
            }
        )
        for name, value in record.arguments.items():
            if isinstance(value, str) and any(_norm(value) and _norm(value) in u
                                              for u in normalized_user):
                hints.append(f"第{record.seq}条的入参 {name} 与用户消息中说过的内容相同")
            for earlier in records:
                if earlier.seq >= record.seq:
                    continue
                for path, values in list_paths(earlier.data):
                    for element in values:
                        for field_path, field_value in scalar_paths(element):
                            if field_value == value:
                                hints.append(
                                    f"第{record.seq}条的入参 {name} 等于第{earlier.seq}条结果列表 "
                                    f"{'.'.join(map(str, path))} 中某项的 "
                                    f"{'.'.join(map(str, field_path))}"
                                )
                                break
                for path, scalar in scalar_paths(earlier.data):
                    if scalar == value:
                        hints.append(
                            f"第{record.seq}条的入参 {name} 等于第{earlier.seq}条结果的 "
                            f"{'.'.join(map(str, path))}"
                        )
    return {"records": items, "hints": sorted(set(hints))[:80]}


# ------------------------------------------------------------ candidate model
@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    kind: Literal["param", "fixed", "omit", "step_output", "loop_item"]
    label: str
    schema: dict[str, Any] | None = None
    value: Any = None
    step: int | None = None
    path: Path = ()
    list_path: Path = ()


def _arg_schema(spec: CapabilitySpec, arg: str) -> dict[str, Any] | None:
    properties = spec.input_schema.get("properties")
    if not isinstance(properties, Mapping) or not isinstance(properties.get(arg), Mapping):
        return None
    source = properties[arg]
    return {key: source[key] for key in _SCHEMA_KEYS if key in source}


def _fixed_kind(spec: CapabilitySpec, arg: str, value: Any) -> str | None:
    properties = spec.input_schema.get("properties")
    prop = properties.get(arg) if isinstance(properties, Mapping) else None
    if not isinstance(prop, Mapping):
        return None
    if "default" in prop and prop["default"] == value:
        return "omit"
    if isinstance(prop.get("enum"), list) and value in prop["enum"]:
        return "fixed"
    if "const" in prop and prop["const"] == value:
        return "fixed"
    if prop.get("type") == "boolean" and isinstance(value, bool):
        return "fixed"
    return None


def _contract_values(spec: CapabilitySpec) -> set[str]:
    """Enum/const literals of the contract; excluded from the sample-value check."""

    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key in ("enum",):
                if isinstance(node.get(key), list):
                    found.update(_norm(str(v)) for v in node[key])
            if "const" in node:
                found.add(_norm(str(node["const"])))
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(spec.input_schema)
    walk(spec.output_schema)
    return found


def sample_values(records: Sequence[RecordEntry], exempt: set[str]) -> set[str]:
    values: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, Mapping):
            for item in node.values():
                collect(item)
        elif isinstance(node, list):
            for item in node:
                collect(item)
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, (str, int, float)):
            text = _norm(str(node))
            if len(text) >= 2 and text not in exempt:
                values.add(text)

    for record in records:
        collect(record.arguments)
        collect(record.data)
    return values


def check_texts(texts: Sequence[str], samples: set[str]) -> None:
    long_samples = [sample for sample in samples if len(sample) >= _PARTIAL_SAMPLE_MIN]
    for text in texts:
        normalized = _norm(text)
        windows = {
            normalized[i : i + _PARTIAL_WINDOW]
            for i in range(len(normalized) - _PARTIAL_WINDOW + 1)
        }
        # Whole values, and a 5-character piece of a long value, both count.
        if any(sample in normalized for sample in samples) or any(
            window in sample for window in windows for sample in long_samples
        ):
            raise ProposalRejected(
                "sample_value_in_text", "名称、说明或标题里出现了本次查询的具体内容，请改成一般说法"
            )


# ------------------------------------------------------------------- proposal
@dataclass(frozen=True)
class _Step:
    capability_id: str
    fingerprint: str
    label: str
    seqs: tuple[int, ...]


def build_proposal(
    records: Sequence[RecordEntry],
    raw: Mapping[str, Any],
    specs: Mapping[str, CapabilitySpec],
    fingerprints: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (card view, internal snapshot). Raises ``ProposalRejected``."""

    try:
        model = ModelProposal.model_validate(raw)
    except ValidationError:
        raise ProposalRejected(
            "proposal_invalid", "提案内容不完整，请重新说「保存为功能」"
        ) from None
    by_seq = {record.seq: record for record in records}
    used: set[int] = set()
    steps: list[_Step] = []
    for item in model.steps:
        found = [by_seq.get(seq) for seq in item.record_seqs]
        if any(entry is None for entry in found) or used & set(item.record_seqs):
            raise ProposalRejected("proposal_invalid", "提案引用了不存在的查询记录")
        entries = [entry for entry in found if entry is not None]
        capability_ids = {entry.capability_id for entry in entries}
        if len(capability_ids) != 1:
            raise ProposalRejected("proposal_invalid", "同一步里混入了不同的查询")
        capability_id = capability_ids.pop()
        spec = specs.get(capability_id)
        recorded = {entry.capability_fingerprint for entry in entries}
        if spec is None or recorded != {fingerprints.get(capability_id)}:
            raise ProposalRejected("capability_changed", "依赖的查询已经变化，请重新查询一次再保存")
        used.update(item.record_seqs)
        steps.append(_Step(capability_id, recorded.pop(), item.label, tuple(item.record_seqs)))
    exempt: set[str] = set()
    for spec in specs.values():
        exempt |= _contract_values(spec)
    samples = sample_values(records, exempt)
    field_labels = {(f.step, tuple(f.path)): f.label for f in model.field_labels}
    check_texts(
        [model.name, model.purpose, model.rules, *(s.label for s in steps),
         *(t.title for t in model.tables), *(c.label for t in model.tables for c in t.columns),
         *field_labels.values(), *(p.label for p in model.preferences if p.label)],
        samples,
    )

    def field_label(step: int, path: Path) -> str:
        spec = specs[steps[step].capability_id]
        label = field_labels.get((step, path))
        if label:
            return label
        node: Any = spec.output_schema
        for segment in path:
            if not isinstance(node, Mapping):
                break
            if node.get("type") == "array":
                node = node.get("items")
            properties = node.get("properties") if isinstance(node, Mapping) else None
            node = properties.get(segment) if isinstance(properties, Mapping) else None
        if isinstance(node, Mapping):
            for key in ("title", "description"):
                if isinstance(node.get(key), str) and 0 < len(node[key]) <= LABEL_MAX:
                    return str(node[key])
        return "其中一项数据"

    preferences = {(p.step, p.arg): p for p in model.preferences}
    bindings: list[dict[str, Any]] = []
    internal_bindings: list[dict[str, Any]] = []
    params_view: list[dict[str, Any]] = []
    offers: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        spec = specs[step.capability_id]
        group = [by_seq[seq] for seq in step.seqs]
        names = sorted({name for entry in group for name in entry.arguments})
        step_loop_candidates = False
        for arg in names:
            values = [entry.arguments.get(arg) for entry in group]
            candidates: list[Candidate] = []
            schema = _arg_schema(spec, arg)
            binding_id = f"b{len(internal_bindings) + 1}"
            pref = preferences.get((index, arg))
            arg_label = (pref.label if pref and pref.label else None) or _input_label(spec, arg)
            if schema is not None:
                candidates.append(Candidate(f"{binding_id}.param", "param", "每次填写", schema))
            if len({json.dumps(v, sort_keys=True, default=str) for v in values}) == 1:
                kind = _fixed_kind(spec, arg, values[0])
                if kind == "fixed":
                    candidates.append(Candidate(f"{binding_id}.fixed", "fixed", "固定不变",
                                                value=values[0]))
                elif kind == "omit":
                    candidates.append(Candidate(f"{binding_id}.fixed", "omit", "使用系统默认",
                                                value=values[0]))
            for earlier in range(index):
                if len(steps[earlier].seqs) != 1:
                    continue
                source = by_seq[steps[earlier].seqs[0]].data
                if len(group) == 1:
                    for path, scalar in scalar_paths(source):
                        if scalar == values[0]:
                            source_label = (
                                f"来自「{steps[earlier].label}」结果的"
                                f"「{field_label(earlier, path)}」"
                            )
                            candidates.append(Candidate(
                                f"{binding_id}.s{earlier}.{len(candidates)}", "step_output",
                                source_label, step=earlier, path=path,
                            ))
                for list_path, elements in list_paths(source):
                    fields = {p for element in elements for p, _ in scalar_paths(element)}
                    for field_path in sorted(fields, key=str):
                        pool = [get_path(element, field_path) for element in elements]
                        if all(value in pool for value in values):
                            candidates.append(Candidate(
                                f"{binding_id}.l{earlier}.{len(candidates)}", "loop_item",
                                f"对「{steps[earlier].label}」的每一条，取其「"
                                f"{field_label(earlier, (*list_path, *field_path))}」",
                                step=earlier, path=field_path, list_path=list_path,
                            ))
                            step_loop_candidates = True
            if not candidates:
                raise ProposalRejected("binding_unresolvable", f"「{arg_label}」无法确定怎么填写")
            prefill = _prefill(candidates, pref, len(group))
            bindings.append({
                "binding_id": binding_id,
                "step_index": index,
                "step_label": step.label,
                "arg_label": arg_label,
                "source_kind": prefill.kind if prefill else "undetermined",
                "source_label": prefill.label if prefill else "来源未定，请选择",
                "candidates": [{"candidate_id": c.candidate_id, "kind": c.kind, "label": c.label}
                               for c in candidates],
                "prefill": prefill.candidate_id if prefill else None,
            })
            param = next((c for c in candidates if c.kind == "param"), None)
            if param is not None:
                params_view.append({"binding_id": binding_id, "label": arg_label,
                                    "schema": param.schema, "required": True})
            internal_bindings.append({
                "binding_id": binding_id, "step": index, "arg": arg, "label": arg_label,
                "candidates": [c.__dict__ for c in candidates],
            })
        if step_loop_candidates:
            offers.append({"offer_id": f"f{index}", "step_index": index, "step_label": step.label,
                           "list_label": "前一步列表里的每一条", "prefilled": len(step.seqs) > 1})
    columns_view: list[dict[str, Any]] = []
    tables_internal: list[dict[str, Any]] = []
    for t_index, table in enumerate(model.tables):
        if table.step >= len(steps):
            raise ProposalRejected("proposal_invalid", "表格引用了不存在的步骤")
        table_id = f"t{t_index + 1}"
        tables_internal.append({"table_id": table_id, **table.model_dump(mode="json")})
        for c_index, column in enumerate(table.columns):
            columns_view.append({"column_id": f"{table_id}.c{c_index + 1}",
                                 "table_id": table_id, "label": column.label})
    record_digest = hashlib.sha256(
        json.dumps([(r.seq, r.capability_fingerprint) for r in records]).encode()
    ).hexdigest()
    proposal_id = str(uuid4())
    view = {
        "kind": "proposal",
        "proposal_id": proposal_id,
        "steps": [{"step_label": s.label, "system_label": _system_label(specs[s.capability_id])}
                  for s in steps],
        "params": params_view,
        "bindings": bindings,
        "for_each_offers": offers,
        "column_options": columns_view,
        "draft": {"name": model.name, "purpose": model.purpose, "rules": model.rules},
        "update_targets": [],
        "rejections": [],
    }
    internal = {
        "proposal_id": proposal_id,
        "record_digest": record_digest,
        "steps": [s.__dict__ for s in steps],
        "bindings": internal_bindings,
        "tables": tables_internal,
        "samples": sorted(samples),
    }
    return view, internal


def _input_label(spec: CapabilitySpec, arg: str) -> str:
    properties = spec.input_schema.get("properties")
    prop = properties.get(arg) if isinstance(properties, Mapping) else None
    if isinstance(prop, Mapping):
        for key in ("title", "description"):
            if isinstance(prop.get(key), str) and 0 < len(prop[key]) <= LABEL_MAX:
                return str(prop[key])
    return "查询条件"


def _system_label(spec: CapabilitySpec) -> str:
    return {"oa": "OA", "u8": "财务系统", "hikvision_ivms": "安防系统",
            "business_platform": "业务平台"}.get(spec.target_system or "", "业务系统")


def _prefill(
    candidates: Sequence[Candidate], pref: ModelPreference | None, group_size: int
) -> Candidate | None:
    if pref is not None:
        kinds = {"fixed": {"fixed", "omit"}}.get(pref.choice, {pref.choice})
        matching = [c for c in candidates if c.kind in kinds]
        if len(matching) == 1:
            return matching[0]
    if group_size > 1:
        loops = [c for c in candidates if c.kind == "loop_item"]
        if len(loops) == 1:
            return loops[0]
    sources = [c for c in candidates if c.kind == "step_output"]
    if len(sources) == 1 and group_size == 1:
        return sources[0]
    return None


# ----------------------------------------------------------- trial compile
class TrialChoices(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    binding_choices: dict[str, str]
    for_each_choices: dict[str, bool] = {}
    column_ids: list[str]
    inputs: dict[str, Any] = {}
    name: Annotated[str, Field(min_length=1, max_length=NAME_MAX)]
    purpose: Annotated[str, Field(min_length=1, max_length=PURPOSE_MAX)]
    rules: Annotated[str, Field(min_length=1, max_length=RULES_MAX)]


def compile_trial(
    internal: Mapping[str, Any], choices: TrialChoices, max_items: int = MAX_LIST_ITEMS
) -> tuple[UserSkillDefinition, dict[str, Any]]:
    """Definition + inputs (by parameter name) from the user's card choices."""

    samples = set(internal["samples"])
    steps = internal["steps"]
    param_names: dict[str, str] = {}
    params: list[dict[str, Any]] = []
    arguments: list[dict[str, Any]] = [{} for _ in steps]
    loops: dict[int, tuple[int, Path]] = {}
    for binding in internal["bindings"]:
        chosen = choices.binding_choices.get(binding["binding_id"])
        candidate = next((c for c in binding["candidates"] if c["candidate_id"] == chosen), None)
        if candidate is None:
            raise ProposalRejected("binding_unconfirmed", f"请确认「{binding['label']}」怎么填写")
        index = binding["step"]
        kind = candidate["kind"]
        if kind == "param":
            name = f"p{len(params) + 1}"
            param_names[binding["binding_id"]] = name
            params.append({"name": name, "label": binding["label"],
                           "schema": candidate["schema"], "required": True})
            arguments[index][binding["arg"]] = {"source": "param", "name": name}
        elif kind == "fixed":
            arguments[index][binding["arg"]] = {"source": "fixed", "value": candidate["value"]}
        elif kind == "step_output":
            arguments[index][binding["arg"]] = {"source": "step_output",
                                                 "step_id": f"s{candidate['step'] + 1}",
                                                 "path": list(candidate["path"])}
        elif kind == "loop_item":
            loop_source = (candidate["step"], tuple(candidate["list_path"]))
            if loops.setdefault(index, loop_source) != loop_source:
                raise ProposalRejected("binding_conflict", "同一步只能对一个列表逐条查询")
            arguments[index][binding["arg"]] = {"source": "loop_item",
                                                 "path": list(candidate["path"])}
        # omit: the capability default applies; the argument is not sent.
    for index in range(len(steps)):
        offered = f"f{index}"
        if offered in choices.for_each_choices and (
            choices.for_each_choices[offered] != (index in loops)
        ):
            raise ProposalRejected("for_each_conflict", "「逐条查询」的选择与来源选择不一致")
    step_specs = []
    for index, step in enumerate(steps):
        spec: dict[str, Any] = {
            "step_id": f"s{index + 1}",
            "capability_id": step["capability_id"],
            "capability_fingerprint": step["fingerprint"],
            "label": step["label"],
            "arguments": arguments[index],
        }
        if index in loops:
            loop_step, list_path = loops[index]
            spec["for_each"] = {
                "items": {"source": "step_output", "step_id": f"s{loop_step + 1}",
                          "path": list(list_path)},
                "max_items": max_items,
            }
        step_specs.append(spec)
    selected = set(choices.column_ids)
    tables = []
    for table in internal["tables"]:
        columns = [
            {"label": column["label"], "ref": {"source": column["source"],
                                               "path": column["path"]}}
            for c_index, column in enumerate(table["columns"])
            if f"{table['table_id']}.c{c_index + 1}" in selected
        ]
        if not columns:
            continue
        step_index = table["step"]
        if step_index in loops:
            rows: dict[str, Any] = {"for_each_step": f"s{step_index + 1}"}
        else:
            rows = {"list": {"source": "step_output", "step_id": f"s{step_index + 1}",
                             "path": table.get("list_path") or []}}
        tables.append({"table_id": table["table_id"], "title": table["title"],
                       "rows": rows, "columns": columns})
    if not tables:
        raise ProposalRejected("no_columns", "请至少选择一列要显示的内容")
    try:
        definition = UserSkillDefinition.model_validate({
            "format": FORMAT, "params": params, "steps": step_specs,
            "output": {"tables": tables},
        })
    except ValidationError:
        raise ProposalRejected("definition_invalid", "这样的组合无法保存，请调整来源或列") from None
    check_texts([choices.name, choices.purpose, choices.rules, *text_slots(definition)], samples)
    inputs = {param_names[b]: v for b, v in choices.inputs.items() if b in param_names}
    return definition, inputs


__all__ = (
    "ProposalRejected",
    "TrialChoices",
    "build_proposal",
    "check_texts",
    "compile_trial",
    "model_proposal_schema",
    "summarize",
)
