"""The ``user_skill.v1`` definition: the only execution source of a saved flow.

A definition holds references and labels, never sample values: fixed values are
limited to what the leaf capability contract allows (see ``proposal``). It
compiles to a ``WorkflowDefinition`` of ``DefinitionStep`` steps for
``WorkflowEngine.execute_definition``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.workflow.models import (
    DefinitionAssertion,
    DefinitionForEach,
    DefinitionRef,
    DefinitionStep,
    WorkflowDefinition,
)

FORMAT = "user_skill.v1"
MAX_STEPS = 6
MAX_TABLES = 3
MAX_COLUMNS = 20
DEFAULT_MAX_ITEMS = 50
HARD_MAX_ITEMS = 200
NAME_MAX = 40
PURPOSE_MAX = 120
RULES_MAX = 300
LABEL_MAX = 30

PathSegment = str | int
Label = Annotated[str, Field(min_length=1, max_length=LABEL_MAX)]
StepId = Annotated[str, Field(pattern=r"^s[1-9][0-9]?$")]
ParamName = Annotated[str, Field(pattern=r"^p[1-9][0-9]?$")]
Fingerprint = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ParamSpec(_Strict):
    name: ParamName
    label: Label
    schema_: dict[str, Any] = Field(alias="schema")
    required: bool = True


class ParamArg(_Strict):
    source: Literal["param"]
    name: ParamName


class FixedArg(_Strict):
    source: Literal["fixed"]
    value: str | int | float | bool


class StepOutputRef(_Strict):
    source: Literal["step_output"]
    step_id: StepId
    path: tuple[PathSegment, ...] = ()


class LoopItemRef(_Strict):
    source: Literal["loop_item"]
    path: tuple[PathSegment, ...] = ()


Argument = Annotated[
    ParamArg | FixedArg | StepOutputRef | LoopItemRef, Field(discriminator="source")
]


class ForEachSpec(_Strict):
    items: StepOutputRef
    max_items: int = Field(default=DEFAULT_MAX_ITEMS, ge=1, le=HARD_MAX_ITEMS)


class AssertionSpec(_Strict):
    op: Literal["non_empty", "max_count", "complete"]
    ref: StepOutputRef
    value: int | None = Field(default=None, ge=1, le=HARD_MAX_ITEMS)


class StepSpec(_Strict):
    step_id: StepId
    capability_id: Annotated[str, Field(min_length=1, max_length=200)]
    capability_fingerprint: Fingerprint
    label: Label
    arguments: dict[str, Argument] = Field(default_factory=dict)
    for_each: ForEachSpec | None = None
    assertions: tuple[AssertionSpec, ...] = Field(default=(), alias="assert")


class RowRef(_Strict):
    source: Literal["row"]
    path: tuple[PathSegment, ...] = ()


class ItemResultRef(_Strict):
    source: Literal["item_result"]
    path: tuple[PathSegment, ...] = ()


ColumnRefSpec = Annotated[RowRef | LoopItemRef | ItemResultRef, Field(discriminator="source")]


class ColumnSpec(_Strict):
    label: Label
    ref: ColumnRefSpec


class ListRows(_Strict):
    list: StepOutputRef


class ForEachRows(_Strict):
    for_each_step: StepId


class TableSpec(_Strict):
    table_id: Annotated[str, Field(pattern=r"^t[1-9]$")]
    title: Label
    rows: ListRows | ForEachRows
    columns: tuple[ColumnSpec, ...] = Field(min_length=1, max_length=MAX_COLUMNS)


class OutputSpec(_Strict):
    tables: tuple[TableSpec, ...] = Field(min_length=1, max_length=MAX_TABLES)


class UserSkillDefinition(_Strict):
    format: Literal["user_skill.v1"]
    params: tuple[ParamSpec, ...] = ()
    steps: tuple[StepSpec, ...] = Field(min_length=1, max_length=MAX_STEPS)
    output: OutputSpec

    @model_validator(mode="after")
    def _references(self) -> UserSkillDefinition:
        params = {param.name for param in self.params}
        if len(params) != len(self.params):
            raise ValueError("duplicate parameter name")
        seen: dict[str, StepSpec] = {}
        for step in self.steps:
            if step.step_id in seen:
                raise ValueError("duplicate step id")
            if step.for_each is not None:
                source = seen.get(step.for_each.items.step_id)
                if source is None or source.for_each is not None:
                    raise ValueError("for_each must read a previous non-loop step")
            for argument in step.arguments.values():
                if isinstance(argument, ParamArg) and argument.name not in params:
                    raise ValueError("unknown parameter")
                if isinstance(argument, StepOutputRef):
                    source = seen.get(argument.step_id)
                    if source is None or source.for_each is not None:
                        raise ValueError("argument must read a previous non-loop step")
                if isinstance(argument, LoopItemRef) and step.for_each is None:
                    raise ValueError("loop_item outside for_each")
            for assertion in step.assertions:
                if assertion.ref.step_id != step.step_id or step.for_each is not None:
                    raise ValueError("assertions read their own non-loop step")
                if (assertion.op == "max_count") != (assertion.value is not None):
                    raise ValueError("max_count needs a value")
            seen[step.step_id] = step
        used = {
            argument.name
            for step in self.steps
            for argument in step.arguments.values()
            if isinstance(argument, ParamArg)
        }
        if used != params:
            raise ValueError("every parameter must be used")
        for table in self.output.tables:
            if isinstance(table.rows, ListRows):
                source = seen.get(table.rows.list.step_id)
                if source is None or source.for_each is not None:
                    raise ValueError("list table reads a non-loop step")
                allowed: tuple[type, ...] = (RowRef,)
            else:
                source = seen.get(table.rows.for_each_step)
                if source is None or source.for_each is None:
                    raise ValueError("for_each table reads a loop step")
                allowed = (LoopItemRef, ItemResultRef)
            if any(not isinstance(column.ref, allowed) for column in table.columns):
                raise ValueError("column reference does not match the table rows")
        if len({table.table_id for table in self.output.tables}) != len(self.output.tables):
            raise ValueError("duplicate table id")
        return self


def canonical_definition_json(definition: UserSkillDefinition) -> str:
    return json.dumps(
        definition.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def definition_digest(definition: UserSkillDefinition) -> bytes:
    return hashlib.sha256(canonical_definition_json(definition).encode("utf-8")).digest()


def params_schema(definition: UserSkillDefinition) -> dict[str, Any]:
    """The object JSON Schema for invocation inputs; labels become descriptions."""

    properties: dict[str, Any] = {}
    for param in definition.params:
        schema = dict(param.schema_)
        schema["description"] = param.label
        properties[param.name] = schema
    return {
        "type": "object",
        "properties": properties,
        "required": [param.name for param in definition.params if param.required],
        "additionalProperties": False,
    }


def _ref(argument: ParamArg | StepOutputRef | LoopItemRef) -> DefinitionRef:
    if isinstance(argument, ParamArg):
        return DefinitionRef("workflow_input", (argument.name,))
    if isinstance(argument, StepOutputRef):
        return DefinitionRef("step_output", tuple(argument.path), argument.step_id)
    return DefinitionRef("loop_item", tuple(argument.path))


def compile_definition(
    definition: UserSkillDefinition, *, workflow_id: str, version: str
) -> WorkflowDefinition:
    steps: list[DefinitionStep] = []
    for step in definition.steps:
        fixed = {
            name: argument.value
            for name, argument in step.arguments.items()
            if isinstance(argument, FixedArg)
        }
        refs = {
            name: _ref(argument)
            for name, argument in step.arguments.items()
            if not isinstance(argument, FixedArg)
        }
        steps.append(
            DefinitionStep(
                step_id=step.step_id,
                capability_id=step.capability_id,
                static_arguments=fixed,
                arguments=refs,
                for_each=(
                    None
                    if step.for_each is None
                    else DefinitionForEach(
                        items=_ref(step.for_each.items), max_items=step.for_each.max_items
                    )
                ),
                assertions=tuple(
                    DefinitionAssertion(assertion.op, _ref(assertion.ref), assertion.value)
                    for assertion in step.assertions
                ),
            )
        )
    return WorkflowDefinition(workflow_id=workflow_id, version=version, steps=tuple(steps))


def text_slots(definition: UserSkillDefinition) -> list[str]:
    """Every persisted free-text slot of the definition (labels and titles)."""

    slots = [param.label for param in definition.params]
    slots.extend(step.label for step in definition.steps)
    for table in definition.output.tables:
        slots.append(table.title)
        slots.extend(column.label for column in table.columns)
    return slots


__all__ = (
    "FORMAT",
    "NAME_MAX",
    "PURPOSE_MAX",
    "RULES_MAX",
    "UserSkillDefinition",
    "canonical_definition_json",
    "compile_definition",
    "definition_digest",
    "params_schema",
    "text_slots",
)
