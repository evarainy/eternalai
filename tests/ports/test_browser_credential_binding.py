"""Pure browser facts reject fabricated version and malformed capture metadata."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.browser_skill.models import BrowserOwner
from app.ports.credential_vault import BrowserAuthFact, BrowserBindingFact


def test_auth_capture_preserves_full_session_and_requires_actual_version():
    owner = BrowserOwner(
        tenant_id="tenant", user_id="user", session_id="sid_v1.synthetic.signature"
    )
    fact = BrowserAuthFact(owner, 7, bytes(range(32)), datetime(2099, 1, 1, tzinfo=UTC))
    assert fact.owner.session_id == owner.session_id
    for invalid in (True, -1, 9007199254740992):
        with pytest.raises(ValueError, match="browser_auth_fact_invalid"):
            replace(fact, authorization_revision=invalid)
    with pytest.raises(ValueError, match="browser_auth_fact_invalid"):
        replace(fact, expires_at=datetime(2099, 1, 1))


def test_binding_fact_requires_positive_revision_and_full_subject_digest():
    fact = BrowserBindingFact("tenant", "user", "oa", "binding", 1, bytes(range(32)))
    assert fact.binding_revision == 1
    for invalid in (0, True, 9007199254740992):
        with pytest.raises(ValueError, match="browser_binding_fact_invalid"):
            replace(fact, binding_revision=invalid)
    with pytest.raises(ValueError, match="browser_binding_fact_invalid"):
        replace(fact, subject_digest=b"incomplete")
