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
)


class _Authority:
    def __init__(self) -> None:
        self.records: dict[str, StoredFileArtifact] = {}
        self.allowed: FileArtifactOwner | None = None

    async def check_create(self, owner: FileArtifactOwner) -> None:
        if owner != self.allowed:
            raise LocalFileArtifactError("denied")

    async def record(self, artifact: StoredFileArtifact) -> None:
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
                     if record.expires_at <= before)[:limit]

    async def confirm_removed(self, artifact: StoredFileArtifact) -> None:
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


@pytest.mark.asyncio
async def test_sealed_file_requires_exact_owner_and_keeps_digest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    payload = b"%PDF-1.7\nsynthetic\nstartxref\n0\n%%EOF"
    source.write_bytes(payload)
    authority = _Authority()
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
    permissive = _PermissiveAuthority()
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


@pytest.mark.asyncio
async def test_login_html_cannot_be_sealed_as_download(tmp_path: Path) -> None:
    source = tmp_path / "login"
    source.write_bytes(b"  <!DOCTYPE html><html><body>login</body></html>")
    authority = _Authority()
    authority.allowed = _owner()
    files = LocalFileArtifacts(tmp_path / "private", authority)
    with pytest.raises(LocalFileArtifactError) as error:
        await files.seal_download(
            source, authority.allowed, media_type="text/plain", ttl_seconds=60,
        )
    assert error.value.code == "browser_file_type_invalid"
    assert not authority.records
    assert not list((tmp_path / "private").iterdir())


@pytest.mark.asyncio
async def test_expired_file_is_unreadable_then_removed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"synthetic text")
    authority = _Authority()
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


@pytest.mark.asyncio
async def test_cleanup_reports_confirmation_failure_and_keeps_record(tmp_path: Path) -> None:
    class FailingConfirmation(_Authority):
        async def confirm_removed(self, artifact: StoredFileArtifact) -> None:
            raise RuntimeError("database unavailable")

    source = tmp_path / "source"
    source.write_bytes(b"synthetic text")
    authority = FailingConfirmation()
    authority.allowed = _owner()
    files = LocalFileArtifacts(tmp_path / "private", authority)
    artifact = await files.seal_download(
        source, authority.allowed, media_type="text/plain", ttl_seconds=60,
    )
    authority.records[artifact.reference] = replace(
        artifact, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(LocalFileArtifactError, match="browser_file_cleanup_incomplete"):
        await files.cleanup_expired()
    assert files.last_cleanup == {
        "scanned": 1, "removed": 0, "failed": 1, "result": "incomplete",
    }
    assert artifact.reference in authority.records
