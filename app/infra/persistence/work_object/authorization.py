"""One SQL authorization source for manual roots and their private children."""

from __future__ import annotations

import time

from app.ports.work_object_dispatch import WorkDispatchError
from app.ports.work_object_scope import AuthorizedWorkObjectScope


def scope_parameters(scope: AuthorizedWorkObjectScope) -> dict[str, object]:
    fresh = scope.valid_until is not None and time.monotonic() <= scope.valid_until
    return {
        "principal_tenant_id": scope.principal_tenant_id,
        "principal_ai_user_id": scope.principal_ai_user_id,
        "principal_directory_user_id": scope.principal_directory_user_id,
        "responsible_department_ids": sorted(scope.responsible_department_ids) if fresh else [],
    }


def recheck_scope(scope: AuthorizedWorkObjectScope) -> None:
    if scope.valid_until is not None and time.monotonic() > scope.valid_until:
        raise WorkDispatchError("organization_directory_stale")


def relation_participant_sql(alias: str = "r") -> str:
    return f"""({alias}.superseded_at IS NULL AND (
        {alias}.assignee_ai_user_id=:principal_ai_user_id
        OR {alias}.assignee_directory_user_id=CAST(:principal_directory_user_id AS TEXT)
        OR ({alias}.target_kind='user' AND
            {alias}.target_directory_user_id=CAST(:principal_directory_user_id AS TEXT))
        OR ({alias}.target_kind='department' AND
            {alias}.target_department_id=ANY(CAST(:responsible_department_ids AS TEXT[])))
    ))"""


def root_visibility_sql(
    scope: AuthorizedWorkObjectScope, alias: str = "wo"
) -> tuple[str, dict[str, object]]:
    # Only programmer-owned aliases enter SQL; all identity values are parameters.
    predicate = f"""({alias}.tenant_id=:principal_tenant_id AND (
      {alias}.initiator_ai_user_id=:principal_ai_user_id
      OR EXISTS(SELECT 1 FROM work_object_handling_relations auth_relation
        WHERE auth_relation.tenant_id={alias}.tenant_id
          AND auth_relation.work_object_id={alias}.work_object_id
          AND {relation_participant_sql("auth_relation")})
      OR ({alias}.dispatch_model_version IS NULL AND (
        {alias}.assignee_ai_user_id=:principal_ai_user_id
        OR {alias}.accepted_by_ai_user_id=:principal_ai_user_id
        OR {alias}.assignee_directory_user_id=CAST(:principal_directory_user_id AS TEXT)))
    ))"""
    return predicate, scope_parameters(scope)


def child_visibility_sql(
    scope: AuthorizedWorkObjectScope,
    *,
    root_alias: str = "wo",
    relation_alias: str = "r",
    visibility_expr: str = "child.visibility",
) -> tuple[str, dict[str, object]]:
    root, parameters = root_visibility_sql(scope, root_alias)
    return (
        f"({root} AND ({visibility_expr}='shared' OR ("
        f"{relation_alias}.tenant_id={root_alias}.tenant_id AND "
        f"{relation_alias}.work_object_id={root_alias}.work_object_id AND "
        f"{relation_participant_sql(relation_alias)})))",
        parameters,
    )
