"""Pre-browser failure diagnostics through real catches; no DB/browser proof."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from app.browser_skill.models import BrowserOwner
from app.browser_skill.publication_contracts import BrowserPublicationRecord
from app.infra.browser.read_execution import (
    BrowserReadExecutionFactory,
    VerifiedBrowserReadExecution,
)
from app.infra.persistence.browser.payload_crypto import BrowserPayloadCipher
from app.ports.browser_read_execution import BrowserReadExecutionError, BrowserWorkerCheckpoint
from app.ports.browser_store import BrowserLeaseStorePort
from app.ports.credential_vault import BrowserBindingReaderPort, BrowserCurrentAuthPort
from tests.browser_skill.test_runtime import persisted_run
from tests.infra.browser.test_local_read_execution import factory_at, registered_site

PRIVATE_TEXT = "synthetic-private-exception-body-never-export"
PrivateFailure = type("SyntheticPrivateFailureClass", (RuntimeError,), {})


def _diagnostic(caplog: pytest.LogCaptureFixture) -> dict[str, object]:
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.name == "app.infra.browser.read_execution"
    prefix, body = record.getMessage().split(" ", 1)
    assert prefix == "browser_read_execution_diagnostic"
    assert record.exc_info is None and record.stack_info is None
    assert PRIVATE_TEXT not in caplog.text
    assert "SyntheticPrivateFailureClass" not in caplog.text
    return json.loads(body)


@pytest.mark.parametrize("point,error,expected_type", [
    ("publication", RuntimeError(PRIVATE_TEXT), "RuntimeError"),
    ("input", ValueError(PRIVATE_TEXT), "ValueError"),
    ("input", PrivateFailure(PRIVATE_TEXT), "UnknownException"),
])
def test_bridge_logs_safe_original_stage_and_preserves_failure_mapping(
    point: str, error: Exception, expected_type: str, caplog: pytest.LogCaptureFixture,
) -> None:
    manifest = registered_site().manifest
    original = persisted_run(phase="acquiring")
    run = replace(original, admission=replace(
        original.admission, publication_digest=bytes.fromhex(manifest.digest),
    ))
    factory = Mock(spec=BrowserReadExecutionFactory)
    factory.open = AsyncMock()
    cipher = Mock(spec=BrowserPayloadCipher)
    publications = Mock()
    publications.get_frozen = AsyncMock(return_value=BrowserPublicationRecord(
        manifest=manifest, state="active", activation_revision=1,
    ))
    if point == "publication":
        publications.get_frozen.side_effect = error
    else:
        cipher.decrypt_input.side_effect = error
    bridge = VerifiedBrowserReadExecution(
        factory, cipher, publications, Mock(), result_digest_key=b"r" * 32,
    )
    checkpoint = Mock(spec=BrowserWorkerCheckpoint)
    checkpoint.refresh = AsyncMock(return_value=run)
    with pytest.raises(BrowserReadExecutionError) as failed:
        asyncio.run(bridge.execute(run, checkpoint))
    assert failed.value.code == "invalid_response"
    assert _diagnostic(caplog) == {
        "stage": "bridge_publication_read" if point == "publication" else "bridge_input",
        "exception_type": expected_type,
        "trusted_error_code": "invalid_response",
    }
    factory.open.assert_not_awaited()
    publications.get_frozen.assert_awaited_once_with(run.owner, manifest.digest)
    if point == "publication":
        cipher.decrypt_input.assert_not_called()
    else:
        cipher.decrypt_input.assert_called_once_with(run.admission)


@pytest.mark.parametrize("error,expected_type,code", [
    (RuntimeError(PRIVATE_TEXT), "RuntimeError", "unavailable"),
    (PrivateFailure(PRIVATE_TEXT), "UnknownException", "unavailable"),
    (BrowserReadExecutionError("denied"), "BrowserReadExecutionError", "denied"),
])
def test_factory_logs_safe_driver_stage_before_existing_conversion_and_cleanup(
    tmp_path: Path, error: Exception, expected_type: str, code: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    factory = factory_at(tmp_path)
    leases = Mock(spec=BrowserLeaseStorePort)
    factory.install_authority(
        leases=leases, current_auth=Mock(spec=BrowserCurrentAuthPort),
        binding_reader=Mock(spec=BrowserBindingReaderPort),
    )
    original = persisted_run(phase="acquiring")
    owner = BrowserOwner(
        tenant_id=factory.binding.tenant_id, user_id=factory.binding.ai_user_id,
        session_id="diagnostic_session",
    )
    run = replace(original, admission=replace(
        original.admission, owner=owner, run_id="diagnostic_run",
        publication_digest=bytes.fromhex(factory.source.manifest.digest),
    ), lease_epoch=None, provider_key=None, provider_manifest_digest=None)
    factory.resources.ensure_started = AsyncMock(side_effect=error)
    private_input = {
        "schema_version": "browser.request.input.v2", "channel": "web",
        "capability_id": factory.source.manifest.capability.capability_id,
        "principal": {
            "ai_user_id": owner.user_id, "display_name": "Synthetic user", "roles": [],
            "org_ctx": {"tenant_id": owner.tenant_id},
        },
        "arguments": {"business_key": "diagnostic_private_key"},
    }
    with pytest.raises(BrowserReadExecutionError) as failed:
        asyncio.run(factory.open(
            run, factory.source.manifest, private_input, Mock(spec=BrowserWorkerCheckpoint),
        ))
    assert failed.value.code == code
    if isinstance(error, BrowserReadExecutionError):
        assert failed.value is error
    assert _diagnostic(caplog) == {
        "stage": "factory_driver_start", "exception_type": expected_type,
        "trusted_error_code": code,
    }
    assert owner.session_id not in caplog.text and run.run_id not in caplog.text
    assert "diagnostic_private_key" not in caplog.text
    factory.resources.ensure_started.assert_awaited_once()
    leases.reserve.assert_not_awaited()
    assert run.run_id not in factory._opening
    state = factory._states[run.run_id]
    assert state.cancellation.is_set() and state.claim is None and state.resource is None
