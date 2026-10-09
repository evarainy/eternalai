import asyncio
from datetime import datetime, timedelta, timezone
from typing import cast

from app.infra.browser.local_profile_cleanup import ProfileCleanupScheduler, retention_ready
from app.ports.auth import Principal, PrincipalOrgContext
from app.ports.browser_profile_store import (
    BrowserProfileContext,
    BrowserProfileStorePort,
    ProfileCleanupCandidatePage,
)
from app.ports.browser_store import BrowserLeaseClaim


def test_retired_generation_needs_head_transition_older_than_retention() -> None:
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=4)
    assert retention_ready(
        disposition="retired", created_at=old, head_updated_at=old,
        now=now, retention_seconds=86_400,
    )
    assert not retention_ready(
        disposition="retired", created_at=old,
        head_updated_at=now - timedelta(hours=1),
        now=now, retention_seconds=86_400,
    )


def test_orphan_without_transition_timestamp_never_qualifies() -> None:
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=4)
    assert not retention_ready(
        disposition="orphan", created_at=old, head_updated_at=old,
        now=now, retention_seconds=86_400,
    )


def test_database_transition_timestamp_gates_new_orphan_and_retired_rows() -> None:
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=4)
    recent = now - timedelta(hours=1)
    for disposition in ("orphan", "retired"):
        assert retention_ready(
            disposition=disposition, created_at=old, head_updated_at=recent,
            disposition_changed_at=old, now=now, retention_seconds=86_400,
        )
        assert not retention_ready(
            disposition=disposition, created_at=old, head_updated_at=old,
            disposition_changed_at=recent, now=now, retention_seconds=86_400,
        )


def test_profile_scheduler_uses_supplied_context_and_bounded_candidate_page() -> None:
    context = BrowserProfileContext(
        principal=Principal(
            ai_user_id="synthetic_user", display_name="Synthetic", roles=(),
            org_ctx=PrincipalOrgContext(tenant_id="synthetic_tenant"),
        ),
        claim=cast(BrowserLeaseClaim, object()),
        task_id="task_1", run_id="run_1", worker_epoch=1,
    )

    class Store:
        def __init__(self) -> None:
            self.discovered: list[tuple[BrowserProfileContext, int, str | None]] = []
            self.cleaned: list[tuple[BrowserProfileContext, str]] = []

        async def discover_cleanup_candidates(
            self, scope: BrowserProfileContext, *, limit: int,
            after_generation_id: str | None,
        ) -> ProfileCleanupCandidatePage:
            self.discovered.append((scope, limit, after_generation_id))
            return ProfileCleanupCandidatePage(("generation_1",), (), None)

        async def cleanup(self, scope: BrowserProfileContext, generation_id: str) -> None:
            self.cleaned.append((scope, generation_id))

    async def run() -> None:
        store = Store()

        async def contexts() -> tuple[BrowserProfileContext, ...]:
            return (context,)

        scheduler = ProfileCleanupScheduler(
            cast(BrowserProfileStorePort, store), contexts, batch_limit=2,
        )
        await scheduler.sweep()
        assert store.discovered == [(context, 2, None)]
        assert store.cleaned == [(context, "generation_1")]
        assert scheduler.last_result == (1, 1, 0)

    asyncio.run(run())
