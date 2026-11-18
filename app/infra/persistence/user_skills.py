"""PostgreSQL storage of personal saved read flows and their session working state.

Every query is scoped by the server-authenticated owner (and, for session
state, the bound session id), so another user's rows are indistinguishable
from missing ones. Deleted flows (``deleted_at``) are invisible to every read.
"""

from __future__ import annotations

import json
import logging
from time import monotonic
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ports.user_skills import (
    RecordEntry,
    SessionKey,
    TrialSnapshot,
    UserSkillError,
    UserSkillHead,
    UserSkillOwner,
    UserSkillPauseReason,
    UserSkillState,
    UserSkillVersion,
    UserSkillVersionPayload,
)

_LOGGER = logging.getLogger("uvicorn.error.user_skills")
_HEAD_COLUMNS = (
    "skill_id::text AS skill_id, display_name, state, pause_reason, current_version, updated_at"
)
_NAME_INDEX = "uq_user_skills_owner_name"
_LIVE = "deleted_at IS NULL"

MAX_RECORDS = 40
MAX_RECORD_BYTES = 256 * 1024
_IDLE = "interval '4 hours'"
_SNAPSHOT = "interval '600 seconds'"
_SWEEP_EVERY_SECONDS = 600.0
_SWEEP_BATCH = 200


def _owner(owner: UserSkillOwner) -> dict[str, str]:
    return {"tenant": owner.tenant_id, "owner": owner.ai_user_id}


def _key(key: SessionKey) -> dict[str, str]:
    return {"tenant": key[0], "owner": key[1], "session": key[2]}


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _version_row(row: Any) -> UserSkillVersion:
    return UserSkillVersion(
        skill_id=row["skill_id"],
        version=row["version"],
        display_name=row["display_name"],
        state=row["state"],
        pause_reason=row["pause_reason"],
        current_version=row["current_version"],
        updated_at=row["updated_at"],
        payload=UserSkillVersionPayload(
            definition=_json(row["definition"]),
            definition_digest=bytes(row["definition_digest"]),
            purpose=row["purpose"],
            business_rules=row["business_rules"],
        ),
    )


async def _insert_version(
    session: AsyncSession,
    owner: UserSkillOwner,
    skill_id: str,
    version: int,
    payload: UserSkillVersionPayload,
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.user_skill_versions (tenant_id, skill_id, version, definition, "
            "definition_digest, purpose, business_rules) VALUES (:tenant, CAST(:skill AS UUID), "
            ":version, CAST(:definition AS JSONB), :digest, :purpose, :rules)"
        ),
        {
            "tenant": owner.tenant_id,
            "skill": skill_id,
            "version": version,
            "definition": json.dumps(payload.definition, ensure_ascii=False),
            "digest": payload.definition_digest,
            "purpose": payload.purpose,
            "rules": payload.business_rules,
        },
    )


async def _create(
    session: AsyncSession,
    owner: UserSkillOwner,
    display_name: str,
    payload: UserSkillVersionPayload,
) -> UserSkillHead:
    skill_id = str(uuid4())
    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO public.user_skills (tenant_id, skill_id, owner_user_id, "
                    "display_name, state, current_version) VALUES (:tenant, CAST(:skill AS UUID), "
                    ":owner, :name, 'usable', 1) RETURNING " + _HEAD_COLUMNS
                ),
                {**_owner(owner), "skill": skill_id, "name": display_name},
            )
        )
        .mappings()
        .one()
    )
    await _insert_version(session, owner, skill_id, 1, payload)
    return UserSkillHead(**dict(row))


async def _add_version(
    session: AsyncSession,
    owner: UserSkillOwner,
    skill_id: str,
    expected_current_version: int,
    payload: UserSkillVersionPayload,
) -> UserSkillHead:
    head = (
        (
            await session.execute(
                text(
                    "SELECT current_version FROM public.user_skills WHERE tenant_id=:tenant AND "
                    "owner_user_id=:owner AND skill_id=CAST(:skill AS UUID) AND "
                    + _LIVE
                    + " FOR UPDATE"
                ),
                {**_owner(owner), "skill": skill_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if head is None:
        raise UserSkillError("not_found")
    if head["current_version"] != expected_current_version:
        raise UserSkillError("conflict")
    version = expected_current_version + 1
    await _insert_version(session, owner, skill_id, version, payload)
    # A new version is the repair of a paused flow; disabled stays disabled.
    row = (
        (
            await session.execute(
                text(
                    "UPDATE public.user_skills SET current_version=:version, updated_at=now(), "
                    "state=CASE WHEN state='paused' THEN 'usable' ELSE state END, "
                    "pause_reason=NULL WHERE tenant_id=:tenant AND owner_user_id=:owner AND "
                    "skill_id=CAST(:skill AS UUID) AND " + _LIVE + " RETURNING " + _HEAD_COLUMNS
                ),
                {**_owner(owner), "skill": skill_id, "version": version},
            )
        )
        .mappings()
        .one()
    )
    return UserSkillHead(**dict(row))


def _name_taken(exc: IntegrityError) -> bool:
    return _NAME_INDEX in str(exc.orig)


class PostgreSQLUserSkillStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(
        self, owner: UserSkillOwner, display_name: str, payload: UserSkillVersionPayload
    ) -> UserSkillHead:
        try:
            async with self._sessions() as session, session.begin():
                return await _create(session, owner, display_name, payload)
        except IntegrityError as exc:
            raise UserSkillError("name_taken" if _name_taken(exc) else "conflict") from None
        except SQLAlchemyError as exc:
            raise UserSkillError(type(exc).__name__) from None

    async def add_version(
        self,
        owner: UserSkillOwner,
        skill_id: str,
        expected_current_version: int,
        payload: UserSkillVersionPayload,
    ) -> UserSkillHead:
        try:
            async with self._sessions() as session, session.begin():
                return await _add_version(
                    session, owner, skill_id, expected_current_version, payload
                )
        except SQLAlchemyError as exc:
            raise UserSkillError(type(exc).__name__) from None

    async def save_trial(
        self, key: SessionKey, trial_id: str, payload: UserSkillVersionPayload,
        *, refused: bool = False,
    ) -> dict[str, Any]:
        """Publish a ready trial and mark it saved atomically; a saved trial replays.

        Lock order: session head, then the trial, then the target flow head.
        Name, target and definition come only from the immutable trial row.
        """

        owner = UserSkillOwner(tenant_id=key[0], ai_user_id=key[1])
        params = _key(key)
        try:
            async with self._sessions() as session, session.begin():
                head = (
                    (
                        await session.execute(
                            text(
                                "SELECT expires_at > now() AS live FROM public.user_skill_sessions "
                                "WHERE tenant_id=:tenant AND owner_user_id=:owner AND "
                                "session_id=:session FOR UPDATE"
                            ),
                            params,
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if head is None or not head["live"]:
                    raise UserSkillError("trial_expired")
                trial = (
                    (
                        await session.execute(
                            text(
                                "SELECT payload, trial_state, saved_result, "
                                "(expires_at IS NULL OR expires_at > now()) AS live "
                                "FROM public.user_skill_session_items WHERE tenant_id=:tenant AND "
                                "owner_user_id=:owner AND session_id=:session AND kind='trial' AND "
                                "item_id=:trial FOR UPDATE"
                            ),
                            {**params, "trial": trial_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if trial is None:
                    raise UserSkillError("trial_expired")
                if trial["trial_state"] == "saved":
                    return dict(_json(trial["saved_result"]))
                if not trial["live"]:
                    raise UserSkillError("trial_expired")
                if refused:
                    raise UserSkillError("not_admissible")
                content = _json(trial["payload"])
                if not content["savable"]:
                    raise UserSkillError("not_savable")
                target = content["target"]
                if target["mode"] == "new":
                    saved = await _create(session, owner, content["name"], payload)
                else:
                    saved = await _add_version(
                        session,
                        owner,
                        target["skill_id"],
                        target["expected_current_version"],
                        payload,
                    )
                result = {
                    "skill_id": saved.skill_id,
                    "version": saved.current_version,
                    "state": saved.state,
                }
                await session.execute(
                    text(
                        "UPDATE public.user_skill_session_items SET trial_state='saved', "
                        "saved_result=CAST(:result AS JSONB), expires_at=NULL "
                        "WHERE tenant_id=:tenant AND owner_user_id=:owner AND "
                        "session_id=:session AND kind='trial' AND item_id=:trial"
                    ),
                    {**params, "trial": trial_id, "result": json.dumps(result)},
                )
                return result
        except IntegrityError as exc:
            raise UserSkillError("name_taken" if _name_taken(exc) else "conflict") from None
        except SQLAlchemyError as exc:
            raise UserSkillError(type(exc).__name__) from None

    async def list_for_owner(self, owner: UserSkillOwner) -> list[UserSkillHead]:
        try:
            async with self._sessions() as session:
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT " + _HEAD_COLUMNS + " FROM public.user_skills WHERE "
                                "tenant_id=:tenant AND owner_user_id=:owner AND "
                                + _LIVE
                                + " ORDER BY lower(display_name), skill_id"
                            ),
                            _owner(owner),
                        )
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as exc:
            raise UserSkillError(type(exc).__name__) from None
        return [UserSkillHead(**dict(row)) for row in rows]

    async def _current(
        self, owner: UserSkillOwner, where: str, params: dict[str, Any]
    ) -> list[UserSkillVersion]:
        try:
            async with self._sessions() as session:
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT h.skill_id::text AS skill_id, h.display_name, h.state, "
                                "h.pause_reason, h.current_version, h.updated_at, v.version, "
                                "v.definition, v.definition_digest, v.purpose, v.business_rules "
                                "FROM public.user_skills h JOIN public.user_skill_versions v ON "
                                "v.tenant_id=h.tenant_id AND v.skill_id=h.skill_id AND "
                                "v.version=h.current_version WHERE h.tenant_id=:tenant AND "
                                "h.owner_user_id=:owner AND h.deleted_at IS NULL AND "
                                + where
                                + " ORDER BY lower(h.display_name), h.skill_id"
                            ),
                            {**_owner(owner), **params},
                        )
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as exc:
            raise UserSkillError(type(exc).__name__) from None
        return [_version_row(row) for row in rows]

    async def get_current(self, owner: UserSkillOwner, skill_id: str) -> UserSkillVersion | None:
        rows = await self._current(owner, "h.skill_id=CAST(:skill AS UUID)", {"skill": skill_id})
        return rows[0] if rows else None

    async def list_current(
        self, owner: UserSkillOwner, states: frozenset[UserSkillState]
    ) -> list[UserSkillVersion]:
        return await self._current(owner, "h.state = ANY(:states)", {"states": sorted(states)})

    async def set_state(
        self,
        owner: UserSkillOwner,
        skill_id: str,
        *,
        from_states: frozenset[UserSkillState],
        to_state: UserSkillState,
        pause_reason: UserSkillPauseReason | None,
        expected_current_version: int,
    ) -> UserSkillHead | None:
        try:
            async with self._sessions() as session, session.begin():
                row = (
                    (
                        await session.execute(
                            text(
                                "UPDATE public.user_skills SET state=:to_state, "
                                "pause_reason=:reason, updated_at=now() WHERE tenant_id=:tenant "
                                "AND owner_user_id=:owner AND skill_id=CAST(:skill AS UUID) AND "
                                "state = ANY(:from_states) AND current_version=:expected AND "
                                + _LIVE
                                + " RETURNING "
                                + _HEAD_COLUMNS
                            ),
                            {
                                **_owner(owner),
                                "skill": skill_id,
                                "to_state": to_state,
                                "reason": pause_reason,
                                "from_states": sorted(from_states),
                                "expected": expected_current_version,
                            },
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as exc:
            raise UserSkillError(type(exc).__name__) from None
        return None if row is None else UserSkillHead(**dict(row))

    async def rename(
        self, owner: UserSkillOwner, skill_id: str, display_name: str
    ) -> UserSkillHead | None:
        try:
            async with self._sessions() as session, session.begin():
                row = (
                    (
                        await session.execute(
                            text(
                                "UPDATE public.user_skills SET display_name=:name, "
                                "updated_at=now() "
                                "WHERE tenant_id=:tenant AND owner_user_id=:owner AND "
                                "skill_id=CAST(:skill AS UUID) AND "
                                + _LIVE
                                + " RETURNING "
                                + _HEAD_COLUMNS
                            ),
                            {**_owner(owner), "skill": skill_id, "name": display_name},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except IntegrityError as exc:
            raise UserSkillError("name_taken" if _name_taken(exc) else "conflict") from None
        except SQLAlchemyError as exc:
            raise UserSkillError(type(exc).__name__) from None
        return None if row is None else UserSkillHead(**dict(row))

    async def delete(self, owner: UserSkillOwner, skill_id: str) -> bool:
        try:
            async with self._sessions() as session, session.begin():
                row = (
                    await session.execute(
                        text(
                            "UPDATE public.user_skills SET deleted_at=now(), updated_at=now() "
                            "WHERE tenant_id=:tenant AND owner_user_id=:owner AND "
                            "skill_id=CAST(:skill AS UUID) AND " + _LIVE + " RETURNING 1"
                        ),
                        {**_owner(owner), "skill": skill_id},
                    )
                ).one_or_none()
        except SQLAlchemyError as exc:
            raise UserSkillError(type(exc).__name__) from None
        return row is not None


class PostgreSQLUserSkillSessionStore:
    """Session working state: idle 4 h (use extends it), snapshots 600 s, by DB time.

    Expired rows are unreadable at once and physically removed by later writes:
    the session's own expired items on each write to it, other expired session
    heads (cascading) in small batches at most every ten minutes per process.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Any = monotonic,
    ) -> None:
        self._sessions = session_factory
        self._clock = clock
        self._last_sweep: float | None = None

    # ----------------------------------------------------------- helpers
    async def _drop_expired_head(self, session: AsyncSession, key: SessionKey) -> None:
        await session.execute(
            text(
                "DELETE FROM public.user_skill_sessions WHERE tenant_id=:tenant AND "
                "owner_user_id=:owner AND session_id=:session AND expires_at <= now()"
            ),
            _key(key),
        )

    async def _open(self, session: AsyncSession, key: SessionKey) -> None:
        """Create or extend the head for a non-record write; drop expired items."""

        await self._drop_expired_head(session, key)
        await session.execute(
            text(
                "INSERT INTO public.user_skill_sessions AS h (tenant_id, owner_user_id, "
                "session_id, expires_at) VALUES (:tenant, :owner, :session, now() + "
                + _IDLE
                + ") ON CONFLICT (tenant_id, owner_user_id, session_id) DO UPDATE SET "
                "expires_at = now() + " + _IDLE
            ),
            _key(key),
        )
        await self._drop_expired_items(session, key)

    async def _drop_expired_items(self, session: AsyncSession, key: SessionKey) -> None:
        await session.execute(
            text(
                "DELETE FROM public.user_skill_session_items WHERE tenant_id=:tenant AND "
                "owner_user_id=:owner AND session_id=:session AND expires_at <= now()"
            ),
            _key(key),
        )

    async def _touch(self, session: AsyncSession, key: SessionKey) -> int | None:
        """Extend a live head; its ``dropped`` count, or None when absent or expired."""

        row = (
            await session.execute(
                text(
                    "UPDATE public.user_skill_sessions SET expires_at = now() + "
                    + _IDLE
                    + " WHERE tenant_id=:tenant AND owner_user_id=:owner AND "
                    "session_id=:session AND expires_at > now() RETURNING dropped"
                ),
                _key(key),
            )
        ).one_or_none()
        return None if row is None else int(row[0])

    async def _sweep(self, session: AsyncSession) -> None:
        now = self._clock()
        if self._last_sweep is not None and now - self._last_sweep < _SWEEP_EVERY_SECONDS:
            return
        self._last_sweep = now
        await session.execute(
            text(
                "DELETE FROM public.user_skill_sessions WHERE ctid IN (SELECT ctid FROM "
                "public.user_skill_sessions WHERE expires_at <= now() ORDER BY expires_at "
                "LIMIT :limit FOR UPDATE SKIP LOCKED) AND expires_at <= now()"
            ),
            {"limit": _SWEEP_BATCH},
        )

    async def _put(
        self,
        key: SessionKey,
        kind: str,
        item_id: str,
        payload: dict[str, Any],
        *,
        ttl: bool,
        trial_state: str | None = None,
    ) -> None:
        try:
            async with self._sessions() as session, session.begin():
                await self._open(session, key)
                await session.execute(
                    text(
                        "INSERT INTO public.user_skill_session_items (tenant_id, owner_user_id, "
                        "session_id, kind, item_id, payload, trial_state, expires_at) VALUES "
                        "(:tenant, :owner, :session, :kind, :item, CAST(:payload AS JSONB), "
                        ":state, " + ("now() + " + _SNAPSHOT if ttl else "NULL") + ") "
                        "ON CONFLICT (tenant_id, owner_user_id, session_id, kind, item_id) "
                        "DO UPDATE SET payload = EXCLUDED.payload, "
                        "expires_at = EXCLUDED.expires_at"
                    ),
                    {
                        **_key(key),
                        "kind": kind,
                        "item": item_id,
                        "state": trial_state,
                        "payload": json.dumps(payload, ensure_ascii=False),
                    },
                )
                await self._sweep(session)
        except SQLAlchemyError as exc:
            _LOGGER.info("user_skill.session_store op=put_%s error=%s", kind, type(exc).__name__)
            raise UserSkillError(type(exc).__name__) from None

    async def _get(self, key: SessionKey, kind: str, item_id: str) -> Any | None:
        try:
            async with self._sessions() as session, session.begin():
                if await self._touch(session, key) is None:
                    return None
                return (
                    (
                        await session.execute(
                            text(
                                "SELECT payload, trial_state, saved_result FROM "
                                "public.user_skill_session_items WHERE tenant_id=:tenant AND "
                                "owner_user_id=:owner AND session_id=:session AND kind=:kind AND "
                                "item_id=:item AND (expires_at IS NULL OR expires_at > now())"
                            ),
                            {**_key(key), "kind": kind, "item": item_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as exc:
            _LOGGER.info("user_skill.session_store op=get_%s error=%s", kind, type(exc).__name__)
            raise UserSkillError(type(exc).__name__) from None

    # ---------------------------------------------------------- records
    async def record(
        self,
        key: SessionKey,
        *,
        turn_id: str,
        capability_id: str,
        capability_fingerprint: str,
        arguments: dict[str, Any],
        data: dict[str, Any],
    ) -> bool:
        body = json.dumps(
            {
                "turn_id": turn_id,
                "capability_id": capability_id,
                "capability_fingerprint": capability_fingerprint,
                "arguments": arguments,
                "data": data,
            },
            ensure_ascii=False,
        )
        oversized = len(body.encode("utf-8")) > MAX_RECORD_BYTES
        try:
            async with self._sessions() as session, session.begin():
                await self._drop_expired_head(session, key)
                if oversized:
                    await session.execute(
                        text(
                            "INSERT INTO public.user_skill_sessions AS h (tenant_id, "
                            "owner_user_id, session_id, dropped, expires_at) VALUES (:tenant, "
                            ":owner, :session, 1, now() + " + _IDLE + ") ON CONFLICT "
                            "(tenant_id, owner_user_id, session_id) DO UPDATE SET "
                            "dropped = h.dropped + 1, expires_at = now() + " + _IDLE
                        ),
                        _key(key),
                    )
                    return False
                seq = (
                    await session.execute(
                        text(
                            "INSERT INTO public.user_skill_sessions AS h (tenant_id, "
                            "owner_user_id, session_id, next_seq, expires_at) VALUES (:tenant, "
                            ":owner, :session, 2, now() + " + _IDLE + ") ON CONFLICT "
                            "(tenant_id, owner_user_id, session_id) DO UPDATE SET "
                            "next_seq = h.next_seq + 1, expires_at = now() + "
                            + _IDLE
                            + " RETURNING h.next_seq - 1"
                        ),
                        _key(key),
                    )
                ).scalar_one()
                await session.execute(
                    text(
                        "INSERT INTO public.user_skill_session_items (tenant_id, owner_user_id, "
                        "session_id, kind, item_id, seq, payload) VALUES (:tenant, :owner, "
                        ":session, 'record', :item, :seq, CAST(:payload AS JSONB))"
                    ),
                    {**_key(key), "item": str(seq), "seq": seq, "payload": body},
                )
                await session.execute(
                    text(
                        "WITH gone AS (DELETE FROM public.user_skill_session_items WHERE "
                        "tenant_id=:tenant AND owner_user_id=:owner AND session_id=:session AND "
                        "kind='record' AND seq <= :oldest RETURNING 1) "
                        "UPDATE public.user_skill_sessions SET dropped = dropped + "
                        "(SELECT count(*) FROM gone) WHERE tenant_id=:tenant AND "
                        "owner_user_id=:owner AND session_id=:session"
                    ),
                    {**_key(key), "oldest": seq - MAX_RECORDS},
                )
                await self._drop_expired_items(session, key)
                await self._sweep(session)
        except SQLAlchemyError as exc:
            _LOGGER.info("user_skill.session_store op=record error=%s", type(exc).__name__)
            raise UserSkillError(type(exc).__name__) from None
        return True

    async def records(self, key: SessionKey) -> tuple[tuple[RecordEntry, ...], int]:
        try:
            async with self._sessions() as session, session.begin():
                dropped = await self._touch(session, key)
                if dropped is None:
                    return (), 0
                rows = (
                    await session.execute(
                        text(
                            "SELECT seq, payload FROM public.user_skill_session_items WHERE "
                            "tenant_id=:tenant AND owner_user_id=:owner AND "
                            "session_id=:session AND kind='record' ORDER BY seq"
                        ),
                        _key(key),
                    )
                ).all()
        except SQLAlchemyError as exc:
            _LOGGER.info("user_skill.session_store op=records error=%s", type(exc).__name__)
            raise UserSkillError(type(exc).__name__) from None
        entries = []
        for seq, payload in rows:
            body = _json(payload)
            entries.append(
                RecordEntry(
                    int(seq),
                    body["turn_id"],
                    body["capability_id"],
                    body["capability_fingerprint"],
                    body["arguments"],
                    body["data"],
                )
            )
        return tuple(entries), dropped

    async def has_records(self, key: SessionKey) -> bool:
        try:
            async with self._sessions() as session:
                return bool(
                    (
                        await session.execute(
                            text(
                                "SELECT EXISTS(SELECT 1 FROM public.user_skill_session_items i "
                                "JOIN public.user_skill_sessions s ON s.tenant_id=i.tenant_id "
                                "AND s.owner_user_id=i.owner_user_id AND "
                                "s.session_id=i.session_id WHERE i.tenant_id=:tenant AND "
                                "i.owner_user_id=:owner AND i.session_id=:session AND "
                                "i.kind='record' AND s.expires_at > now())"
                            ),
                            _key(key),
                        )
                    ).scalar_one()
                )
        except SQLAlchemyError as exc:
            _LOGGER.info("user_skill.session_store op=has_records error=%s", type(exc).__name__)
            raise UserSkillError(type(exc).__name__) from None

    # -------------------------------------------------------- snapshots
    async def put_proposal(
        self, key: SessionKey, proposal_id: str, payload: dict[str, Any]
    ) -> None:
        await self._put(key, "proposal", proposal_id, payload, ttl=True)

    async def proposal(self, key: SessionKey, proposal_id: str) -> dict[str, Any] | None:
        row = await self._get(key, "proposal", proposal_id)
        return None if row is None else dict(_json(row["payload"]))

    async def put_trial(self, key: SessionKey, trial: TrialSnapshot) -> None:
        await self._put(
            key,
            "trial",
            trial.trial_id,
            {**trial.payload, "proposal_id": trial.proposal_id, "savable": trial.savable},
            ttl=True,
            trial_state="ready",
        )

    async def trial(self, key: SessionKey, trial_id: str) -> TrialSnapshot | None:
        row = await self._get(key, "trial", trial_id)
        if row is None:
            return None
        content = dict(_json(row["payload"]))
        proposal_id = content.pop("proposal_id")
        savable = bool(content.pop("savable"))
        saved = None if row["saved_result"] is None else dict(_json(row["saved_result"]))
        return TrialSnapshot(trial_id, proposal_id, content, savable, saved)

    async def put_run(self, key: SessionKey, run_id: str, snapshot: dict[str, Any]) -> None:
        await self._put(key, "run", run_id, snapshot, ttl=True)

    async def run(self, key: SessionKey, run_id: str) -> dict[str, Any] | None:
        row = await self._get(key, "run", run_id)
        return None if row is None else dict(_json(row["payload"]))

    async def set_takeover(self, key: SessionKey, context: dict[str, Any]) -> None:
        await self._put(key, "takeover", "pending", context, ttl=False)

    async def take_takeover(self, key: SessionKey) -> dict[str, Any] | None:
        """Atomically take the pending slot once; never creates or extends the head."""

        try:
            async with self._sessions() as session, session.begin():
                row = (
                    await session.execute(
                        text(
                            "DELETE FROM public.user_skill_session_items i USING "
                            "public.user_skill_sessions s WHERE s.tenant_id=i.tenant_id AND "
                            "s.owner_user_id=i.owner_user_id AND s.session_id=i.session_id AND "
                            "i.tenant_id=:tenant AND i.owner_user_id=:owner AND "
                            "i.session_id=:session AND i.kind='takeover' AND "
                            "i.item_id='pending' AND s.expires_at > now() RETURNING i.payload"
                        ),
                        _key(key),
                    )
                ).one_or_none()
        except SQLAlchemyError as exc:
            _LOGGER.info("user_skill.session_store op=take_takeover error=%s", type(exc).__name__)
            raise UserSkillError(type(exc).__name__) from None
        return None if row is None else dict(_json(row[0]))


__all__ = ("PostgreSQLUserSkillSessionStore", "PostgreSQLUserSkillStore")
