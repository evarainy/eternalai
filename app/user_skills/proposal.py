"""Save proposal: value-free summary → model choices → program checks → card.

The program, not the model, computes every argument's candidate sources from
this session's record (equal values are clues only; the user confirms each
non-typed binding on the card). Fixed values and parameter schemas come only
from the leaf capability contract, and no persisted text may contain a value
seen in the record.
"""

from __future__ import annotations

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
COMPLETENESS_FIELD = "is_complete"


class ProposalRejected(ValueError):
    def __init__(self, code: str, label: str, reason: str = "none") -> None:
        super().__init__(code)
        self.code = code
        self.label = label
        self.reason = reason  # closed set, for logs only


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


def _contract_text(spec: CapabilitySpec) -> str:
    """Normalized names, titles and descriptions of a capability contract."""

    parts = [spec.name, spec.short_description or ""]

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            parts.extend(str(node[key]) for key in ("title", "description")
                         if isinstance(node.get(key), str))
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(spec.input_schema)
    walk(spec.output_schema)
    return "|".join(_norm(part) for part in parts)


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
    for text in texts:
        normalized = _norm(text)
        if any(sample in normalized for sample in samples):
            raise ProposalRejected(
                "sample_value_in_text", "名称、说明或标题里出现了本次查询的具体内容，请改成一般说法"
            )


# ------------------------------------------------------------ output schema
def _deref(root: Mapping[str, Any], node: Any) -> Any:
    seen = 0
    while isinstance(node, Mapping) and isinstance(node.get("$ref"), str) and seen < 10:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            return None
        target: Any = root
        for part in ref[2:].split("/"):
            target = target.get(part) if isinstance(target, Mapping) else None
        node, seen = target, seen + 1
    return node


def _branches(root: Mapping[str, Any], node: Any) -> list[Mapping[str, Any]]:
    node = _deref(root, node)
    if not isinstance(node, Mapping):
        return []
    variants = node.get("anyOf") or node.get("oneOf")
    if isinstance(variants, list):
        return [b for v in variants for b in _branches(root, v)]
    return [node]


def schema_at(root: Mapping[str, Any], path: Sequence[str | int]) -> list[Mapping[str, Any]]:
    """Schema nodes ``path`` can reach in a leaf's exposed output (empty: not exposed)."""

    nodes = _branches(root, root)
    for segment in path:
        following: list[Mapping[str, Any]] = []
        for node in nodes:
            if isinstance(segment, int):
                if node.get("type") == "array":
                    following += _branches(root, node.get("items"))
            else:
                properties = node.get("properties")
                if isinstance(properties, Mapping) and segment in properties:
                    following += _branches(root, properties[segment])
        nodes = following
    return nodes


def _has_type(nodes: Sequence[Mapping[str, Any]], kind: str) -> bool:
    return any(node.get("type") == kind for node in nodes)


def _list_items(root: Mapping[str, Any], path: Sequence[str | int]) -> Mapping[str, Any] | None:
    """A root for one element of the list at ``path`` (keeps ``$defs`` for references)."""

    for node in schema_at(root, path):
        if node.get("type") == "array":
            for item in _branches(root, node.get("items")):
                return {**item, "$defs": root.get("$defs", {})}
    return None


def _exposed(root: Mapping[str, Any], path: Sequence[str | int]) -> bool:
    return bool(schema_at(root, path))


_SCALAR_TYPES = {"string", "integer", "number", "boolean"}


def _node_kinds(node: Mapping[str, Any]) -> set[str]:
    """Value kinds a schema node allows: its ``type`` (string or list) or its literal values."""

    declared = node.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list):
        return {str(item) for item in declared}
    literals = node["enum"] if isinstance(node.get("enum"), list) else (
        [node["const"]] if "const" in node else None
    )
    if literals is None:
        return {"unknown"}
    return {
        "null" if value is None else "boolean" if isinstance(value, bool)
        else "number" if isinstance(value, (int, float))
        else "string" if isinstance(value, str) else "object"
        for value in literals
    }


def _displayable(root: Mapping[str, Any], path: Sequence[str | int]) -> bool:
    """A column must reach a scalar (what a cell can show), never an object or a list."""

    kinds = {kind for node in schema_at(root, path) for kind in _node_kinds(node)}
    return bool(path) and bool(kinds & _SCALAR_TYPES) and kinds <= _SCALAR_TYPES | {"null"}


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
    except ValidationError as error:
        where = "；".join(
            f"{'.'.join(map(str, item['loc']))}（{item['type']}）" for item in error.errors()[:4]
        )
        raise ProposalRejected(
            "proposal_invalid", f"提案格式不对：{where}", "schema"
        ) from None
    by_seq = {record.seq: record for record in records}
    used: set[int] = set()
    steps: list[_Step] = []
    for item in model.steps:
        found = [by_seq.get(seq) for seq in item.record_seqs]
        if (
            any(entry is None for entry in found)
            or used & set(item.record_seqs)
            or len(set(item.record_seqs)) != len(item.record_seqs)
        ):
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
    contract_text = "".join(_contract_text(spec) for spec in specs.values())
    # A value that is also the contract's own wording (e.g. a source named like the
    # query) is a general term, not something this run found.
    samples = {s for s in sample_values(records, exempt) if s not in contract_text}
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
                source_schema = specs[steps[earlier].capability_id].output_schema
                if len(group) == 1:
                    for path, scalar in scalar_paths(source):
                        if scalar == values[0] and _exposed(source_schema, path):
                            source_label = (
                                f"来自「{steps[earlier].label}」结果的"
                                f"「{field_label(earlier, path)}」"
                            )
                            candidates.append(Candidate(
                                f"{binding_id}.s{earlier}.{len(candidates)}", "step_output",
                                source_label, step=earlier, path=path,
                            ))
                for list_path, elements in list_paths(source):
                    item_schema = _list_items(source_schema, list_path)
                    if item_schema is None:
                        continue
                    fields = {p for element in elements for p, _ in scalar_paths(element)}
                    for field_path in sorted(fields, key=str):
                        pool = [get_path(element, field_path) for element in elements]
                        if _exposed(item_schema, field_path) and _distinct_items(values, pool):
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
                "values": values,
                "candidates": [c.__dict__ for c in candidates],
            })
        if step_loop_candidates:
            offers.append({"offer_id": f"f{index}", "step_index": index, "step_label": step.label,
                           "list_label": "前一步列表里的每一条", "prefilled": len(step.seqs) > 1})
    columns_view: list[dict[str, Any]] = []
    tables_internal: list[dict[str, Any]] = []
    for t_index, table in enumerate(model.tables):
        if table.step >= len(steps):
            raise ProposalRejected(
                "proposal_invalid",
                f"表格「{table.title}」的 step={table.step} 不存在：step 是 steps 数组的下标，"
                f"从 0 开始，只能是 0 到 {len(steps) - 1}，不是记录的 seq",
                "table_step",
            )
        _check_table(table, specs[steps[table.step].capability_id],
                     by_seq[steps[table.step].seqs[0]].data)
        table_id = f"t{t_index + 1}"
        tables_internal.append({"table_id": table_id, **table.model_dump(mode="json")})
        for c_index, column in enumerate(table.columns):
            columns_view.append({"column_id": f"{table_id}.c{c_index + 1}",
                                 "table_id": table_id, "label": column.label})
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
        "steps": [{**s.__dict__, "output_schema": specs[s.capability_id].output_schema,
                   "data": by_seq[s.seqs[0]].data if len(s.seqs) == 1 else None}
                  for s in steps],
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
    """Preselect only an unambiguous choice; several possible sources are left to the user."""

    if pref is not None and pref.choice == "param":
        return next((c for c in candidates if c.kind == "param"), None)
    sources = [c for c in candidates if c.kind != "param"]
    if len(sources) != 1:
        return None
    only = sources[0]
    if only.kind == "loop_item" and group_size < 2:
        return None
    return only


def _assign_distinct(options: Sequence[Sequence[int]]) -> bool:
    """Each recorded call can be given its own list element (bipartite matching)."""

    owner: dict[int, int] = {}

    def place(call: int, seen: set[int]) -> bool:
        for element in options[call]:
            if element in seen:
                continue
            seen.add(element)
            if element not in owner or place(owner[element], seen):
                owner[element] = call
                return True
        return False

    return all(place(call, set()) for call in range(len(options)))


def _distinct_items(values: Sequence[Any], pool: Sequence[Any]) -> bool:
    """Each recorded value matches a different list element (a loop, not one item reused)."""

    return _assign_distinct([[i for i, v in enumerate(pool) if v == value] for value in values])


def _check_table(table: ModelTable, spec: CapabilitySpec, sample: Any) -> None:
    """Every column path must be exposed by the leaf; list tables need a real list."""

    root = spec.output_schema
    sources = {column.source for column in table.columns}

    def invalid(reason: str) -> ProposalRejected:
        return ProposalRejected(
            "table_path_invalid",
            f"表格「{table.title}」引用了这一步查询结果里没有的内容。"
            f"这一步可用的列表与列：{_list_fields_hint(root)}。"
            "列表表格的 list_path 填列表路径，columns 的 source 都填 row，"
            "path 填列名（相对列表里的一项，例如 [\"title\"]）；"
            "如果这张表格对应的是另一步，检查 step 是不是填成了 seq",
            reason,
        )

    if "row" in sources:
        if sources != {"row"}:
            raise invalid("mixed_sources")
        if not table.list_path:
            raise invalid("no_list_path")
        items = _list_items(root, table.list_path)
        if items is None or not isinstance(get_path(sample, table.list_path), list):
            raise invalid("list_path")
        if not all(_displayable(items, column.path) for column in table.columns):
            raise invalid("row_column")
    elif any(
        column.source == "item_result" and not _displayable(root, column.path)
        for column in table.columns
    ):
        raise invalid("item_result_column")


def _list_fields_hint(root: Mapping[str, Any]) -> str:
    """Field names (never values) of the top-level lists a leaf exposes."""

    hints = []
    for node in _branches(root, root):
        properties = node.get("properties")
        if not isinstance(properties, Mapping):
            continue
        for name in properties:
            items = _list_items(root, [name])
            fields = items.get("properties") if items is not None else None
            if isinstance(fields, Mapping):
                hints.append(f"list_path [\"{name}\"]，列 {'、'.join(map(str, fields))}")
    return "；".join(hints) or "无"


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
    loop_fields: dict[int, list[tuple[Path, list[Any]]]] = {}
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
            loop_fields.setdefault(index, []).append(
                (tuple(candidate["path"]), list(binding["values"]))
            )
        # omit: the capability default applies; the argument is not sent.
    # All per-item arguments of one call must come from the same list element, and the
    # recorded calls from different elements: otherwise the loop would run other queries.
    for index, fields in loop_fields.items():
        loop_step, list_path = loops[index]
        elements = get_path(steps[loop_step]["data"], list_path)
        calls = len(fields[0][1])
        if not isinstance(elements, list) or not _assign_distinct([
            [
                position for position, element in enumerate(elements)
                if all(get_path(element, path) == values[call] for path, values in fields)
            ]
            for call in range(calls)
        ]):
            raise ProposalRejected(
                "loop_mismatch", "逐条查询的几个填写项不是来自列表里的同一条，请重新选择来源"
            )
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
    completeness: dict[int, dict[str, Any]] = {}
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
        per_item = any(c["source"] != "row" for c in table["columns"])
        if per_item != (step_index in loops):
            raise ProposalRejected(
                "table_loop_mismatch", f"表格「{table['title']}」需要与「逐条查询」的选择一致"
            )
        if step_index in loops:
            loop_step, list_path = loops[step_index]
            items = _list_items(steps[loop_step]["output_schema"], list_path)
            if items is None or not all(
                _displayable(items, c["ref"]["path"]) for c in columns
                if c["ref"]["source"] == "loop_item"
            ):
                raise ProposalRejected(
                    "table_path_invalid", f"表格「{table['title']}」引用了查询结果里没有的内容"
                )
            rows: dict[str, Any] = {"for_each_step": f"s{step_index + 1}"}
        else:
            rows = {"list": {"source": "step_output", "step_id": f"s{step_index + 1}",
                             "path": table["list_path"]}}
            if _has_type(
                schema_at(steps[step_index]["output_schema"], [COMPLETENESS_FIELD]), "boolean"
            ):
                completeness[step_index] = {
                    "op": "complete",
                    "ref": {"source": "step_output", "step_id": f"s{step_index + 1}",
                            "path": [COMPLETENESS_FIELD]},
                }
        tables.append({"table_id": table["table_id"], "title": table["title"],
                       "rows": rows, "columns": columns})
    if not tables:
        raise ProposalRejected("no_columns", "请至少选择一列要显示的内容")
    # Leaves that report their own completeness get the ``complete`` check automatically.
    for index, assertion in completeness.items():
        step_specs[index]["assert"] = [assertion]
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
