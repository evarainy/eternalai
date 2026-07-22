"""Closed Admin Lite action set used by policy and trace records."""

from __future__ import annotations

from typing import Literal, TypeAlias

AdminRegistryAction: TypeAlias = Literal[
    "list",
    "get",
    "create",
    "enable",
    "disable",
]

ADMIN_POLICY_CAPABILITY_BY_ACTION: dict[AdminRegistryAction, str] = {
    "list": "admin_registry_list",
    "get": "admin_registry_get",
    "create": "admin_registry_create",
    "enable": "admin_registry_enable",
    "disable": "admin_registry_disable",
}

ADMIN_LITE_POLICY_CAPABILITY_IDS = frozenset(ADMIN_POLICY_CAPABILITY_BY_ACTION.values())
