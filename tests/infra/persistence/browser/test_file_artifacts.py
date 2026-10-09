"""MAC metadata and scoped PG cleanup contract; no live database in this harness."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.infra.browser.local_file_artifacts import (
    FileArtifactOwner,
    LocalFileArtifactError,
    StoredFileArtifact,
)
from app.infra.persistence.browser import file_artifacts as module
from app.infra.persistence.browser.file_artifacts import PostgreSQLBrowserFileArtifactAuthority


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def one_or_none(self):
        assert len(self.rows) <= 1
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self):
        assert self.rows == []
        return None


class _Session:
    def __init__(self):
        self.rows = []
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    @asynccontextmanager
    async def begin(self):
        yield

    async def execute(self, statement, parameters):
        sql = str(statement)
        self.calls.append((sql, parameters))
        if "SELECT e.event_type,e.payload" in sql:
            return _Result([{"event_type": module._EVENT, "payload": self.rows[0]["payload"]}])
        if "SELECT payload FROM task_events" in sql:
            return _Result([])
        return _Result(self.rows)


def _authority(scope="a" * 64):
    session = _Session()
    authority = PostgreSQLBrowserFileArtifactAuthority(
        lambda: session, {"test": b"k" * 32}, "test", storage_scope=scope,
    )
    return authority, session


def _artifact(scope="a" * 64):
    return StoredFileArtifact(
        "r" * 43, FileArtifactOwner("tenant", "user", "session", "oa", "binding", 1,
                                    "task", "run"),
        "text/plain", 3, b"d" * 32, datetime.now(UTC) - timedelta(seconds=1), scope,
    )


def _payload(authority, artifact):
    return authority._seal({
        "reference": artifact.reference, "owner": authority._owner_data(artifact.owner),
        "media_type": artifact.media_type, "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256.hex(), "expires_at": artifact.expires_at.isoformat(),
        "storage_scope": artifact.storage_scope,
    }, module._EVENT)


@pytest.mark.asyncio
async def test_expiration_query_is_bound_to_storage_scope_and_unknown_records_are_visible() -> None:
    authority, session = _authority()
    artifact = _artifact()
    session.rows = [{"payload": _payload(authority, artifact)}]
    assert await authority.expired(datetime.now(UTC), 10) == (artifact,)
    sql, parameters = session.calls[0]
    assert parameters["scope"] == authority.storage_scope
    assert "e.payload->>'storage_scope'=:scope" in sql
    assert "e.payload->>'storage_scope' IS NULL" in sql
    assert "jsonb_typeof(e.payload->'storage_scope')<>'string'" in sql
    assert "e.payload->>'storage_scope' !~ :scope_pattern" in sql
    assert parameters["scope_pattern"] == "^[0-9a-f]{64}$"
    assert "removed.payload->>'storage_scope'=e.payload->>'storage_scope'" in sql
    assert "removed.payload->>'storage_scope'=:scope" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [None, "", "unknown", 7])
async def test_signed_legacy_or_unknown_scope_fails_scan_without_deletion(scope) -> None:
    authority, session = _authority()
    data = {key: value for key, value in _payload(authority, _artifact()).items()
            if key not in {"mac", "key_id", "schema_version", "storage_scope"}}
    if scope is not None:
        data["storage_scope"] = scope
    session.rows = [{"payload": authority._seal(data, module._EVENT)}]
    with pytest.raises(LocalFileArtifactError) as caught:
        await authority.expired(datetime.now(UTC), 10)
    assert caught.value.code == "browser_file_storage_scope_unknown"
    assert len(session.rows) == 1 and len(session.calls) == 1


@pytest.mark.asyncio
async def test_foreign_scope_cannot_be_scanned_or_confirmed_even_if_returned_by_database() -> None:
    authority, session = _authority()
    foreign = _artifact("b" * 64)
    session.rows = [{"payload": _payload(authority, foreign)}]
    with pytest.raises(LocalFileArtifactError) as scan:
        await authority.expired(datetime.now(UTC), 10)
    assert scan.value.code == "browser_file_storage_scope_mismatch"
    calls = len(session.calls)
    with pytest.raises(LocalFileArtifactError) as ack:
        await authority.confirm_removed(foreign)
    assert ack.value.code == "browser_file_storage_scope_mismatch"
    assert len(session.calls) == calls and len(session.rows) == 1


@pytest.mark.asyncio
async def test_same_scope_expired_ack_is_mac_bound_to_exact_stored_artifact(monkeypatch) -> None:
    authority, session = _authority()
    artifact = _artifact()
    session.rows = [{"payload": _payload(authority, artifact)}]
    events = []

    async def append(session, **kwargs):
        events.append(kwargs["event"])

    monkeypatch.setattr(module, "append_owned_event", append)
    await authority.confirm_removed(artifact)
    assert len(events) == 1 and events[0].event_type == module._REMOVED
    removed = authority._decode(events[0].payload, module._REMOVED)
    assert removed["storage_scope"] == artifact.storage_scope
    assert removed["reference"] == artifact.reference
    assert removed["owner"] == authority._owner_data(artifact.owner)
    assert session.calls[0][1] == {
        "task": artifact.owner.task_id,
        "event": authority._event_id(module._EVENT, artifact.reference),
        "tenant": artifact.owner.tenant_id, "user": artifact.owner.ai_user_id,
        "session": artifact.owner.session_id,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["tenant_id", "ai_user_id", "session_id", "binding_id", "run_id"])
async def test_cleanup_ack_retains_exact_owner_binding(field, monkeypatch) -> None:
    authority, session = _authority()
    stored = _artifact()
    session.rows = [{"payload": _payload(authority, stored)}]
    wrong = replace(stored, owner=replace(stored.owner, **{field: "other"}))
    events = []

    async def append(session, **kwargs):
        events.append(kwargs["event"])

    monkeypatch.setattr(module, "append_owned_event", append)
    with pytest.raises(LocalFileArtifactError) as caught:
        await authority.confirm_removed(wrong)
    assert caught.value.code == "browser_file_access_denied"
    assert events == [] and len(session.rows) == 1


@pytest.mark.asyncio
async def test_unexpired_metadata_cannot_be_acknowledged() -> None:
    authority, session = _authority()
    unexpired = replace(_artifact(), expires_at=datetime.now(UTC) + timedelta(seconds=60))
    with pytest.raises(LocalFileArtifactError) as caught:
        await authority.confirm_removed(unexpired)
    assert caught.value.code == "browser_file_cleanup_invalid"
    assert session.calls == []


@pytest.mark.asyncio
async def test_same_scope_listing_surfaces_unknown_ownership(monkeypatch) -> None:
    authority, session = _authority()
    artifact = _artifact()
    data = {key: value for key, value in _payload(authority, artifact).items()
            if key not in {"mac", "key_id", "schema_version", "storage_scope"}}
    session.rows = [{"payload": authority._seal(data, module._EVENT)}]

    async def current(session, owner):
        assert owner == artifact.owner

    monkeypatch.setattr(authority, "_current_run_in_session", current)
    with pytest.raises(LocalFileArtifactError) as caught:
        await authority.list_authorized(artifact.owner, 10)
    assert caught.value.code == "browser_file_storage_scope_unknown"
    assert session.calls[0][1]["scope"] == authority.storage_scope
    assert "e.payload->>'storage_scope' IS NULL" in session.calls[0][0]
    assert len(session.rows) == 1


def test_storage_scope_cannot_be_changed_without_invalidating_metadata_mac() -> None:
    authority, _ = _authority()
    payload = _payload(authority, _artifact())
    payload["storage_scope"] = "b" * 64
    with pytest.raises(LocalFileArtifactError) as caught:
        authority._artifact(payload)
    assert caught.value.code == "browser_file_metadata_invalid"
