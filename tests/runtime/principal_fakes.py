"""Synthetic trusted principals for Runtime contract tests."""

from app.ports.auth import Principal, PrincipalOrgContext


def runtime_principal(
    ai_user_id: str = "ai-user-1",
    *,
    tenant_id: str = "tenant-test",
) -> Principal:
    return Principal(
        ai_user_id=ai_user_id,
        display_name="Runtime Test User",
        roles=("user",),
        org_ctx=PrincipalOrgContext(tenant_id=tenant_id),
    )
