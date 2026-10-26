"""Closed Admin Lite action set used by policy and trace records."""

from __future__ import annotations

from typing import Literal, TypeAlias

AdminAction: TypeAlias = Literal[
    "list",
    "get",
    "create",
    "enable",
    "disable",
    "tasks_list",
    "task_events_list",
    "bindings_list",
    "bindings_revoke",
    "bindings_reset",
    "traces_list",
    "leader_roster_list",
    "leader_roster_candidates",
    "leader_roster_add",
    "leader_roster_revoke",
]

AUDIT_READER_ROLE = "audit_reader"

ADMIN_POLICY_CAPABILITY_BY_ACTION: dict[AdminAction, str] = {
    "list": "admin_registry_list",
    "get": "admin_registry_get",
    "create": "admin_registry_create",
    "enable": "admin_registry_enable",
    "disable": "admin_registry_disable",
    "tasks_list": "admin_tasks_list",
    "task_events_list": "admin_task_events_list",
    "bindings_list": "admin_bindings_list",
    "bindings_revoke": "admin_bindings_revoke",
    "bindings_reset": "admin_bindings_reset",
    "traces_list": "admin_traces_list",
    # Leader roster actions are admin-only; audit_reader does not unlock them.
    "leader_roster_list": "admin_leader_roster_list",
    "leader_roster_candidates": "admin_leader_roster_candidates",
    "leader_roster_add": "admin_leader_roster_add",
    "leader_roster_revoke": "admin_leader_roster_revoke",
}

ADMIN_LITE_POLICY_CAPABILITY_IDS = frozenset(ADMIN_POLICY_CAPABILITY_BY_ACTION.values())

ADMIN_AUDIT_READ_ACTIONS: frozenset[AdminAction] = frozenset(
    {
        "tasks_list",
        "task_events_list",
        "bindings_list",
        "traces_list",
    }
)
ADMIN_AUDIT_READ_POLICY_CAPABILITY_IDS = frozenset(
    ADMIN_POLICY_CAPABILITY_BY_ACTION[action] for action in ADMIN_AUDIT_READ_ACTIONS
)
