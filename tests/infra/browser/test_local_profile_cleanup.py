from datetime import datetime, timedelta, timezone

from app.infra.browser.local_profile_cleanup import retention_ready


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
