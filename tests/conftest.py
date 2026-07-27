from __future__ import annotations

import base64
import os

import pytest

_TEST_KEY_B64 = base64.b64encode(b"test-only-key-material-32-bytes!").decode("ascii")

# Pytest imports application modules during collection, before fixtures run.
# These values are inert test configuration: all upstream network calls remain
# replaced by fixtures, while the module-level app still builds every dependency.
os.environ["ENV"] = "testing"
os.environ["REDIS_URL"] = "redis://redis.invalid:6379/0"
os.environ["OA_BASE_URL"] = "https://oa.invalid"
os.environ["OA_CREDENTIAL_TTL_S"] = "3600"
os.environ["SESSION_COOKIE_TTL_S"] = "3600"
os.environ["LLM_BASE_URL"] = "https://vllm.invalid/v1"
os.environ["LLM_MODEL"] = "qwen3.5-27b"
os.environ["ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64"] = _TEST_KEY_B64
os.environ["ETERNALAI_IDENTITY_HMAC_KEY_B64"] = _TEST_KEY_B64
os.environ["ETERNALAI_SESSION_SIGNING_KEY_B64"] = _TEST_KEY_B64
os.environ["ETERNALAI_SESSION_BINDING_KEY_B64"] = _TEST_KEY_B64


@pytest.fixture(autouse=True)
def _declare_testing_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the test-only environment exception explicit for every test."""
    monkeypatch.setenv("ENV", "testing")
    monkeypatch.delenv("PHASE0_MOCK_MODE", raising=False)
