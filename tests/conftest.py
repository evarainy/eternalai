from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _declare_testing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the test-only environment exception explicit for every test."""
    monkeypatch.setenv("ENV", "testing")
    monkeypatch.delenv("PHASE0_MOCK_MODE", raising=False)
