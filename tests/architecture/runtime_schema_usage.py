"""AST data-flow inventory for Runtime-test CapabilitySpec output contracts."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

from app.infra.adapters.oa.contracts import (
    OAPendingWorkflowCollection,
    OASystemMessageCollection,
)
from app.infra.sdui.credential_markers import has_credential_marker
from tests.runtime.registry_fakes import VALID_RUNTIME_OUTPUT_SCHEMAS

_RUNTIME_OUTPUT_SCHEMA_TARGET = "tests.runtime.registry_fakes:runtime_output_schema"
_ACTIVE_CAPABILITY_TARGET = "tests.runtime.registry_fakes:active_capability"
_SUPPORTED_CAPABILITY_CONSTRUCTION_APIS = frozenset(
    {
        "model_construct",
        "model_validate",
    }
)
_SUPPORTED_CAPABILITY_COPY_APIS = frozenset({"model_copy"})
_SUPPORTED_CAPABILITY_APIS = (
    _SUPPORTED_CAPABILITY_CONSTRUCTION_APIS | _SUPPORTED_CAPABILITY_COPY_APIS
)
_UNRESOLVED = object()


@dataclass(frozen=True)
class RuntimeSchemaUsage:
    source: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class RuntimeSchemaInventory:
    usages: tuple[RuntimeSchemaUsage, ...]
    unresolved: tuple[str, ...]


@dataclass(frozen=True)
class _Value:
    value: Any
    origin: str


@dataclass(frozen=True)
class _Ref:
    module_name: str
    scope: str | None
    node: ast.AST
    bindings: dict[str, tuple[_Value | _Ref, ...]]


class _Module:
    def __init__(
        self,
        path: Path,
        module_name: str,
        *,
        source: str | None = None,
    ) -> None:
        self.path = path
        self.module_name = module_name
        if source is None:
            source = path.read_text(encoding="utf-8")
        self.tree = ast.parse(source, filename=str(path))
        self.parents: dict[int, ast.AST] = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[id(child)] = parent
        self.functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.top_level_functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            scope = self._scope_name(node)
            if scope is None:
                continue
            self.functions[scope] = node
            if "." not in scope:
                self.top_level_functions[node.name] = node
        self.imports: dict[str, tuple[str, str]] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = (node.module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local_name = alias.asname or alias.name.split(".", 1)[0]
                    self.imports[local_name] = (alias.name, "")

    def scope_of(self, node: ast.AST) -> str | None:
        current: ast.AST | None = node
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return self._scope_name(current)
            current = self.parents.get(id(current))
        return None

    def _scope_name(self, node: ast.AST) -> str | None:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
        names = [node.name]
        current = self.parents.get(id(node))
        while current is not None:
            if isinstance(current, ast.ClassDef):
                names.append(current.name)
            elif isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(current.name)
            current = self.parents.get(id(current))
        return ".".join(reversed(names))


class _RuntimeSchemaProgram:
    def __init__(
        self,
        runtime_test_root: Path | None = None,
        *,
        sources: Mapping[str, str] | None = None,
    ) -> None:
        if (runtime_test_root is None) == (sources is None):
            raise ValueError("provide exactly one Runtime schema source")
        self._source_fixture = sources is not None
        self._schema_related_names_cache: set[tuple[str, str | None, str]] | None = None
        self._runtime_module_names: set[str] | None = None
        self._repo_root = runtime_test_root.parents[1] if runtime_test_root is not None else None
        self.modules: dict[str, _Module] = {}
        if runtime_test_root is not None:
            for path in sorted(runtime_test_root.glob("*.py")):
                if path.name == "__init__.py":
                    continue
                module_name = f"tests.runtime.{path.stem}"
                self.modules[module_name] = _Module(path, module_name)
        else:
            assert sources is not None
            for filename, source in sorted(sources.items()):
                path = Path(filename)
                module_name = f"tests.runtime.{path.stem}"
                self.modules[module_name] = _Module(
                    path,
                    module_name,
                    source=source,
                )

    def collect(self) -> RuntimeSchemaInventory:
        usages: list[RuntimeSchemaUsage] = []
        unresolved: list[str] = []
        runtime_modules = self._runtime_modules()
        self._runtime_module_names = {module.module_name for module in runtime_modules}
        for module in runtime_modules:
            for node in ast.walk(module.tree):
                mutation_values = self._mutation_schema_values(module, node)
                if mutation_values:
                    self._record_values(
                        module,
                        node,
                        mutation_values,
                        usages,
                        unresolved,
                    )
                if not isinstance(node, ast.Call):
                    continue
                unsupported_api = self._unsupported_capability_api(module, node)
                if unsupported_api is not None:
                    unresolved.append(
                        f"{self._location(module, node)}:unsupported-capability-api:"
                        f"{unsupported_api}"
                    )
                expressions = self._schema_expressions(module, node)
                for expression in expressions:
                    values = self._eval_ref(
                        _Ref(
                            module_name=module.module_name,
                            scope=module.scope_of(node),
                            node=expression,
                            bindings={},
                        ),
                        stack=(),
                    )
                    self._record_values(
                        module,
                        expression,
                        values,
                        usages,
                        unresolved,
                    )
                for value in self._indirect_schema_values(module, node):
                    self._record_values(
                        module,
                        node,
                        [value],
                        usages,
                        unresolved,
                    )
        return RuntimeSchemaInventory(
            usages=tuple(self._dedupe_usages(usages)),
            unresolved=tuple(sorted(set(unresolved))),
        )

    def _runtime_modules(self) -> tuple[_Module, ...]:
        if self._source_fixture:
            return tuple(self.modules.values())

        runtime_reaching_modules = self._production_modules_reaching_runtime()
        runtime_reaching = {
            module.module_name
            for module in self.modules.values()
            if any(
                imported_module in runtime_reaching_modules
                for imported_module, _ in module.imports.values()
            )
        }
        changed = True
        while changed:
            changed = False
            for module in self.modules.values():
                called_modules = self._called_runtime_modules(module)
                if module.module_name not in runtime_reaching and called_modules & runtime_reaching:
                    runtime_reaching.add(module.module_name)
                    changed = True

        selected = set(runtime_reaching)
        changed = True
        while changed:
            changed = False
            for module_name in tuple(selected):
                missing = self._called_runtime_modules(self.modules[module_name]) - selected
                if missing:
                    selected.update(missing)
                    changed = True
        return tuple(module for module in self.modules.values() if module.module_name in selected)

    def _called_runtime_modules(self, module: _Module) -> set[str]:
        called: set[str] = set()
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Call):
                continue
            target_module, _ = self._local_function_target(self._call_target(module, node.func))
            if target_module is not None:
                called.add(target_module)
        return called

    def _analysis_modules(self) -> tuple[_Module, ...]:
        if self._runtime_module_names is None:
            return tuple(self.modules.values())
        return tuple(
            module
            for module in self.modules.values()
            if module.module_name in self._runtime_module_names
        )

    def _production_modules_reaching_runtime(self) -> set[str]:
        assert self._repo_root is not None
        imports_by_module: dict[str, set[str]] = {}
        for top_level in ("app", "scripts"):
            for path in sorted((self._repo_root / top_level).rglob("*.py")):
                module_name = self._module_name(path)
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                imports: set[str] = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module is not None:
                        imports.add(node.module)
                    elif isinstance(node, ast.Import):
                        imports.update(alias.name for alias in node.names)
                imports_by_module[module_name] = imports

        reaching = {"app.runtime.runtime"}
        changed = True
        while changed:
            changed = False
            for module_name, imports in imports_by_module.items():
                if module_name not in reaching and imports & reaching:
                    reaching.add(module_name)
                    changed = True
        return reaching

    def _module_name(self, path: Path) -> str:
        assert self._repo_root is not None
        relative = path.relative_to(self._repo_root)
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    def _mutation_schema_values(
        self,
        module: _Module,
        node: ast.AST,
    ) -> list[_Value]:
        assignments: list[tuple[ast.AST, ast.AST]] = []
        if isinstance(node, ast.Assign):
            assignments.extend((target, node.value) for target in node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            assignments.append((node.target, node.value))
        elif isinstance(node, ast.AugAssign):
            root_name = self._root_name(node.target)
            output_schema_path = self._output_schema_path(node.target)
            path = (
                output_schema_path
                if output_schema_path is not None
                else self._subscript_path(node.target)
            )
            schema_related = root_name is not None and self._is_schema_related(
                module, node, root_name
            )
            root_schema_target = output_schema_path == () or (
                isinstance(node.target, ast.Name) and schema_related
            )
            if root_schema_target:
                if not isinstance(node.op, ast.BitOr):
                    return [
                        self._unresolved_at(
                            module,
                            node,
                            "output-schema-root-augmented-assignment",
                        )
                    ]
                return self._root_mapping_mutation_values(module, node, node.value)
            if path and schema_related:
                if not isinstance(node.op, ast.Add):
                    return [
                        self._unresolved_at(
                            module,
                            node,
                            "output-schema-augmented-assignment",
                        )
                    ]
                return self._schema_mapping_mutation_values(module, node, path, node.value)

        values: list[_Value] = []
        for target, expression in assignments:
            if isinstance(target, ast.Attribute) and target.attr == "output_schema":
                values.extend(self._eval_expression(module, node, expression))
                continue
            path = self._output_schema_path(target)
            if path is not None:
                values.extend(
                    self._wrapped_mutation_values(
                        module,
                        node,
                        path,
                        expression,
                    )
                )
                continue
            generic_path = self._subscript_path(target)
            root_name = self._root_name(target)
            schema_related = root_name is not None and self._is_schema_related(
                module, node, root_name
            )
            property_path = (
                self._property_path(module, node, generic_path) if schema_related else None
            )
            if property_path is not None:
                values.extend(
                    self._wrapped_mutation_values(
                        module,
                        node,
                        property_path,
                        expression,
                    )
                )
                continue
            if generic_path and schema_related:
                values.extend(
                    self._schema_mapping_mutation_values(module, node, generic_path, expression)
                )

        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
        ):
            path = self._output_schema_path(node.func.value)
            if path is None:
                generic_path = self._subscript_path(node.func.value)
                root_name = self._root_name(node.func.value)
                schema_related = root_name is not None and self._is_schema_related(
                    module, node, root_name
                )
                path = self._property_path(module, node, generic_path) if schema_related else None
            if path is not None:
                if len(node.args) > 1 or any(keyword.arg is None for keyword in node.keywords):
                    return [self._unresolved_at(module, node, "output-schema-update")]
                mappings: list[ast.AST] = list(node.args)
                if node.keywords:
                    mappings.append(
                        ast.copy_location(
                            ast.Dict(
                                keys=[ast.Constant(keyword.arg) for keyword in node.keywords],
                                values=[keyword.value for keyword in node.keywords],
                            ),
                            node,
                        )
                    )
                for mapping in mappings:
                    values.extend(
                        self._wrapped_mutation_values(
                            module,
                            node,
                            path,
                            mapping,
                        )
                    )
            else:
                root_name = self._root_name(node.func.value)
                schema_related = root_name is not None and self._is_schema_related(
                    module, node, root_name
                )
                if not schema_related:
                    return values
                for mapping in list(node.args) + [
                    ast.copy_location(
                        ast.Dict(
                            keys=[ast.Constant(keyword.arg) for keyword in node.keywords],
                            values=[keyword.value for keyword in node.keywords],
                        ),
                        node,
                    )
                ]:
                    for candidate in self._eval_expression(module, node, mapping):
                        if candidate.value is _UNRESOLVED:
                            values.append(candidate)
                        elif isinstance(candidate.value, dict):
                            if candidate.value:
                                values.append(candidate)
                            output_schema = candidate.value.get("output_schema")
                            if isinstance(output_schema, dict):
                                values.append(_Value(output_schema, candidate.origin))
                            elif "output_schema" in candidate.value:
                                values.append(
                                    self._unresolved_at(
                                        module,
                                        node,
                                        "output-schema-value",
                                    )
                                )
                            if any(
                                isinstance(key, str) and has_credential_marker(key)
                                for key in candidate.value
                            ):
                                values.append(
                                    _Value(
                                        {"properties": candidate.value},
                                        candidate.origin,
                                    )
                                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"append", "extend", "insert"}
        ):
            root_name = self._root_name(node.func.value)
            schema_related = root_name is not None and self._is_schema_related(
                module, node, root_name
            )
            path = self._output_schema_path(node.func.value)
            if path is None:
                path = self._subscript_path(node.func.value)
            if schema_related or path:
                expected_args = 2 if node.func.attr == "insert" else 1
                if len(node.args) != expected_args or node.keywords:
                    values.append(
                        self._unresolved_at(module, node, "output-schema-sequence-mutation")
                    )
                else:
                    expression = node.args[-1]
                    if node.func.attr in {"append", "insert"}:
                        expression = ast.copy_location(
                            ast.List(elts=[expression], ctx=ast.Load()),
                            expression,
                        )
                    else:
                        expression = ast.copy_location(
                            ast.Call(
                                func=ast.Name(id="list", ctx=ast.Load()),
                                args=[expression],
                                keywords=[],
                            ),
                            expression,
                        )
                    if path:
                        values.extend(
                            self._wrapped_mutation_values(
                                module,
                                node,
                                path,
                                expression,
                            )
                        )
                    else:
                        values.extend(self._eval_expression(module, node, expression))
        return values

    def _schema_mapping_mutation_values(
        self,
        module: _Module,
        context: ast.AST,
        path: tuple[ast.AST, ...],
        expression: ast.AST,
    ) -> list[_Value]:
        first = self._eval_expression(module, context, path[0])
        if (
            len(first) == 1
            and first[0].value is not _UNRESOLVED
            and first[0].value == "output_schema"
        ):
            if len(path) == 1:
                return self._eval_expression(module, context, expression)
            return self._wrapped_mutation_values(
                module,
                context,
                path[1:],
                expression,
            )
        return self._wrapped_mutation_values(module, context, path, expression)

    def _root_mapping_mutation_values(
        self,
        module: _Module,
        context: ast.AST,
        expression: ast.AST,
    ) -> list[_Value]:
        values = self._eval_expression(module, context, expression)
        mutations: list[_Value] = []
        for value in values:
            mutations.append(value)
            if value.value is _UNRESOLVED or not isinstance(value.value, dict):
                continue
            output_schema = value.value.get("output_schema")
            if isinstance(output_schema, dict):
                mutations.append(_Value(output_schema, value.origin))
            elif "output_schema" in value.value:
                mutations.append(self._unresolved_at(module, context, "output-schema-value"))
        return mutations

    def _schema_related_names(self) -> set[tuple[str, str | None, str]]:
        if self._schema_related_names_cache is not None:
            return self._schema_related_names_cache

        name_key = tuple[str, str | None, str]
        related: set[name_key] = set()
        assignment_edges: list[set[name_key]] = []
        call_edges: list[tuple[set[name_key], name_key]] = []
        for module in self._analysis_modules():
            for node in ast.walk(module.tree):
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    target_names: set[name_key] = {
                        (module.module_name, module.scope_of(node), target.id)
                        for target in targets
                        if isinstance(target, ast.Name)
                    }
                    if isinstance(node.value, ast.Attribute) and node.value.attr == "output_schema":
                        related.update(target_names)
                    if node.value is None:
                        continue
                    value_names = {
                        self._lexical_name_key(module, node, value_name)
                        for value_name in self._schema_assignment_names(node.value)
                    }
                    if target_names and value_names:
                        assignment_edges.append(target_names | value_names)
                if not isinstance(node, ast.Call):
                    continue

                expressions = self._schema_expressions(module, node)
                if self._is_capability_api(module, node, "model_validate"):
                    payload = node.args[0] if node.args else self._keyword(node, "obj")
                    if payload is not None:
                        expressions.append(payload)
                for expression in expressions:
                    related.update(
                        self._lexical_name_key(module, node, item.id)
                        for item in ast.walk(expression)
                        if isinstance(item, ast.Name)
                    )
                for mapping in self._indirect_mapping_expressions(module, node):
                    related.update(
                        self._lexical_name_key(module, node, item.id)
                        for item in ast.walk(mapping)
                        if isinstance(item, ast.Name)
                    )

                target_module, target_scope = self._local_function_target(
                    self._call_target(module, node.func)
                )
                if target_module is None or target_scope is None:
                    continue
                function = self.modules[target_module].top_level_functions[target_scope]
                parameters = self._parameter_names(function)
                actuals: dict[str, ast.AST] = {
                    parameter: argument
                    for parameter, argument in zip(parameters, node.args, strict=False)
                }
                actuals.update(
                    {
                        keyword.arg: keyword.value
                        for keyword in node.keywords
                        if keyword.arg in parameters
                    }
                )
                for parameter, actual in actuals.items():
                    actual_names = {
                        self._lexical_name_key(module, node, item.id)
                        for item in ast.walk(actual)
                        if isinstance(item, ast.Name)
                    }
                    if actual_names:
                        call_edges.append(
                            (
                                actual_names,
                                (target_module, target_scope, parameter),
                            )
                        )

        changed = True
        while changed:
            changed = False
            for edge in assignment_edges:
                if edge & related and not edge <= related:
                    related.update(edge)
                    changed = True
            for actual_names, parameter in call_edges:
                if parameter in related:
                    missing = actual_names - related
                    if missing:
                        related.update(missing)
                        changed = True

        self._schema_related_names_cache = related
        return self._schema_related_names_cache

    def _is_schema_related(
        self,
        module: _Module,
        context: ast.AST,
        name: str,
    ) -> bool:
        return self._lexical_name_key(module, context, name) in self._schema_related_names()

    def _lexical_name_key(
        self,
        module: _Module,
        context: ast.AST,
        name: str,
    ) -> tuple[str, str | None, str]:
        scope = module.scope_of(context)
        for candidate_scope in self._scope_chain(scope):
            function = module.functions.get(candidate_scope)
            if function is not None and name in self._parameter_names(function):
                return module.module_name, candidate_scope, name
            if any(
                module.scope_of(candidate) == candidate_scope
                and isinstance(candidate, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == name
                    for target in (
                        candidate.targets
                        if isinstance(candidate, ast.Assign)
                        else [candidate.target]
                    )
                )
                for candidate in ast.walk(module.tree)
            ):
                return module.module_name, candidate_scope, name
        return module.module_name, None, name

    @staticmethod
    def _schema_assignment_names(value: ast.AST) -> set[str]:
        if isinstance(value, (ast.Name, ast.Subscript)) or (
            isinstance(value, ast.Attribute) and value.attr == "output_schema"
        ):
            return {item.id for item in ast.walk(value) if isinstance(item, ast.Name)}
        if isinstance(value, ast.Call) and any(
            keyword.arg == "output_schema" for keyword in value.keywords
        ):
            return {
                item.id
                for keyword in value.keywords
                if keyword.arg == "output_schema"
                for item in ast.walk(keyword.value)
                if isinstance(item, ast.Name)
            }
        if isinstance(value, ast.Call):
            names: set[str] = set()
            for argument in (*value.args, *(keyword.value for keyword in value.keywords)):
                if isinstance(argument, ast.Dict):
                    names.update(_RuntimeSchemaProgram._schema_assignment_names(argument))
            return names
        if not isinstance(value, ast.Dict):
            return set()
        structure_keys = {
            "output_schema",
            "properties",
            "$defs",
            "items",
            "additionalProperties",
            "anyOf",
        }
        names = set()
        for key, item_value in zip(value.keys, value.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and key.value in structure_keys
            ):
                names.update(item.id for item in ast.walk(item_value) if isinstance(item, ast.Name))
        return names

    @staticmethod
    def _root_name(node: ast.AST) -> str | None:
        current = node
        while isinstance(current, (ast.Subscript, ast.Attribute)):
            current = current.value
        return current.id if isinstance(current, ast.Name) else None

    def _wrapped_mutation_values(
        self,
        module: _Module,
        context: ast.AST,
        path_nodes: tuple[ast.AST, ...],
        expression: ast.AST,
    ) -> list[_Value]:
        path_values: list[str | int] = []
        for path_node in path_nodes:
            candidates = self._eval_expression(module, context, path_node)
            if (
                len(candidates) != 1
                or candidates[0].value is _UNRESOLVED
                or not isinstance(candidates[0].value, (str, int))
            ):
                return [self._unresolved_at(module, context, "output-schema-path")]
            path_values.append(candidates[0].value)

        values = self._eval_expression(module, context, expression)
        wrapped: list[_Value] = []
        for value in values:
            if value.value is _UNRESOLVED:
                wrapped.append(value)
                continue
            item = value.value
            for segment in reversed(path_values):
                if isinstance(segment, int):
                    if segment < 0:
                        return [
                            self._unresolved_at(
                                module,
                                context,
                                "output-schema-path",
                            )
                        ]
                    items: list[Any] = [None] * (segment + 1)
                    items[segment] = item
                    item = items
                else:
                    item = {segment: item}
            wrapped.append(_Value(item, value.origin))
        return wrapped

    def _eval_expression(
        self,
        module: _Module,
        context: ast.AST,
        expression: ast.AST,
    ) -> list[_Value]:
        return self._eval_ref(
            _Ref(
                module_name=module.module_name,
                scope=module.scope_of(context),
                node=expression,
                bindings={},
            ),
            stack=(),
        )

    @staticmethod
    def _output_schema_path(node: ast.AST) -> tuple[ast.AST, ...] | None:
        path: list[ast.AST] = []
        current = node
        while isinstance(current, ast.Subscript):
            path.append(current.slice)
            current = current.value
        if not isinstance(current, ast.Attribute) or current.attr != "output_schema":
            return None
        return tuple(reversed(path))

    @staticmethod
    def _subscript_path(node: ast.AST) -> tuple[ast.AST, ...]:
        path: list[ast.AST] = []
        current = node
        while isinstance(current, ast.Subscript):
            path.append(current.slice)
            current = current.value
        return tuple(reversed(path))

    def _property_path(
        self,
        module: _Module,
        context: ast.AST,
        path: tuple[ast.AST, ...],
    ) -> tuple[ast.AST, ...] | None:
        resolved: list[object] = []
        for index, segment in enumerate(path):
            values = self._eval_expression(module, context, segment)
            if len(values) != 1 or values[0].value is _UNRESOLVED:
                return None
            resolved.append(values[0].value)
            if values[0].value == "properties":
                return path[index:]
        if resolved and isinstance(resolved[-1], str) and has_credential_marker(resolved[-1]):
            return (ast.copy_location(ast.Constant("properties"), path[-1]), path[-1])
        return None

    def _record_values(
        self,
        module: _Module,
        expression: ast.AST,
        values: list[_Value],
        usages: list[RuntimeSchemaUsage],
        unresolved: list[str],
    ) -> None:
        if not values:
            unresolved.append(self._location(module, expression))
            return
        for value in values:
            if value.value is _UNRESOLVED:
                unresolved.append(value.origin)
            elif isinstance(value.value, dict) and value.value:
                usages.append(
                    RuntimeSchemaUsage(
                        source=value.origin,
                        schema=value.value,
                    )
                )
            elif value.value not in (None, {}):
                unresolved.append(value.origin)

    def _indirect_schema_values(
        self,
        module: _Module,
        call: ast.Call,
    ) -> list[_Value]:
        mappings_to_resolve = self._indirect_mapping_expressions(module, call)
        if not mappings_to_resolve:
            return []
        schemas: list[_Value] = []
        for mapping in mappings_to_resolve:
            mappings = self._eval_ref(
                _Ref(
                    module_name=module.module_name,
                    scope=module.scope_of(call),
                    node=mapping,
                    bindings={},
                ),
                stack=(),
            )
            for candidate in mappings:
                if candidate.value is _UNRESOLVED:
                    schemas.append(candidate)
                    continue
                if not isinstance(candidate.value, dict):
                    schemas.append(
                        _Value(
                            _UNRESOLVED,
                            f"{candidate.origin}:capability-update-mapping",
                        )
                    )
                    continue
                if "output_schema" not in candidate.value:
                    continue
                schema = candidate.value["output_schema"]
                if isinstance(schema, dict):
                    schemas.append(_Value(schema, self._location(module, call)))
                else:
                    schemas.append(
                        _Value(
                            _UNRESOLVED,
                            f"{candidate.origin}:output-schema-value",
                        )
                    )
        return schemas

    def _indirect_mapping_expressions(
        self,
        module: _Module,
        call: ast.Call,
    ) -> list[ast.AST]:
        mappings_to_resolve: list[ast.AST] = []
        capability_api = self._capability_api(module, call)
        if capability_api == "model_validate":
            mapping = call.args[0] if call.args else self._keyword(call, "obj")
            if mapping is not None and not isinstance(mapping, ast.Dict):
                mappings_to_resolve.append(mapping)
        elif capability_api == "model_construct":
            mappings_to_resolve.extend(
                keyword.value for keyword in call.keywords if keyword.arg is None
            )
        elif isinstance(call.func, ast.Attribute) and call.func.attr == "model_copy":
            mapping = self._keyword(call, "update")
            if mapping is not None and not isinstance(mapping, ast.Dict):
                mappings_to_resolve.append(mapping)
        target = self._call_target(module, call.func)
        if target == _ACTIVE_CAPABILITY_TARGET or target.endswith(":CapabilitySpec"):
            mappings_to_resolve.extend(
                keyword.value for keyword in call.keywords if keyword.arg is None
            )
        return mappings_to_resolve

    def _capability_api(self, module: _Module, call: ast.Call) -> str | None:
        if not isinstance(call.func, ast.Attribute):
            return None
        owner = call.func.value
        owner_target = self._call_target(module, owner)
        if owner_target == "CapabilitySpec" or owner_target.endswith(":CapabilitySpec"):
            return call.func.attr
        return None

    def _is_capability_api(self, module: _Module, call: ast.Call, api: str) -> bool:
        return self._capability_api(module, call) == api

    def _unsupported_capability_api(self, module: _Module, call: ast.Call) -> str | None:
        capability_api = self._capability_api(module, call)
        if capability_api is None or capability_api in _SUPPORTED_CAPABILITY_APIS:
            return None
        return capability_api

    def _schema_expressions(self, module: _Module, call: ast.Call) -> list[ast.AST]:
        target = self._call_target(module, call.func)
        if target == _ACTIVE_CAPABILITY_TARGET:
            expression = self._keyword(call, "output_schema")
            if expression is None:
                if any(keyword.arg is None for keyword in call.keywords):
                    return []
                return [
                    ast.copy_location(
                        ast.Call(
                            func=ast.Name(id="runtime_output_schema", ctx=ast.Load()),
                            args=[ast.Constant(value="registry_fakes.default")],
                            keywords=[],
                        ),
                        call,
                    )
                ]
            return [expression]
        if target.endswith(":CapabilitySpec") or target == "CapabilitySpec":
            expression = self._keyword(call, "output_schema")
            return [] if expression is None else [expression]
        capability_api = self._capability_api(module, call)
        if capability_api == "model_validate":
            payload = call.args[0] if call.args else self._keyword(call, "obj")
            if not isinstance(payload, ast.Dict):
                return []
            return [
                value
                for key, value in zip(payload.keys, payload.values, strict=True)
                if isinstance(key, ast.Constant) and key.value == "output_schema"
            ]
        if capability_api == "model_construct":
            expression = self._keyword(call, "output_schema")
            return [] if expression is None else [expression]
        if isinstance(call.func, ast.Attribute) and call.func.attr == "model_copy":
            update = self._keyword(call, "update")
            if isinstance(update, ast.Dict):
                return [
                    value
                    for key, value in zip(update.keys, update.values, strict=True)
                    if isinstance(key, ast.Constant) and key.value == "output_schema"
                ]
        return []

    def _eval_ref(self, ref: _Ref, stack: tuple[tuple[Any, ...], ...]) -> list[_Value]:
        node = ref.node
        key = (ref.module_name, ref.scope, id(node), tuple(sorted(ref.bindings)))
        if key in stack or len(stack) >= 40:
            return [self._unresolved(ref, "recursive-or-deep-expression")]
        next_stack = (*stack, key)
        module = self.modules[ref.module_name]
        origin = self._location(module, node)

        if isinstance(node, ast.Constant):
            return [_Value(node.value, origin)]
        if isinstance(node, ast.Name):
            return self._eval_name(ref, next_stack)
        if isinstance(node, ast.Dict):
            return self._eval_dict(ref, next_stack)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return self._eval_sequence(ref, next_stack)
        if isinstance(node, (ast.BoolOp, ast.IfExp)):
            branches = node.values if isinstance(node, ast.BoolOp) else [node.body, node.orelse]
            return self._eval_many(ref, branches, next_stack)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self._eval_add(ref, next_stack)
        if isinstance(node, ast.Subscript):
            return self._eval_subscript(ref, next_stack)
        if isinstance(node, ast.Call):
            return self._eval_call(ref, next_stack)
        return [self._unresolved(ref, node.__class__.__name__)]

    def _eval_name(self, ref: _Ref, stack: tuple[tuple[Any, ...], ...]) -> list[_Value]:
        node = ref.node
        assert isinstance(node, ast.Name)
        if node.id in ref.bindings:
            return self._eval_bound(ref.bindings[node.id], stack)
        module = self.modules[ref.module_name]
        for scope in self._scope_chain(ref.scope):
            assignments = self._assignments(module, scope, node.id, node.lineno)
            function = module.functions.get(scope)
            if function is not None and node.id in self._parameter_names(function):
                actuals = self._parameter_actuals(module, function, node.id)
                values = self._eval_many(
                    _Ref(ref.module_name, scope, node, ref.bindings),
                    list(assignments),
                    stack,
                )
                values.extend(self._eval_bound(actuals, stack))
                if values:
                    return values
            elif assignments:
                return self._eval_many(
                    _Ref(ref.module_name, scope, node, ref.bindings),
                    list(assignments),
                    stack,
                )
        module_assignments = self._assignments(module, None, node.id, node.lineno)
        if module_assignments:
            return self._eval_many(
                _Ref(ref.module_name, None, node, ref.bindings),
                list(module_assignments),
                stack,
            )
        return [self._unresolved(ref, f"name:{node.id}")]

    def _eval_dict(self, ref: _Ref, stack: tuple[tuple[Any, ...], ...]) -> list[_Value]:
        node = ref.node
        assert isinstance(node, ast.Dict)
        choices: list[list[tuple[Any, Any]]] = []
        for key_node, value_node in zip(node.keys, node.values, strict=True):
            if key_node is None:
                return [self._unresolved(ref, "dict-unpack")]
            key_values = self._eval_ref(
                _Ref(ref.module_name, ref.scope, key_node, ref.bindings),
                stack,
            )
            value_values = self._eval_ref(
                _Ref(ref.module_name, ref.scope, value_node, ref.bindings),
                stack,
            )
            if self._contains_unresolved(key_values) or self._contains_unresolved(value_values):
                return [self._unresolved(ref, "dict-entry")]
            pairs = [(key.value, value.value) for key in key_values for value in value_values]
            if not pairs:
                return [self._unresolved(ref, "dict-entry")]
            choices.append(pairs)
        return [
            _Value(dict(items), self._location(self.modules[ref.module_name], node))
            for items in product(*choices)
        ]

    def _eval_sequence(
        self,
        ref: _Ref,
        stack: tuple[tuple[Any, ...], ...],
    ) -> list[_Value]:
        node = ref.node
        assert isinstance(node, (ast.List, ast.Tuple, ast.Set))
        choices = [
            self._eval_ref(
                _Ref(ref.module_name, ref.scope, item, ref.bindings),
                stack,
            )
            for item in node.elts
        ]
        unresolved = [value for values in choices for value in values if value.value is _UNRESOLVED]
        if unresolved:
            return unresolved
        if any(not values for values in choices):
            return [self._unresolved(ref, "sequence-entry")]
        constructor = tuple if isinstance(node, ast.Tuple) else list
        return [
            _Value(
                constructor(value.value for value in values),
                self._location(self.modules[ref.module_name], node),
            )
            for values in product(*choices)
        ]

    def _eval_add(self, ref: _Ref, stack: tuple[tuple[Any, ...], ...]) -> list[_Value]:
        node = ref.node
        assert isinstance(node, ast.BinOp)
        left = self._eval_ref(_Ref(ref.module_name, ref.scope, node.left, ref.bindings), stack)
        right = self._eval_ref(_Ref(ref.module_name, ref.scope, node.right, ref.bindings), stack)
        if self._contains_unresolved(left) or self._contains_unresolved(right):
            return [self._unresolved(ref, "addition")]
        values: list[_Value] = []
        for lhs in left:
            for rhs in right:
                try:
                    values.append(_Value(lhs.value + rhs.value, lhs.origin))
                except (TypeError, ValueError):
                    continue
        return values or [self._unresolved(ref, "addition")]

    def _eval_subscript(
        self,
        ref: _Ref,
        stack: tuple[tuple[Any, ...], ...],
    ) -> list[_Value]:
        node = ref.node
        assert isinstance(node, ast.Subscript)
        containers = self._eval_ref(
            _Ref(ref.module_name, ref.scope, node.value, ref.bindings), stack
        )
        indexes = self._eval_ref(_Ref(ref.module_name, ref.scope, node.slice, ref.bindings), stack)
        if self._contains_unresolved(containers) or self._contains_unresolved(indexes):
            return [self._unresolved(ref, "subscript")]
        values: list[_Value] = []
        for container in containers:
            for index in indexes:
                try:
                    values.append(_Value(container.value[index.value], container.origin))
                except (KeyError, IndexError, TypeError):
                    continue
        return values or [self._unresolved(ref, "subscript")]

    def _eval_call(self, ref: _Ref, stack: tuple[tuple[Any, ...], ...]) -> list[_Value]:
        node = ref.node
        assert isinstance(node, ast.Call)
        module = self.modules[ref.module_name]
        target = self._call_target(module, node.func)
        if target == _RUNTIME_OUTPUT_SCHEMA_TARGET or target == "runtime_output_schema":
            if not node.args:
                return [self._unresolved(ref, "runtime-output-schema-name")]
            names = self._eval_ref(
                _Ref(ref.module_name, ref.scope, node.args[0], ref.bindings), stack
            )
            if self._contains_unresolved(names):
                return [self._unresolved(ref, "runtime-output-schema-name")]
            values: list[_Value] = []
            for name in names:
                schema = VALID_RUNTIME_OUTPUT_SCHEMAS.get(name.value)
                if schema is None:
                    return [self._unresolved(ref, "runtime-output-schema-name")]
                values.append(_Value(schema, self._location(module, node)))
            return values or [self._unresolved(ref, "runtime-output-schema-name")]
        if isinstance(node.func, ast.Attribute) and node.func.attr == "model_json_schema":
            if isinstance(node.func.value, ast.Name):
                model_name = node.func.value.id
                models = {
                    "OAPendingWorkflowCollection": OAPendingWorkflowCollection,
                    "OASystemMessageCollection": OASystemMessageCollection,
                }
                if model_name in models:
                    return [
                        _Value(
                            models[model_name].model_json_schema(),
                            self._location(module, node),
                        )
                    ]
        if target in {"deepcopy", "copy:deepcopy"} and node.args:
            return self._eval_ref(
                _Ref(ref.module_name, ref.scope, node.args[0], ref.bindings), stack
            )
        if target == "dict" and node.args:
            return self._eval_ref(
                _Ref(ref.module_name, ref.scope, node.args[0], ref.bindings), stack
            )
        if target == "list" and node.args:
            candidates = self._eval_ref(
                _Ref(ref.module_name, ref.scope, node.args[0], ref.bindings), stack
            )
            values: list[_Value] = []
            for candidate in candidates:
                if candidate.value is _UNRESOLVED:
                    values.append(candidate)
                elif isinstance(candidate.value, (list, tuple, set)):
                    values.append(_Value(list(candidate.value), candidate.origin))
                else:
                    values.append(self._unresolved(ref, "list-conversion"))
            return values
        target_module, target_scope = self._local_function_target(target)
        if target_module is not None and target_scope is not None:
            function = self.modules[target_module].top_level_functions[target_scope]
            bindings = self._bind_call(ref, function)
            returns = [
                item.value
                for item in ast.walk(function)
                if isinstance(item, ast.Return)
                and item.value is not None
                and self.modules[target_module].scope_of(item) == target_scope
            ]
            return self._eval_many(
                _Ref(target_module, target_scope, node, bindings),
                returns,
                stack,
            )
        return [self._unresolved(ref, f"call:{target}")]

    def _eval_many(
        self,
        ref: _Ref,
        nodes: list[ast.AST],
        stack: tuple[tuple[Any, ...], ...],
    ) -> list[_Value]:
        values: list[_Value] = []
        for node in nodes:
            values.extend(
                self._eval_ref(_Ref(ref.module_name, ref.scope, node, ref.bindings), stack)
            )
        return values

    def _eval_bound(
        self,
        bound: tuple[_Value | _Ref, ...],
        stack: tuple[tuple[Any, ...], ...],
    ) -> list[_Value]:
        values: list[_Value] = []
        for item in bound:
            if isinstance(item, _Value):
                values.append(item)
            else:
                values.extend(self._eval_ref(item, stack))
        return values

    def _parameter_actuals(
        self,
        defining_module: _Module,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        parameter: str,
    ) -> tuple[_Value | _Ref, ...]:
        target = f"{defining_module.module_name}:{function.name}"
        actuals: list[_Value | _Ref] = []
        for module in self._analysis_modules():
            for call in (node for node in ast.walk(module.tree) if isinstance(node, ast.Call)):
                if self._call_target(module, call.func) != target:
                    continue
                bindings = self._bind_call(
                    _Ref(module.module_name, module.scope_of(call), call, {}),
                    function,
                )
                actuals.extend(bindings.get(parameter, ()))
        actuals.extend(self._parameterized_values(defining_module, function, parameter))
        if not actuals:
            default = self._parameter_default(function, parameter)
            if default is not None:
                actuals.append(_Ref(defining_module.module_name, function.name, default, {}))
        return tuple(actuals)

    def _parameterized_values(
        self,
        module: _Module,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        parameter: str,
    ) -> tuple[_Value, ...]:
        values: list[_Value] = []
        for decorator in function.decorator_list:
            if not isinstance(decorator, ast.Call) or len(decorator.args) < 2:
                continue
            if (
                not isinstance(decorator.func, ast.Attribute)
                or decorator.func.attr != "parametrize"
            ):
                continue
            names = self._literal_parameter_names(decorator.args[0])
            if parameter not in names:
                continue
            candidates = self._eval_ref(
                _Ref(module.module_name, None, decorator.args[1], {}), stack=()
            )
            index = names.index(parameter)
            for candidate in candidates:
                if candidate.value is _UNRESOLVED:
                    values.append(candidate)
                    continue
                if not isinstance(candidate.value, (list, tuple)):
                    values.append(_Value(_UNRESOLVED, candidate.origin))
                    continue
                for case in candidate.value:
                    if len(names) == 1:
                        values.append(_Value(case, candidate.origin))
                    elif isinstance(case, (list, tuple)) and len(case) == len(names):
                        values.append(_Value(case[index], candidate.origin))
                    else:
                        values.append(_Value(_UNRESOLVED, candidate.origin))
        return tuple(values)

    def _bind_call(
        self,
        caller: _Ref,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> dict[str, tuple[_Value | _Ref, ...]]:
        call = caller.node
        assert isinstance(call, ast.Call)
        parameters = self._parameter_names(function)
        bound: dict[str, tuple[_Value | _Ref, ...]] = {}
        for name, argument in zip(parameters, call.args, strict=False):
            bound[name] = (_Ref(caller.module_name, caller.scope, argument, caller.bindings),)
        for keyword in call.keywords:
            if keyword.arg in parameters:
                bound[keyword.arg] = (
                    _Ref(caller.module_name, caller.scope, keyword.value, caller.bindings),
                )
        for parameter in parameters:
            if parameter in bound:
                continue
            default = self._parameter_default(function, parameter)
            if default is not None:
                bound[parameter] = (
                    _Ref(
                        self._function_module(function).module_name,
                        self._function_module(function).scope_of(function),
                        default,
                        {},
                    ),
                )
        return bound

    def _call_target(self, module: _Module, function: ast.AST) -> str:
        if isinstance(function, ast.Name):
            if function.id in module.top_level_functions:
                return f"{module.module_name}:{function.id}"
            imported = module.imports.get(function.id)
            if imported is not None:
                return f"{imported[0]}:{imported[1]}"
            return function.id
        if isinstance(function, ast.Attribute):
            if isinstance(function.value, ast.Name):
                imported = module.imports.get(function.value.id)
                if imported is not None:
                    imported_module = imported[0]
                    if imported[1]:
                        imported_module = f"{imported_module}.{imported[1]}"
                    return f"{imported_module}:{function.attr}"
            return function.attr
        return ast.dump(function, include_attributes=False)

    def _local_function_target(self, target: str) -> tuple[str | None, str | None]:
        if ":" not in target:
            return None, None
        module_name, function_name = target.rsplit(":", 1)
        module = self.modules.get(module_name)
        if module is None or function_name not in module.top_level_functions:
            return None, None
        return module_name, function_name

    @staticmethod
    def _scope_chain(scope: str | None) -> tuple[str, ...]:
        if scope is None:
            return ()
        parts = scope.split(".")
        return tuple(".".join(parts[:index]) for index in range(len(parts), 0, -1))

    def _assignments(
        self,
        module: _Module,
        scope: str | None,
        name: str,
        before_line: int,
    ) -> tuple[ast.AST, ...]:
        candidates: list[tuple[int, ast.AST]] = []
        for node in ast.walk(module.tree):
            if (
                module.scope_of(node) != scope
                or getattr(node, "lineno", before_line) >= before_line
            ):
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                    candidates.append((node.lineno, node.value))
        return tuple(value for _, value in sorted(candidates, key=lambda item: item[0]))

    def _function_module(
        self,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> _Module:
        for module in self.modules.values():
            if function in module.functions.values():
                return module
        raise AssertionError("function is outside the Runtime schema program")

    @staticmethod
    def _parameter_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        return [
            argument.arg
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
        ]

    @staticmethod
    def _parameter_default(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        parameter: str,
    ) -> ast.AST | None:
        positional = [*function.args.posonlyargs, *function.args.args]
        positional_defaults = [None] * (len(positional) - len(function.args.defaults)) + list(
            function.args.defaults
        )
        defaults = {
            argument.arg: default
            for argument, default in zip(positional, positional_defaults, strict=True)
        }
        defaults.update(
            {
                argument.arg: default
                for argument, default in zip(
                    function.args.kwonlyargs,
                    function.args.kw_defaults,
                    strict=True,
                )
            }
        )
        return defaults.get(parameter)

    @staticmethod
    def _keyword(call: ast.Call, name: str) -> ast.AST | None:
        return next(
            (keyword.value for keyword in call.keywords if keyword.arg == name),
            None,
        )

    @staticmethod
    def _literal_parameter_names(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [item.strip() for item in node.value.split(",")]
        if isinstance(node, (ast.List, ast.Tuple)):
            return [
                item.value
                for item in node.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
        return []

    @staticmethod
    def _dedupe_usages(usages: list[RuntimeSchemaUsage]) -> list[RuntimeSchemaUsage]:
        seen: set[tuple[str, str]] = set()
        result: list[RuntimeSchemaUsage] = []
        for usage in usages:
            key = (usage.source, repr(usage.schema))
            if key not in seen:
                seen.add(key)
                result.append(usage)
        return result

    @staticmethod
    def _contains_unresolved(values: list[_Value]) -> bool:
        return any(value.value is _UNRESOLVED for value in values)

    @staticmethod
    def _location(module: _Module, node: ast.AST) -> str:
        return f"{module.path.name}:{getattr(node, 'lineno', 0)}"

    def _unresolved(self, ref: _Ref, reason: str) -> _Value:
        module = self.modules[ref.module_name]
        return self._unresolved_at(module, ref.node, reason)

    def _unresolved_at(self, module: _Module, node: ast.AST, reason: str) -> _Value:
        return _Value(_UNRESOLVED, f"{self._location(module, node)}:{reason}")


def collect_runtime_schema_inventory(runtime_test_root: Path) -> RuntimeSchemaInventory:
    return _RuntimeSchemaProgram(runtime_test_root).collect()


def collect_runtime_schema_inventory_from_sources(
    sources: Mapping[str, str],
) -> RuntimeSchemaInventory:
    return _RuntimeSchemaProgram(sources=sources).collect()


__all__ = (
    "RuntimeSchemaInventory",
    "RuntimeSchemaUsage",
    "collect_runtime_schema_inventory",
    "collect_runtime_schema_inventory_from_sources",
)
