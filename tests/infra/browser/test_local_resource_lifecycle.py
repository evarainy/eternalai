"""Reject unverifiable native IPC and cleanup; never manufacture an exit proof."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from app.infra.browser.deployment_manifest import LocalOfflineManifest
from app.infra.browser.local_resource_lifecycle import (
    LocalBrowserReadLifecycle,
    LocalChromiumDeployment,
    _NativeChromium,
    local_deployment_configuration_digest,
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


@pytest.mark.parametrize("case,stage,exception_type", [
    ("native_failure", "browser_launch", "ValueError"),
    ("untrusted_fields", "unknown", "ValueError"),
    ("wrong_request_id", "native_response", "ValueError"),
    ("malformed_json", "native_response", "JSONDecodeError"),
    ("empty_line", "native_read", "ValueError"),
])
def test_native_launch_retains_only_matched_closed_failure_diagnostic(
    case: str, stage: str, exception_type: str, caplog: pytest.LogCaptureFixture,
) -> None:
    private_text = "synthetic-private-native-exception-never-export"
    response = {
        "id": 1, "ok": False, "code": "unavailable", "stage": "browser_launch",
        "reason": "filesystem_unwritable", "native_code": "EROFS",
        "features": {"home_readonly": True, "crashpad_database_missing": False},
        "message": private_text, "endpoint": private_text, "identity": private_text,
    }
    if case == "untrusted_fields":
        response.update({"stage": private_text, "reason": private_text, "native_code": private_text,
                         "features": {"home_readonly": private_text, "timeout": 0,
                                      private_text: True}})
    elif case == "wrong_request_id":
        response["id"] = 2
    line = json.dumps(response).encode() + b"\n"
    if case == "malformed_json":
        line = private_text.encode()
    elif case == "empty_line":
        line = b""
    process = Mock(returncode=None)
    process.stdin.drain = AsyncMock()
    process.stdout.readline = AsyncMock(return_value=line)
    native = _NativeChromium(process, "a" * 64, 1, b"n" * 32, b"c" * 32)
    with pytest.raises(BrowserReadExecutionError) as failed:
        asyncio.run(native.call("launch"))
    assert failed.value.code == "unavailable"
    assert native.original is None and native.exited is False
    assert len(caplog.records) == 1
    record = caplog.records[0]
    prefix, body = record.getMessage().split(" ", 1)
    assert prefix == "browser_native_launch_diagnostic"
    assert record.exc_info is None and record.stack_info is None
    expected = {
        "stage": stage, "exception_type": exception_type, "trusted_error_code": "unavailable",
    }
    if case == "native_failure":
        expected.update({
            "native_reason": "filesystem_unwritable", "native_code": "EROFS",
            "features": {"home_readonly": True, "crashpad_database_missing": False},
        })
    elif case == "untrusted_fields":
        expected.update({"native_reason": "unknown", "native_code": None, "features": {}})
    assert json.loads(body) == expected
    assert private_text not in caplog.text and "a" * 64 not in caplog.text
    process.stdin.drain.assert_awaited_once()
    process.stdout.readline.assert_awaited_once()


def test_successful_native_launch_ignores_failure_fields_without_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    original = {"identity": "a" * 64, "pid": 1234, "start": "654321",
                "boot": "12345678-1234-1234-1234-123456789012"}
    result = {**original, "exited": False, "endpoint": "ws://127.0.0.1:1234/fixture"}
    response = {"id": 1, "ok": True, "result": result, "stage": "untrusted_extra"}
    process = Mock(returncode=None)
    process.stdin.drain = AsyncMock()
    process.stdout.readline = AsyncMock(return_value=json.dumps(response).encode() + b"\n")
    native = _NativeChromium(process, "a" * 64, 1, b"n" * 32, b"c" * 32)
    assert asyncio.run(native.call("launch")) == result
    assert native.original == original and native.exited is False
    assert caplog.records == []


def test_resource_launch_marks_failed_driver_probe_before_native_work(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    factory = factory_at(tmp_path)
    resources = factory.resources
    # Explicit synthetic asset/probe boundaries, not asset or driver health proof.
    resources._verify_assets = Mock(return_value=0.0)
    resources.driver.probe = AsyncMock(return_value=False)
    guard, subject_guard = AsyncMock(), AsyncMock()
    with pytest.raises(BrowserReadExecutionError) as failed:
        asyncio.run(resources.launch(
            Mock(), Mock(), factory.source, guard=guard, subject_guard=subject_guard,
        ))
    assert failed.value.code == "unavailable"
    assert len(caplog.records) == 1
    prefix, body = caplog.records[0].getMessage().split(" ", 1)
    assert prefix == "browser_resource_launch_diagnostic"
    assert json.loads(body) == {
        "stage": "driver_probe", "exception_type": "BrowserReadExecutionError",
        "trusted_error_code": "unavailable",
    }
    assert resources._helpers == {} and resources._resources == {}
    resources.driver.probe.assert_awaited_once()
    guard.assert_not_awaited()
    subject_guard.assert_not_awaited()


def test_subject_digest_separates_owner_and_object() -> None:
    digest = local_subject_digest("tenant", "owner")
    assert len(digest) == 32
    assert digest != local_subject_digest("other_tenant", "owner")
    assert digest != local_subject_digest("tenant", "other_owner")
    assert digest != local_subject_digest("tenant", "owner", "other_object")


def test_offline_lock_rejects_configuration_change(tmp_path: Path) -> None:
    node, package, browsers = (tmp_path / name for name in ("node", "playwright", "browsers"))
    helper_hash, node_hash, chromium_hash = (b"h" * 32, b"n" * 32, b"c" * 32)
    config_digest = local_deployment_configuration_digest(
        provider_key="local", node_executable=node, playwright_package=package,
        browsers_path=browsers, assets_root=tmp_path,
        helper_digest=helper_hash, node_digest=node_hash,
        chromium_digest=chromium_hash, enabled=True, timeout_seconds=25.0,
        immutable_assets=False,
    )
    assets = []
    for role, digest in (
        ("node", node_hash), ("chromium", chromium_hash),
        ("playwright", b"p" * 32), ("helper", helper_hash),
        ("font", b"f" * 32), ("certificate", b"t" * 32),
    ):
        assets.append({
            "asset_id": role, "role": role, "relative_path": role + ".bin",
            "sha256": digest.hex(), "license_ref": "synthetic-license-record",
        })
    body = {
        "deployment_id": "fixture", "assets": tuple(assets),
        "runtime": {
            "platform": "linux", "architecture": "x86_64",
            "playwright_version": "1.63.0", "node_version": "v22.0.0",
            "chromium_revision": "fixture", "configuration_digest": config_digest,
        },
    }
    body["manifest_digest"] = hashlib.sha256((
        "browser.local.offline.v1\n"
        + json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    ).encode("ascii")).hexdigest()
    manifest = LocalOfflineManifest.model_validate(body)
    common = dict(
        provider_key="local", manifest_digest=bytes.fromhex(manifest.manifest_digest),
        node_executable=node, playwright_package=package, browsers_path=browsers,
        helper_digest=helper_hash, node_digest=node_hash,
        chromium_digest=chromium_hash, enabled=True, assets_root=tmp_path,
        offline_manifest=manifest,
    )
    LocalChromiumDeployment(**common)
    with pytest.raises(ValueError, match="configuration_mismatch"):
        LocalChromiumDeployment(**common, timeout_seconds=20.0)


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
