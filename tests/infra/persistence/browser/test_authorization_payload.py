"""Private request version decoding only; real AEAD, no DB or authorization grant."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Literal

import pytest

from app.infra.persistence.browser.payload_crypto import BrowserRunCryptoIdentity
from app.ports.browser_run_store import CanonicalRequest
from app.ports.credential_vault import BrowserAuthorizationError
from tests.infra.persistence.browser.test_runs_postgresql import RunHarness


@pytest.mark.parametrize("channel", ["web", "cli", "api", "mock"])
def test_v2_decodes_exact_channel_without_assuming_web(
    channel: Literal["web", "cli", "api", "mock"],
) -> None:
    h = RunHarness("postgresql://browser_v42_test@127.0.0.1:15432/eternalai_test")
    try:
        h.channel = channel
        request = CanonicalRequest(
            owner=h.owner, task_id="synthetic_task", trace_id=None,
            client_request_id="synthetic_request", request_digest_key_id="synthetic",
            request_digest=b"d" * 32, processing_owner="parser", processing_deadline=None,
            parse_winner=True, task_status="parsing", error_code=None, run=None,
        )
        admission = h.admission(request)
        assert h.cipher.decrypt_input(admission)["schema_version"] == "browser.request.input.v2"
        decoded = h.auth.input(admission)
        assert decoded.channel == channel
        assert decoded.principal == h.principal
        assert decoded.capability_id == h.capability.capability_id
    finally:
        asyncio.run(h.engine.dispose())


@pytest.mark.parametrize("case", ["v1", "v1_with_channel", "missing", "invalid", "unhashable"])
def test_unverifiable_channel_fails_closed(case: str) -> None:
    h = RunHarness("postgresql://browser_v42_test@127.0.0.1:15432/eternalai_test")
    try:
        request = CanonicalRequest(
            owner=h.owner, task_id="synthetic_task", trace_id=None,
            client_request_id="synthetic_request", request_digest_key_id="synthetic",
            request_digest=b"d" * 32, processing_owner="parser", processing_deadline=None,
            parse_winner=True, task_status="parsing", error_code=None, run=None,
        )
        admission = h.admission(request)
        payload = h.cipher.decrypt_input(admission)
        if case in {"v1", "v1_with_channel"}:
            payload["schema_version"] = "browser.request.input.v1"
        if case in {"v1", "missing"}:
            del payload["channel"]
        elif case == "invalid":
            payload["channel"] = "unknown"
        elif case == "unhashable":
            payload["channel"] = ["web"]
        protected = h.cipher.encrypt_input(
            BrowserRunCryptoIdentity.from_admission(admission), payload,
        )
        with pytest.raises(BrowserAuthorizationError) as error:
            h.auth.input(replace(admission, protected_input=protected))
        assert error.value.code == (
            "browser_authorization_channel_unavailable" if case.startswith("v1")
            else "browser_authorization_evidence_invalid"
        )
    finally:
        asyncio.run(h.engine.dispose())
