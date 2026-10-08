"""Owner-scoped PostgreSQL metadata authority for private browser downloads."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.browser_skill.models import BrowserOwner
from app.infra.browser.local_file_artifacts import (
    FileArtifactOwner,
    LocalFileArtifactError,
    StoredFileArtifact,
)
from app.infra.persistence.browser.runs import PostgreSQLBrowserRunStore
from app.infra.persistence.task_store.transactional import append_owned_event
from app.ports.browser_run_store import RunSnapshot
from app.ports.task_store import TaskEventRecord

_EVENT = "browser.file_artifact.v1"
_REMOVED = "browser.file_removed.v1"


class PostgreSQLBrowserFileArtifactAuthority:
    """MAC-sealed Task events; every live read independently checks Run authority."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession],
        digest_keys: dict[str, bytes], active_key_id: str,
    ) -> None:
        if (not digest_keys or active_key_id not in digest_keys
                or any(type(key) is not bytes or len(key) != 32
                       for key in digest_keys.values())):
            raise ValueError("browser_file_authority_key_invalid")
        self._sessions = sessions
        self._keys = dict(digest_keys)
        self._active_key_id = active_key_id
        self._runs: PostgreSQLBrowserRunStore | None = None

    def install_runs(self, runs: PostgreSQLBrowserRunStore) -> None:
        if self._runs is not None or not isinstance(runs, PostgreSQLBrowserRunStore):
            raise ValueError("browser_file_authority_installation_invalid")
        if runs._sessions is not self._sessions:
            raise ValueError("browser_file_authority_session_mismatch")
        self._runs = runs

    @staticmethod
    def _event_id(kind: str, reference: str) -> str:
        return uuid5(NAMESPACE_URL, f"{kind}:{reference}").hex

    @staticmethod
    def _owner_data(owner: FileArtifactOwner) -> dict[str, object]:
        return {
            "tenant_id": owner.tenant_id, "ai_user_id": owner.ai_user_id,
            "session_id": owner.session_id, "target_system": owner.target_system,
            "binding_id": owner.binding_id,
            "binding_revision": owner.binding_revision,
            "task_id": owner.task_id, "run_id": owner.run_id,
        }

    def _mac(self, payload: dict[str, object], key_id: str) -> str:
        key = self._keys.get(key_id)
        if key is None:
            raise LocalFileArtifactError("browser_file_authority_key_unavailable")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("ascii")
        return hmac.new(key, b"browser-file-artifact-v1\x00" + encoded,
                        hashlib.sha256).hexdigest()

    def _seal(self, data: dict[str, object], kind: str) -> dict[str, object]:
        payload = {"schema_version": kind, "key_id": self._active_key_id, **data}
        payload["mac"] = self._mac(payload, self._active_key_id)
        return payload

    def _decode(self, payload: object, kind: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise LocalFileArtifactError("browser_file_metadata_invalid")
        unsigned = {key: value for key, value in payload.items() if key != "mac"}
        key_id, mac = payload.get("key_id"), payload.get("mac")
        if (payload.get("schema_version") != kind or type(key_id) is not str
                or type(mac) is not str
                or not hmac.compare_digest(mac, self._mac(unsigned, key_id))):
            raise LocalFileArtifactError("browser_file_metadata_invalid")
        return payload

    def _artifact(self, payload: object) -> StoredFileArtifact:
        data = self._decode(payload, _EVENT)
        try:
            owner = FileArtifactOwner(**data["owner"])
            return StoredFileArtifact(
                reference=data["reference"], owner=owner,
                media_type=data["media_type"], size_bytes=data["size_bytes"],
                sha256=bytes.fromhex(data["sha256"]),
                expires_at=datetime.fromisoformat(data["expires_at"]),
            )
        except (KeyError, TypeError, ValueError):
            raise LocalFileArtifactError("browser_file_metadata_invalid") from None

    async def _current_run_in_session(
        self, session: AsyncSession, owner: FileArtifactOwner,
    ) -> RunSnapshot:
        runs = self._runs
        if runs is None:
            raise LocalFileArtifactError("browser_file_authority_unavailable")
        run = await runs._read_run(
            session,
            BrowserOwner(tenant_id=owner.tenant_id, user_id=owner.ai_user_id,
                         session_id=owner.session_id), owner.task_id, owner.run_id,
        )
        await runs._authority.check_run(session, run, "read")
        if (run.admission.target_system != owner.target_system
                or run.admission.binding_id != owner.binding_id
                or run.admission.binding_revision != owner.binding_revision):
            raise LocalFileArtifactError("browser_file_access_denied")
        return run

    async def _current_run(self, owner: FileArtifactOwner) -> RunSnapshot:
        async with self._sessions() as session, session.begin():
            return await self._current_run_in_session(session, owner)

    async def check_create(self, owner: FileArtifactOwner) -> None:
        run = await self._current_run(owner)
        if (run.status != "running" or run.cancel_requested
                or run.verification is not None):
            raise LocalFileArtifactError("browser_file_access_denied")

    async def record(self, artifact: StoredFileArtifact) -> None:
        owner = artifact.owner
        payload = self._seal({
            "reference": artifact.reference, "owner": self._owner_data(owner),
            "media_type": artifact.media_type, "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256.hex(),
            "expires_at": artifact.expires_at.astimezone(UTC).isoformat(),
        }, _EVENT)
        async with self._sessions() as session, session.begin():
            run = await self._current_run_in_session(session, owner)
            if (run.status != "running" or run.cancel_requested
                    or run.verification is not None):
                raise LocalFileArtifactError("browser_file_access_denied")
            await append_owned_event(
                session, tenant_id=owner.tenant_id, ai_user_id=owner.ai_user_id,
                session_id=owner.session_id, task_id=owner.task_id,
                event=TaskEventRecord(
                    event_id=self._event_id(_EVENT, artifact.reference),
                    task_id=owner.task_id, event_type=_EVENT,
                    timestamp=datetime.now(UTC), payload=payload,
                ),
            )

    async def _stored(
        self, session: AsyncSession, reference: str, owner: FileArtifactOwner,
    ) -> StoredFileArtifact:
        row = (await session.execute(text(
            "SELECT e.event_type,e.payload FROM task_events e JOIN tasks t"
            " ON t.task_id=e.task_id WHERE e.task_id=:task AND e.event_id=:event"
            " AND t.tenant_id=:tenant AND t.ai_user_id=:user AND t.session_id=:session"
        ), {"task": owner.task_id, "event": self._event_id(_EVENT, reference),
            "tenant": owner.tenant_id, "user": owner.ai_user_id,
            "session": owner.session_id})).mappings().one_or_none()
        if row is None or row["event_type"] != _EVENT:
            raise LocalFileArtifactError("browser_file_access_denied")
        artifact = self._artifact(row["payload"])
        if artifact.reference != reference or artifact.owner != owner:
            raise LocalFileArtifactError("browser_file_access_denied")
        removed = (await session.execute(text(
            "SELECT payload FROM task_events WHERE task_id=:task AND event_id=:event"
        ), {"task": owner.task_id,
            "event": self._event_id(_REMOVED, reference)})).scalar_one_or_none()
        if removed is not None:
            self._decode(removed, _REMOVED)
            raise LocalFileArtifactError("browser_file_access_denied")
        return artifact

    async def authorize_read(
        self, reference: str, owner: FileArtifactOwner,
    ) -> StoredFileArtifact:
        async with self._sessions() as session, session.begin():
            await self._current_run_in_session(session, owner)
            artifact = await self._stored(session, reference, owner)
        if artifact.expires_at <= datetime.now(UTC):
            raise LocalFileArtifactError("browser_file_access_denied")
        return artifact

    async def list_authorized(
        self, owner: FileArtifactOwner, limit: int,
    ) -> tuple[StoredFileArtifact, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise LocalFileArtifactError("browser_file_request_invalid")
        async with self._sessions() as session, session.begin():
            await self._current_run_in_session(session, owner)
            rows = (await session.execute(text(
                "SELECT e.payload FROM task_events e JOIN tasks t ON t.task_id=e.task_id"
                " WHERE e.task_id=:task AND e.event_type=:kind"
                " AND t.tenant_id=:tenant AND t.ai_user_id=:user AND t.session_id=:session"
                " ORDER BY e.timestamp DESC LIMIT :limit"
            ), {"task": owner.task_id, "kind": _EVENT,
                "tenant": owner.tenant_id, "user": owner.ai_user_id,
                "session": owner.session_id, "limit": limit})).mappings().all()
            result: list[StoredFileArtifact] = []
            for row in rows:
                artifact = self._artifact(row["payload"])
                if artifact.owner != owner:
                    raise LocalFileArtifactError("browser_file_access_denied")
                if artifact.expires_at > datetime.now(UTC):
                    try:
                        result.append(await self._stored(session, artifact.reference, owner))
                    except LocalFileArtifactError as error:
                        if error.code != "browser_file_access_denied":
                            raise
            return tuple(result)

    async def expired(
        self, before: datetime, limit: int,
    ) -> tuple[StoredFileArtifact, ...]:
        if before.tzinfo is None or type(limit) is not int or not 1 <= limit <= 1000:
            raise LocalFileArtifactError("browser_file_cleanup_invalid")
        async with self._sessions() as session:
            rows = (await session.execute(text(
                "SELECT e.payload FROM task_events e WHERE e.event_type=:kind"
                " AND (e.payload->>'expires_at')::timestamptz<=:before"
                " AND NOT EXISTS (SELECT 1 FROM task_events removed"
                " WHERE removed.task_id=e.task_id AND removed.event_type=:removed"
                " AND removed.payload->>'reference'=e.payload->>'reference')"
                " ORDER BY e.timestamp ASC LIMIT :limit"
            ), {"kind": _EVENT, "removed": _REMOVED, "before": before,
                "limit": limit})).mappings().all()
        return tuple(artifact for row in rows
                     if (artifact := self._artifact(row["payload"])).expires_at <= before)

    async def confirm_removed(self, artifact: StoredFileArtifact) -> None:
        if artifact.expires_at > datetime.now(UTC):
            raise LocalFileArtifactError("browser_file_cleanup_invalid")
        owner = artifact.owner
        async with self._sessions() as session, session.begin():
            if await self._stored(session, artifact.reference, owner) != artifact:
                raise LocalFileArtifactError("browser_file_metadata_invalid")
            await append_owned_event(
                session, tenant_id=owner.tenant_id, ai_user_id=owner.ai_user_id,
                session_id=owner.session_id, task_id=owner.task_id,
                event=TaskEventRecord(
                    event_id=self._event_id(_REMOVED, artifact.reference),
                    task_id=owner.task_id, event_type=_REMOVED,
                    timestamp=datetime.now(UTC),
                    payload=self._seal({"reference": artifact.reference,
                                        "owner": self._owner_data(owner)}, _REMOVED),
                ),
            )
