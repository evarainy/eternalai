"""Private file spool rejects wrong owner, spoofed type and changed bytes."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.infra.browser.local_file_artifacts import (
    FileArtifactOwner,
    LocalFileArtifactError,
    LocalFileArtifacts,
    StoredFileArtifact,
    initialize_file_artifact_storage_scope,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Authority:
    def __init__(self, root: Path) -> None:
        self.storage_scope = initialize_file_artifact_storage_scope(root)
        self.records: dict[str, StoredFileArtifact] = {}
        self.allowed: FileArtifactOwner | None = None

    async def check_create(self, owner: FileArtifactOwner) -> None:
        if owner != self.allowed:
            raise LocalFileArtifactError("denied")

    async def record(self, artifact: StoredFileArtifact) -> None:
        if artifact.storage_scope != self.storage_scope:
            raise LocalFileArtifactError("browser_file_storage_scope_mismatch")
        self.records[artifact.reference] = artifact

    async def authorize_read(
        self, reference: str, owner: FileArtifactOwner,
    ) -> StoredFileArtifact:
        if owner != self.allowed:
            raise LocalFileArtifactError("denied")
        return self.records[reference]

    async def list_authorized(
        self, owner: FileArtifactOwner, limit: int,
    ) -> tuple[StoredFileArtifact, ...]:
        if owner != self.allowed:
            raise LocalFileArtifactError("denied")
        return tuple(self.records.values())[:limit]

    async def expired(
        self, before: datetime, limit: int,
    ) -> tuple[StoredFileArtifact, ...]:
        return tuple(record for record in self.records.values()
                     if record.expires_at <= before
                     and record.storage_scope == self.storage_scope)[:limit]

    async def confirm_removed(self, artifact: StoredFileArtifact) -> None:
        if artifact.storage_scope != self.storage_scope:
            raise LocalFileArtifactError("browser_file_storage_scope_mismatch")
        assert self.records[artifact.reference] == artifact
        del self.records[artifact.reference]


class _PermissiveAuthority(_Authority):
    async def authorize_read(
        self, reference: str, owner: FileArtifactOwner,
    ) -> StoredFileArtifact:
        return self.records[reference]

    async def list_authorized(
        self, owner: FileArtifactOwner, limit: int,
    ) -> tuple[StoredFileArtifact, ...]:
        return tuple(self.records.values())[:limit]


def _owner(user: str = "user") -> FileArtifactOwner:
    return FileArtifactOwner("tenant", user, "session", "oa", "binding", 1,
                             "task", "run")


@pytest.mark.anyio
async def test_sealed_file_requires_exact_owner_and_keeps_digest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    payload = b"%PDF-1.7\nsynthetic\nstartxref\n0\n%%EOF"
    source.write_bytes(payload)
    authority = _Authority(tmp_path / "private")
    authority.allowed = _owner()
    files = LocalFileArtifacts(tmp_path / "private", authority)
    artifact = await files.seal_download(
        source, authority.allowed, media_type="application/pdf", ttl_seconds=60,
    )
    assert artifact.size_bytes == len(payload)
    assert await files.list_artifacts(authority.allowed) == (artifact,)
    assert await files.read(artifact.reference, authority.allowed) == payload
    with pytest.raises(LocalFileArtifactError):
        await files.read(artifact.reference, _owner("other"))
    permissive = _PermissiveAuthority(tmp_path / "private")
    permissive.records = authority.records
    other_reader = LocalFileArtifacts(tmp_path / "private", permissive)
    for wrong in (
        replace(authority.allowed, tenant_id="other"),
        replace(authority.allowed, session_id="other"),
        replace(authority.allowed, binding_id="other"),
        replace(authority.allowed, run_id="other"),
    ):
        with pytest.raises(LocalFileArtifactError) as denied:
            await other_reader.read(artifact.reference, wrong)
        assert denied.value.code == "browser_file_access_denied"
        with pytest.raises(LocalFileArtifactError) as hidden:
            await other_reader.list_artifacts(wrong)
        assert hidden.value.code == "browser_file_access_denied"
    (tmp_path / "private" / artifact.reference).write_bytes(
        b"%PDF-1.7\nchanged\nstartxref\n0\n%%EOF"
    )
    with pytest.raises(LocalFileArtifactError) as error:
        await files.read(artifact.reference, authority.allowed)
    assert error.value.code == "browser_file_integrity_invalid"


@pytest.mark.anyio
async def test_login_html_cannot_be_sealed_as_download(tmp_path: Path) -> None:
    source = tmp_path / "login"
    source.write_bytes(b"  <!DOCTYPE html><html><body>login</body></html>")
    authority = _Authority(tmp_path / "private")
    authority.allowed = _owner()
    files = LocalFileArtifacts(tmp_path / "private", authority)
    with pytest.raises(LocalFileArtifactError) as error:
        await files.seal_download(
            source, authority.allowed, media_type="text/plain", ttl_seconds=60,
        )
    assert error.value.code == "browser_file_type_invalid"
    assert not authority.records
    assert [path.name for path in (tmp_path / "private").iterdir()] == [
        ".browser-file-storage-scope-v1",
    ]


@pytest.mark.anyio
async def test_expired_file_is_unreadable_then_removed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"synthetic text")
    authority = _Authority(tmp_path / "private")
    authority.allowed = _owner()
    files = LocalFileArtifacts(tmp_path / "private", authority)
    artifact = await files.seal_download(
        source, authority.allowed, media_type="text/plain", ttl_seconds=60,
    )
    authority.records[artifact.reference] = replace(
        artifact, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(LocalFileArtifactError) as error:
        await files.read(artifact.reference, authority.allowed)
    assert error.value.code == "browser_file_access_denied"
    assert await files.list_artifacts(authority.allowed) == ()
    assert await files.cleanup_expired() == 1
    assert not (tmp_path / "private" / artifact.reference).exists()
    assert not authority.records


@pytest.mark.anyio
async def test_cleanup_reports_confirmation_failure_and_keeps_record(tmp_path: Path) -> None:
    class FailingConfirmation(_Authority):
        async def confirm_removed(self, artifact: StoredFileArtifact) -> None:
            raise RuntimeError("database unavailable")

    source = tmp_path / "source"
    source.write_bytes(b"synthetic text")
    authority = FailingConfirmation(tmp_path / "private")
    authority.allowed = _owner()
    files = LocalFileArtifacts(tmp_path / "private", authority)
    artifact = await files.seal_download(
        source, authority.allowed, media_type="text/plain", ttl_seconds=60,
    )
    authority.records[artifact.reference] = replace(
        artifact, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(LocalFileArtifactError) as caught:
        await files.cleanup_expired()
    assert caught.value.code == "browser_file_cleanup_incomplete"
    assert files.last_cleanup == {
        "scanned": 1, "removed": 0, "failed": 1, "result": "incomplete",
    }
    assert artifact.reference in authority.records


def test_storage_scope_is_stable_for_shared_spool_and_distinct_for_other_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shared"
    first = initialize_file_artifact_storage_scope(root)
    assert initialize_file_artifact_storage_scope(root, create=False) == first
    assert initialize_file_artifact_storage_scope(root) == first
    assert initialize_file_artifact_storage_scope(tmp_path / "other") != first


def test_read_only_api_cannot_initialize_missing_marker(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir(mode=0o700)
    with pytest.raises(LocalFileArtifactError) as caught:
        initialize_file_artifact_storage_scope(root, create=False)
    assert caught.value.code == "browser_file_storage_scope_invalid"
    assert list(root.iterdir()) == []


def test_unmarked_legacy_spool_cannot_be_claimed(tmp_path: Path) -> None:
    root = tmp_path / "legacy"
    root.mkdir(mode=0o700)
    (root / "existing-file").write_bytes(b"synthetic legacy file")
    with pytest.raises(LocalFileArtifactError) as caught:
        initialize_file_artifact_storage_scope(root)
    assert caught.value.code == "browser_file_storage_scope_unknown"
    assert [path.name for path in root.iterdir()] == ["existing-file"]
    assert (root / "existing-file").read_bytes() == b"synthetic legacy file"


@pytest.mark.anyio
async def test_foreign_root_record_cannot_be_read_listed_or_acknowledged(tmp_path: Path) -> None:
    class GlobalScan(_PermissiveAuthority):
        async def expired(self, before: datetime, limit: int) -> tuple[StoredFileArtifact, ...]:
            return tuple(self.records.values())[:limit]

    authority_b = _Authority(tmp_path / "b")
    authority_b.allowed = _owner()
    files_b = LocalFileArtifacts(tmp_path / "b", authority_b)
    source = tmp_path / "source"
    source.write_bytes(b"synthetic file in B")
    artifact = await files_b.seal_download(
        source, _owner(), media_type="text/plain", ttl_seconds=60,
    )
    authority_a = GlobalScan(tmp_path / "a")
    authority_a.allowed = _owner()
    authority_a.records = authority_b.records
    files_a = LocalFileArtifacts(tmp_path / "a", authority_a)
    with pytest.raises(LocalFileArtifactError) as denied:
        await files_a.read(artifact.reference, _owner())
    assert denied.value.code == "browser_file_storage_scope_mismatch"
    with pytest.raises(LocalFileArtifactError) as hidden:
        await files_a.list_artifacts(_owner())
    assert hidden.value.code == "browser_file_storage_scope_mismatch"
    expired = replace(artifact, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    authority_a.records[artifact.reference] = expired
    with pytest.raises(LocalFileArtifactError) as cleanup:
        await files_a.cleanup_expired()
    assert cleanup.value.code == "browser_file_cleanup_incomplete"
    assert files_a.last_cleanup == {
        "scanned": 1, "removed": 0, "failed": 1, "result": "incomplete",
    }
    assert authority_b.records[artifact.reference] == expired
    assert (tmp_path / "b" / artifact.reference).read_bytes() == b"synthetic file in B"
    assert not (tmp_path / "a" / artifact.reference).exists()


@pytest.mark.anyio
async def test_already_absent_file_is_acknowledged_only_for_verified_same_root(
    tmp_path: Path,
) -> None:
    authority = _Authority(tmp_path / "private")
    authority.allowed = _owner()
    files = LocalFileArtifacts(tmp_path / "private", authority)
    artifact = StoredFileArtifact(
        "a" * 43, _owner(), "text/plain", 3, b"a" * 32,
        datetime.now(timezone.utc) - timedelta(seconds=1), authority.storage_scope,
    )
    authority.records[artifact.reference] = artifact
    assert not (tmp_path / "private" / artifact.reference).exists()
    assert await files.cleanup_expired() == 1
    assert authority.records == {}
    assert files.last_cleanup == {
        "scanned": 1, "removed": 1, "failed": 0, "result": "ok",
    }


@pytest.mark.anyio
async def test_marker_changed_during_scan_preserves_cleanup_record(tmp_path: Path) -> None:
    class ChangedMarker(_Authority):
        async def expired(self, before: datetime, limit: int) -> tuple[StoredFileArtifact, ...]:
            marker.write_text("f" * 64, encoding="ascii")
            return await super().expired(before, limit)

    root = tmp_path / "private"
    marker = root / ".browser-file-storage-scope-v1"
    authority = ChangedMarker(root)
    authority.allowed = _owner()
    files = LocalFileArtifacts(root, authority)
    artifact = StoredFileArtifact(
        "b" * 43, _owner(), "text/plain", 3, b"b" * 32,
        datetime.now(timezone.utc) - timedelta(seconds=1), authority.storage_scope,
    )
    authority.records[artifact.reference] = artifact
    with pytest.raises(LocalFileArtifactError) as caught:
        await files.cleanup_expired()
    assert caught.value.code == "browser_file_cleanup_incomplete"
    assert authority.records[artifact.reference] == artifact
    assert files.last_cleanup["removed"] == 0 and files.last_cleanup["failed"] == 1


def test_authority_cannot_be_attached_to_different_root(tmp_path: Path) -> None:
    authority = _Authority(tmp_path / "a")
    initialize_file_artifact_storage_scope(tmp_path / "b")
    with pytest.raises(LocalFileArtifactError) as caught:
        LocalFileArtifacts(tmp_path / "b", authority)
    assert caught.value.code == "browser_file_storage_scope_mismatch"
