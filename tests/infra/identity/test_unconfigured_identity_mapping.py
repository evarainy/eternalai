"""Fail-closed tests for the unconfigured identity mapping."""

from __future__ import annotations

import pytest

from app.infra.identity.unconfigured import UnconfiguredIdentityMapping


@pytest.mark.anyio
async def test_unconfigured_mutations_never_claim_a_binding_changed() -> None:
    mapping = UnconfiguredIdentityMapping()
    binding_id = "oa-session-v1:usr_v1_" + "a" * 43

    assert await mapping.revoke_mapping(binding_id) is None
    assert await mapping.reset_mapping(binding_id) is None
