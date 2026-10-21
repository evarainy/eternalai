"""The private lease contract validates full owner and monotonic bounded versions."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.browser_skill.models import BrowserOwner
from app.ports.browser_store import (
    MAX_BROWSER_REVISION,
    BrowserBindingKey,
    BrowserLeaseBindingSnapshot,
    BrowserLeaseClaim,
)
from app.ports.credential_vault import BrowserAuthFact, BrowserBindingFact


def claim() -> BrowserLeaseClaim:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    owner = BrowserOwner(tenant_id="tenant", user_id="user", session_id="sid_v1.synthetic")
    auth = BrowserAuthFact(owner, 7, b"a" * 32, now + timedelta(hours=1))
    binding = BrowserBindingFact("tenant", "user", "oa", "binding", 2, b"b" * 32)
    return BrowserLeaseClaim(auth, binding, 1, 1, "holder", "operation", "pool", now)


def test_claim_preserves_complete_session_identity_and_hides_private_facts() -> None:
    value = claim()
    assert value.auth.owner.session_id == "sid_v1.synthetic"
    assert "sid_v1.synthetic" not in repr(value)
    assert "operation" not in repr(value)


@pytest.mark.parametrize("value", [0, -1, MAX_BROWSER_REVISION + 1, True, 1.0])
@pytest.mark.parametrize("field", ["lease_epoch", "lease_revision"])
def test_claim_rejects_invalid_versions(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="browser_claim_invalid"):
        replace(claim(), **{field: value})


def test_claim_rejects_different_owner_and_naive_time() -> None:
    value = claim()
    with pytest.raises(ValueError, match="browser_claim_invalid"):
        replace(value, binding=replace(value.binding, tenant_id="another"))
    with pytest.raises(ValueError, match="browser_claim_invalid"):
        replace(value, deadline=value.deadline.replace(tzinfo=None))


def test_cleanup_snapshot_has_historical_identity_without_invented_subject() -> None:
    snapshot = BrowserLeaseBindingSnapshot("tenant", "user", "oa", "binding", 2)
    recovered = replace(claim(), binding=snapshot)
    assert recovered.binding.binding_revision == 2
    assert not hasattr(recovered.binding, "subject_digest")
    with pytest.raises(ValueError, match="browser_binding_snapshot_invalid"):
        replace(snapshot, binding_revision=0)
    with pytest.raises(ValueError, match="browser_binding_key_invalid"):
        BrowserBindingKey("tenant", "user", "unregistered", "binding")
