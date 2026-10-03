"""Reject unverifiable native IPC and cleanup; never manufacture an exit proof."""

import asyncio
import json
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from app.infra.browser.local_resource_lifecycle import (
    LocalBrowserReadLifecycle,
    _NativeChromium,
    local_subject_digest,
)
from app.ports.browser_read_execution import BrowserReadExecutionError
from app.ports.browser_store import BrowserLeaseError, BrowserProviderExpectation
from tests.browser_skill.test_runtime import persisted_run
from tests.infra.browser.test_local_read_execution import factory_at


@pytest.mark.parametrize("change", ["request_id", "pid", "start", "not_exited"])
def test_native_stop_rejects_mismatched_or_unproven_exit(change: str) -> None:
    original = {
        "identity": "a" * 64,
        "pid": 1234,
        "start": "654321",
        "boot": "12345678-1234-1234-1234-123456789012",
    }
    result = {**original, "exited": True}
    response = {"id": 1, "ok": True, "result": result}
    if change == "request_id":
        response["id"] = 2
    elif change == "pid":
        result["pid"] = 4321
    elif change == "start":
        result["start"] = "654322"
    else:
        result["exited"] = False
    process = Mock(returncode=None)
    process.stdin.drain = AsyncMock()
    process.stdout.readline = AsyncMock(return_value=json.dumps(response).encode() + b"\n")
    native = _NativeChromium(process, "a" * 64, 1, b"n" * 32, b"c" * 32, original=original)
    with pytest.raises(BrowserReadExecutionError) as denied:
        asyncio.run(native.call("stop"))
    assert denied.value.code == "unavailable"
    assert native.exited is False
    assert "654321" not in repr(native)


def test_unknown_receipt_and_resource_cannot_become_proof(tmp_path: Path) -> None:
    resources = factory_at(tmp_path).resources
    with pytest.raises(BrowserLeaseError) as denied:
        asyncio.run(resources.verify(cast(BrowserProviderExpectation, object()), b"untrusted"))
    assert denied.value.code == "browser_local_proof_invalid"
    with pytest.raises(BrowserLeaseError) as missing:
        resources.resource(b"untrusted_reference")
    assert missing.value.code == "browser_local_resource_unknown"


def test_subject_digest_separates_owner_and_object() -> None:
    digest = local_subject_digest("tenant", "owner")
    assert len(digest) == 32
    assert digest != local_subject_digest("other_tenant", "owner")
    assert digest != local_subject_digest("tenant", "other_owner")
    assert digest != local_subject_digest("tenant", "owner", "other_object")


def test_denied_cleanup_grant_never_reaches_resource_or_lease() -> None:
    factory, authority = Mock(), Mock()
    authority.check_recovery = AsyncMock(side_effect=BrowserLeaseError("cleanup_denied"))
    lifecycle = LocalBrowserReadLifecycle(factory, authority)
    with pytest.raises(BrowserLeaseError) as denied:
        asyncio.run(lifecycle.cleanup(persisted_run(phase="running")))
    assert denied.value.code == "cleanup_denied"
    factory.state_for_cleanup.assert_not_called()
    factory.resources.terminate.assert_not_called()
    assert lifecycle._cleanup == {} and lifecycle._cancel == {}


def test_profile_capture_remains_unsupported() -> None:
    checkpoint = Mock()
    checkpoint.refresh = AsyncMock(return_value=persisted_run(phase="verifying"))
    factory = Mock()
    lifecycle = LocalBrowserReadLifecycle(factory, Mock())
    run = persisted_run(phase="verifying")
    assert asyncio.run(lifecycle.lookup_capture(run, checkpoint)).status == "unsupported"
    assert asyncio.run(lifecycle.send_capture(run, checkpoint)).status == "unsupported"
    assert checkpoint.refresh.await_count == 2
    factory.resources.assert_not_called()
