"""Shared immutable binding comparisons for HumanGate adapters."""

from __future__ import annotations

from app.ports.human_gate import (
    TaskVersionBindingManifest,
    VersionBinding,
    VersionBindingMismatchError,
    canonical_version_bindings,
)


def assert_bindings(
    manifest: TaskVersionBindingManifest,
    bindings: tuple[VersionBinding, ...],
    *,
    exact: bool,
) -> None:
    try:
        actual = canonical_version_bindings(bindings)
    except ValueError as exc:
        raise VersionBindingMismatchError("Resource binding tuple is invalid") from exc
    expected_by_key = {
        (binding.resource_type, binding.resource_id): binding
        for binding in manifest.bindings
    }
    actual_by_key = {
        (binding.resource_type, binding.resource_id): binding for binding in actual
    }
    if exact and actual_by_key.keys() != expected_by_key.keys():
        raise VersionBindingMismatchError("Task version binding tuple changed")
    if any(expected_by_key.get(key) != value for key, value in actual_by_key.items()):
        raise VersionBindingMismatchError("Task version binding tuple changed")


__all__ = ("assert_bindings",)
