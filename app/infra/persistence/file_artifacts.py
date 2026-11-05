"""PostgreSQL authority and publication protocol for file artifacts (FOUNDATION_PLAN §2).

The artifact row lock is the serialization point between producers and the cleanup job:
a directory entry is only added (``O_EXCL`` create, publishing rename) while holding the
lock and owning a live lease, so a reclaimed reservation can never grow a new file. Job
outputs additionally hold the job row ``FOR SHARE`` and re-check the claim while
publishing; the lock order is always background_jobs -> file_artifacts.

The SQL is driver independent (API asyncpg, worker psycopg): only scalars are bound, and
time comes from the database. Timestamps are signed as fixed-format UTC because the two
drivers may decode ``timestamptz`` into different time zones.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.file_artifacts import FileArtifactService
from app.infra.files.local_store import (
    LocalFileStorage,
    create_exclusive,
    durable_replace,
    open_storage,
    open_verified,
    remove_regular,
    sync_and_close,
    write_all,
)
from app.ports.file_artifacts import (
    FileArtifactCleanupResult,
    FileArtifactDownload,
    FileArtifactError,
    FileArtifactOwner,
    JobOutputOwner,
    OutputWriter,
)

if TYPE_CHECKING:
    from app.config import FileArtifactSettings

_LOGGER = logging.getLogger("uvicorn.error.file_artifacts")
_T = TypeVar("_T")
_MAC_DOMAIN = b"eternalai-file-artifact-v1\x00"
_KEY_ID = "v1"
_LEASE = timedelta(seconds=120)
_RENEW_INTERVAL_SECONDS = 30.0
_UNSAFE_FILENAME_CHARACTERS = frozenset('/\\":*?<>|')
_DEFAULT_FILENAME = "file"
_MAX_FILENAME_CHARACTERS = 255
_RESERVATION_LOST = "file_artifact_reservation_lost"
_PRODUCTION_FAILED = "production_failed"
BATCH_SIZE = 100
STANDARD_RETENTION_SECONDS = 1209600
_COLUMNS = (
    "artifact_id, tenant_id, owner_ai_user_id, session_id, purpose, source_job_id, "
    "source_artifact_id, display_filename, media_type, size_bytes, sha256, "
    "inspection::text AS inspection, storage_scope, storage_reference, state, sealed_at, "
    "lifecycle_started_at, expires_at, lease_expires_at, mac_key_id, mac, created_at"
)
_RENEW_LEASE = (
    "UPDATE public.file_artifacts SET lease_expires_at = "
    "LEAST(now() + interval '120 seconds', created_at + interval '10 minutes') "
    "WHERE artifact_id = :artifact_id AND state = 'pending' AND lease_expires_at > now() "
    "RETURNING artifact_id"
)
_RECORD_REMOVAL_FAILURE = (
    "UPDATE public.file_artifacts SET removal_attempts = removal_attempts + 1, "
    "last_removal_error = :error WHERE artifact_id = :artifact_id"
)


def sanitize_display_filename(filename: str) -> str:
    """Drop control, format and path characters; the result is only ever a display name."""

    cleaned = "".join(
        character
        for character in filename
        if character not in _UNSAFE_FILENAME_CHARACTERS
        and unicodedata.category(character)[0] != "C"
    )
    return cleaned.strip(" .")[:_MAX_FILENAME_CHARACTERS].strip(" .") or _DEFAULT_FILENAME


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("file_artifact_timestamp_naive")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _uuid(value: UUID | None) -> str | None:
    return None if value is None else str(value).lower()


async def _in_thread(
    function: Callable[..., _T], *arguments: Any, discard: Callable[[_T], None] | None = None
) -> _T:
    """Run blocking file work to completion even when the caller is cancelled.

    The caller closes descriptors the work may still be using, so a cancelled caller
    waits for the thread first; ``discard`` releases a result nobody will receive.
    """

    work = asyncio.ensure_future(asyncio.to_thread(function, *arguments))
    cancelled = False
    while not work.done():
        try:
            await asyncio.wait({work})
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        if work.exception() is None and discard is not None:
            discard(work.result())
        raise asyncio.CancelledError
    return work.result()


def _removal_error(paths: tuple[Path, ...]) -> str | None:
    """Remove the files in order; returns the failure class, or None when all are gone."""

    for path in paths:
        try:
            remove_regular(path)
        except FileArtifactError as error:
            return error.code
        except OSError as error:
            return "file_in_use" if getattr(error, "winerror", None) == 32 else "unlink_failed"
    return None


@dataclass(frozen=True)
class _ReservationRequest:
    tenant_id: str
    owner_ai_user_id: str
    session_id: str | None
    purpose: str
    display_filename: str
    media_type: str
    source_job_id: UUID | None = None
    source_artifact_id: UUID | None = None
    upload_idempotency_key: UUID | None = None
    request_fingerprint: bytes | None = None


@dataclass(frozen=True)
class _Reservation:
    artifact_id: UUID
    job: JobOutputOwner | None


@dataclass
class _CleanupCounters:
    scanned: int = 0
    removed: int = 0
    failed: int = 0
    abandoned: int = 0


class PostgreSQLFileArtifactStore:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        storage: LocalFileStorage,
        mac_key: bytes,
        retention_seconds: int,
    ) -> None:
        self._sessions = sessions
        self._storage = storage
        self._mac_key = mac_key
        self._retention = timedelta(seconds=retention_seconds)

    # ---- HMAC -----------------------------------------------------------------

    def _mac(self, fields: Mapping[Any, Any], *, sealed: bool) -> str:
        payload: dict[str, object] = {
            "schema_version": "sealed-v1" if sealed else "reservation-v1",
            "key_id": fields["mac_key_id"],
            "artifact_id": _uuid(fields["artifact_id"]),
            "tenant_id": fields["tenant_id"],
            "owner_ai_user_id": fields["owner_ai_user_id"],
            "session_id": fields["session_id"],
            "purpose": fields["purpose"],
            "storage_scope": fields["storage_scope"],
            "storage_reference": fields["storage_reference"],
            "source_job_id": _uuid(fields["source_job_id"]),
            "source_artifact_id": _uuid(fields["source_artifact_id"]),
            "created_at": _timestamp(fields["created_at"]),
        }
        if sealed:
            inspection = fields["inspection"]
            payload.update(
                display_filename=fields["display_filename"],
                media_type=fields["media_type"],
                size_bytes=fields["size_bytes"],
                sha256=bytes(fields["sha256"]).hex(),
                inspection=(
                    None
                    if inspection is None
                    else json.dumps(
                        json.loads(inspection),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    )
                ),
                sealed_at=_timestamp(fields["sealed_at"]),
                lifecycle_started_at=_timestamp(fields["lifecycle_started_at"]),
                expires_at=_timestamp(fields["expires_at"]),
            )
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
        return hmac.new(self._mac_key, _MAC_DOMAIN + encoded, hashlib.sha256).hexdigest()

    def _mac_valid(self, row: RowMapping) -> bool:
        """A row is signed by its reservation until sealed; state changes are not signed."""

        try:
            expected = self._mac(row, sealed=row["sealed_at"] is not None)
        except (ValueError, TypeError, OverflowError):
            return False
        return row["mac_key_id"] == _KEY_ID and hmac.compare_digest(expected, row["mac"])

    # ---- Download -------------------------------------------------------------

    async def open_download(
        self, owner: FileArtifactOwner, artifact_id: UUID
    ) -> FileArtifactDownload:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        f"SELECT {_COLUMNS}, now() AS db_now FROM public.file_artifacts "
                        "WHERE artifact_id = :artifact_id"
                    ),
                    {"artifact_id": artifact_id},
                )
            ).mappings().one_or_none()
        if (
            row is None
            or not self._mac_valid(row)
            or row["tenant_id"] != owner.tenant_id
            or row["owner_ai_user_id"] != owner.ai_user_id
            or row["storage_scope"] != self._storage.scope
        ):
            _LOGGER.info("file_artifact_download artifact=%s result=not_found", artifact_id)
            raise FileArtifactError("file_artifact_not_found")
        if row["state"] != "available" or row["db_now"] >= row["expires_at"]:
            _LOGGER.info("file_artifact_download artifact=%s result=expired", artifact_id)
            raise FileArtifactError("file_artifact_expired")
        path = self._storage.object_path(row["storage_reference"])
        try:
            fd, metadata = await _in_thread(
                open_verified,
                path,
                row["size_bytes"],
                bytes(row["sha256"]),
                discard=lambda opened: os.close(opened[0]),
            )
        except (OSError, FileArtifactError):
            _LOGGER.warning(
                "file_artifact_download artifact=%s result=file_unavailable", artifact_id
            )
            raise FileArtifactError("file_artifact_expired") from None
        _LOGGER.info("file_artifact_download artifact=%s result=ok", artifact_id)
        return FileArtifactDownload(
            artifact_id=artifact_id,
            path=path,
            pin_fd=fd,
            stat_result=metadata,
            display_filename=row["display_filename"],
            media_type=row["media_type"],
        )

    # ---- Reservation and ownership --------------------------------------------

    async def _reserve(self, session: AsyncSession, request: _ReservationRequest) -> UUID:
        db_now: datetime = (await session.execute(text("SELECT now()"))).scalar_one()
        fields: dict[str, Any] = {
            "artifact_id": uuid4(),
            "tenant_id": request.tenant_id,
            "owner_ai_user_id": request.owner_ai_user_id,
            "session_id": request.session_id,
            "purpose": request.purpose,
            "source_job_id": request.source_job_id,
            "source_artifact_id": request.source_artifact_id,
            "display_filename": request.display_filename,
            "media_type": request.media_type,
            "storage_scope": self._storage.scope,
            "storage_reference": secrets.token_urlsafe(32),
            "lease_expires_at": db_now + _LEASE,
            "upload_idempotency_key": request.upload_idempotency_key,
            "request_fingerprint": request.request_fingerprint,
            "mac_key_id": _KEY_ID,
            "created_at": db_now,
        }
        fields["mac"] = self._mac(fields, sealed=False)
        await session.execute(
            text(
                "INSERT INTO public.file_artifacts (artifact_id, tenant_id, owner_ai_user_id, "
                "session_id, purpose, source_job_id, source_artifact_id, display_filename, "
                "media_type, storage_scope, storage_reference, state, lease_expires_at, "
                "upload_idempotency_key, request_fingerprint, mac_key_id, mac, created_at) "
                "VALUES (:artifact_id, :tenant_id, :owner_ai_user_id, :session_id, :purpose, "
                ":source_job_id, :source_artifact_id, :display_filename, :media_type, "
                ":storage_scope, :storage_reference, 'pending', :lease_expires_at, "
                ":upload_idempotency_key, :request_fingerprint, :mac_key_id, :mac, :created_at)"
            ),
            fields,
        )
        artifact_id: UUID = fields["artifact_id"]
        return artifact_id

    async def _lock_owned(
        self, session: AsyncSession, artifact_id: UUID
    ) -> tuple[RowMapping, datetime] | None:
        """Lock the row; returns it with the transaction time only while the lease is live.

        ``clock_timestamp()`` is read after the lock: ``now()`` is frozen at transaction
        start and would still see the lease as live after a long lock wait.
        """

        row = (
            await session.execute(
                text(
                    f"SELECT {_COLUMNS} FROM public.file_artifacts "
                    "WHERE artifact_id = :artifact_id FOR UPDATE"
                ),
                {"artifact_id": artifact_id},
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        clock = (
            await session.execute(text("SELECT now() AS db_now, clock_timestamp() AS held_at"))
        ).mappings().one()
        if (
            row["state"] != "pending"
            or row["storage_scope"] != self._storage.scope
            or row["lease_expires_at"] <= clock["held_at"]
            or not self._mac_valid(row)
        ):
            return None
        return row, clock["db_now"]

    async def _job_row_live(self, session: AsyncSession, job: JobOutputOwner) -> RowMapping | None:
        """Share-lock the job row while this execution still holds the claim."""

        return (
            await session.execute(
                text(
                    "SELECT tenant_id, requested_by_ai_user_id, session_id "
                    "FROM public.background_jobs WHERE job_id = :job_id "
                    "AND claim_token = :claim_token "
                    "AND status IN ('running','cancel_requested') FOR SHARE"
                ),
                {"job_id": job.job_id, "claim_token": job.claim_token},
            )
        ).mappings().one_or_none()

    async def _lock_for_publication(
        self, session: AsyncSession, reservation: _Reservation
    ) -> tuple[RowMapping, datetime] | None:
        job = reservation.job
        if job is not None and await self._job_row_live(session, job) is None:
            return None
        return await self._lock_owned(session, reservation.artifact_id)

    async def _renew(self, artifact_id: UUID) -> bool:
        async with self._sessions() as session, session.begin():
            renewed = await session.execute(text(_RENEW_LEASE), {"artifact_id": artifact_id})
            return renewed.first() is not None

    def _reservation_paths(self, reference: str) -> tuple[Path, Path]:
        return self._storage.staging_path(reference), self._storage.object_path(reference)

    # ---- Publication ----------------------------------------------------------

    async def publish_output(
        self, owner: JobOutputOwner, writer: OutputWriter, filename: str, media_type: str
    ) -> UUID:
        reservation = await self._reserve_output(owner, filename, media_type)
        try:
            size_bytes, digest = await self._produce_part(reservation, writer)
        except Exception as error:
            if not isinstance(error, FileArtifactError) or error.code != _RESERVATION_LOST:
                await self._fail_reservation(reservation, _PRODUCTION_FAILED)
            raise
        await self._seal(reservation, size_bytes, digest, inspection=None)
        return reservation.artifact_id

    async def _reserve_output(
        self, owner: JobOutputOwner, filename: str, media_type: str
    ) -> _Reservation:
        async with self._sessions() as session, session.begin():
            job = await self._job_row_live(session, owner)
            if job is None:
                raise FileArtifactError(_RESERVATION_LOST)
            artifact_id = await self._reserve(
                session,
                _ReservationRequest(
                    tenant_id=job["tenant_id"],
                    owner_ai_user_id=job["requested_by_ai_user_id"],
                    session_id=job["session_id"],
                    purpose="job_output",
                    display_filename=sanitize_display_filename(filename),
                    media_type=media_type,
                    source_job_id=owner.job_id,
                ),
            )
        return _Reservation(artifact_id=artifact_id, job=owner)

    async def _create_part(self, reservation: _Reservation) -> int:
        async with self._sessions() as session:
            locked = await self._lock_owned(session, reservation.artifact_id)
            if locked is None:
                raise FileArtifactError(_RESERVATION_LOST)
            row, _db_now = locked
            renewed = await session.execute(
                text(_RENEW_LEASE), {"artifact_id": reservation.artifact_id}
            )
            if renewed.first() is None:
                raise FileArtifactError(_RESERVATION_LOST)
            fd = await _in_thread(
                create_exclusive,
                self._storage.staging_path(row["storage_reference"]),
                discard=os.close,
            )
            try:
                await session.commit()
            except BaseException:
                os.close(fd)
                raise
        return fd

    async def _produce_part(
        self, reservation: _Reservation, writer: OutputWriter
    ) -> tuple[int, bytes]:
        fd = await self._create_part(reservation)
        fd_open = True
        try:
            size_bytes, digest = await self._write_part(reservation, fd, writer)
            if size_bytes == 0:
                raise FileArtifactError("file_artifact_output_empty")
            fd_open = False
            await _in_thread(sync_and_close, fd)
        except BaseException:
            if fd_open:
                os.close(fd)
            raise
        return size_bytes, digest

    async def _write_part(
        self, reservation: _Reservation, fd: int, writer: OutputWriter
    ) -> tuple[int, bytes]:
        digest = hashlib.sha256()
        size_bytes = 0

        async def write_chunk(chunk: bytes) -> None:
            nonlocal size_bytes
            await _in_thread(write_all, fd, chunk)
            size_bytes += len(chunk)
            digest.update(chunk)

        lost = asyncio.Event()
        producer = asyncio.create_task(writer(write_chunk))
        renewer = asyncio.create_task(
            self._renew_while_producing(reservation.artifact_id, producer, lost)
        )
        cancelled = False
        while not producer.done():
            try:
                await asyncio.wait({producer})
            except asyncio.CancelledError:
                cancelled = True
                producer.cancel()
        renewer.cancel()
        while not renewer.done():
            try:
                await asyncio.wait({renewer})
            except asyncio.CancelledError:
                cancelled = True
        if cancelled or lost.is_set():
            if not producer.cancelled():
                producer.exception()
            if cancelled:
                raise asyncio.CancelledError
            raise FileArtifactError(_RESERVATION_LOST)
        producer.result()
        return size_bytes, digest.digest()

    async def _renew_while_producing(
        self, artifact_id: UUID, producer: asyncio.Task[None], lost: asyncio.Event
    ) -> None:
        while True:
            finished, _pending = await asyncio.wait({producer}, timeout=_RENEW_INTERVAL_SECONDS)
            if finished:
                return
            try:
                owned = await self._renew(artifact_id)
            except Exception:
                _LOGGER.warning("file_artifact_lease_renew_failed artifact=%s", artifact_id)
                owned = False
            if not owned:
                lost.set()
                producer.cancel()
                return

    async def _seal(
        self,
        reservation: _Reservation,
        size_bytes: int,
        digest: bytes,
        inspection: Mapping[str, object] | None,
    ) -> None:
        artifact_id = reservation.artifact_id
        try:
            async with self._sessions() as session:
                locked = await self._lock_for_publication(session, reservation)
                if locked is None:
                    raise FileArtifactError(_RESERVATION_LOST)
                row, db_now = locked
                staging, target = self._reservation_paths(row["storage_reference"])
                await _in_thread(durable_replace, staging, target)
                fields: dict[str, Any] = {
                    **row,
                    "size_bytes": size_bytes,
                    "sha256": digest,
                    "inspection": None if inspection is None else json.dumps(inspection),
                    "sealed_at": db_now,
                    "lifecycle_started_at": db_now,
                    "expires_at": db_now + self._retention,
                }
                await session.execute(
                    text(
                        "UPDATE public.file_artifacts SET state = 'available', "
                        "size_bytes = :size_bytes, sha256 = :sha256, "
                        "inspection = CAST(:inspection AS JSONB), sealed_at = :sealed_at, "
                        "lifecycle_started_at = :lifecycle_started_at, "
                        "expires_at = :expires_at, lease_expires_at = NULL, mac = :mac "
                        "WHERE artifact_id = :artifact_id"
                    ),
                    {
                        "size_bytes": size_bytes,
                        "sha256": digest,
                        "inspection": fields["inspection"],
                        "sealed_at": fields["sealed_at"],
                        "lifecycle_started_at": fields["lifecycle_started_at"],
                        "expires_at": fields["expires_at"],
                        "mac": self._mac(fields, sealed=True),
                        "artifact_id": artifact_id,
                    },
                )
                try:
                    await session.commit()
                except Exception:
                    # Never delete on an unknown outcome: a committed row owns the file.
                    if not await self._is_available(artifact_id):
                        _LOGGER.error("file_artifact_seal_outcome_unknown artifact=%s", artifact_id)
                        raise FileArtifactError("file_artifact_seal_outcome_unknown") from None
        except FileArtifactError:
            raise
        except Exception:
            await self._fail_reservation(reservation, _PRODUCTION_FAILED)
            raise
        _LOGGER.info(
            "file_artifact_sealed purpose=%s artifact=%s size=%s",
            row["purpose"],
            artifact_id,
            size_bytes,
        )

    async def _is_available(self, artifact_id: UUID) -> bool:
        try:
            async with self._sessions() as session:
                state = (
                    await session.execute(
                        text(
                            "SELECT state FROM public.file_artifacts "
                            "WHERE artifact_id = :artifact_id"
                        ),
                        {"artifact_id": artifact_id},
                    )
                ).scalar_one_or_none()
        except Exception:
            return False
        return state == "available"

    async def _fail_reservation(self, reservation: _Reservation, reason: str) -> None:
        """D2: remove this reservation's own files while it still owns a live lease."""

        artifact_id = reservation.artifact_id
        try:
            async with self._sessions() as session, session.begin():
                locked = await self._lock_for_publication(session, reservation)
                if locked is None:
                    return  # the residue belongs to D1b
                row, _db_now = locked
                error = await _in_thread(
                    _removal_error, self._reservation_paths(row["storage_reference"])
                )
                if error is None:
                    await session.execute(
                        text(
                            "UPDATE public.file_artifacts SET state = 'removed', "
                            "removed_at = now(), lease_expires_at = NULL, "
                            "last_removal_error = :error WHERE artifact_id = :artifact_id"
                        ),
                        {"artifact_id": artifact_id, "error": reason},
                    )
                else:
                    await session.execute(
                        text(
                            "UPDATE public.file_artifacts SET lease_expires_at = now(), "
                            "removal_attempts = removal_attempts + 1, "
                            "last_removal_error = :error WHERE artifact_id = :artifact_id"
                        ),
                        {"artifact_id": artifact_id, "error": error},
                    )
        except Exception:
            _LOGGER.warning("file_artifact_failure_cleanup_failed artifact=%s", artifact_id)

    # ---- Cleanup --------------------------------------------------------------

    async def cleanup_once(self) -> FileArtifactCleanupResult:
        due, pending = await self._cleanup_candidates()
        counters = _CleanupCounters(scanned=len(due) + len(pending))
        for artifact_id in due:
            await self._remove_due(artifact_id, counters)
        for artifact_id in pending:
            await self._reclaim_pending(artifact_id, counters)
        _LOGGER.info(
            "file_artifact_cleanup scanned=%s removed=%s failed=%s abandoned=%s",
            counters.scanned,
            counters.removed,
            counters.failed,
            counters.abandoned,
        )
        return FileArtifactCleanupResult(
            scanned=counters.scanned,
            removed=counters.removed,
            failed=counters.failed,
            abandoned=counters.abandoned,
        )

    async def _cleanup_candidates(self) -> tuple[list[UUID], list[UUID]]:
        """One batch of at most ``BATCH_SIZE`` rows across D1a and D1b candidates.

        Rows that keep failing sort last so they cannot starve a batch.
        """

        async with self._sessions() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT artifact_id, state FROM public.file_artifacts "
                        "WHERE storage_scope = :scope AND ("
                        "(state IN ('available','expired') "
                        "AND expires_at <= now() - interval '10 minutes') "
                        "OR (state = 'pending' "
                        "AND lease_expires_at <= now() - interval '1 minute')) "
                        "ORDER BY removal_attempts, CASE WHEN state = 'pending' "
                        "THEN lease_expires_at ELSE expires_at END LIMIT :limit"
                    ),
                    {"scope": self._storage.scope, "limit": BATCH_SIZE},
                )
            ).all()
        due = [row.artifact_id for row in rows if row.state != "pending"]
        pending = [row.artifact_id for row in rows if row.state == "pending"]
        return due, pending

    async def _remove_due(self, artifact_id: UUID, counters: _CleanupCounters) -> None:
        """D1a: expire in its own transaction, unlink, then mark removed."""

        async with self._sessions() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM public.file_artifacts "
                        "WHERE artifact_id = :artifact_id AND storage_scope = :scope "
                        "AND state IN ('available','expired') "
                        "AND expires_at <= now() - interval '10 minutes' "
                        "FOR UPDATE SKIP LOCKED"
                    ),
                    {"artifact_id": artifact_id, "scope": self._storage.scope},
                )
            ).mappings().one_or_none()
            if row is None:
                return
            if not self._mac_valid(row):
                await self._record_failure(session, artifact_id, "metadata_invalid", counters)
                return
            if row["state"] == "available":
                await session.execute(
                    text(
                        "UPDATE public.file_artifacts SET state = 'expired', expired_at = now() "
                        "WHERE artifact_id = :artifact_id"
                    ),
                    {"artifact_id": artifact_id},
                )
            target = self._storage.object_path(row["storage_reference"])
        error = await _in_thread(_removal_error, (target,))
        async with self._sessions() as session, session.begin():
            if error is not None:
                await self._record_failure(session, artifact_id, error, counters)
                return
            removed = await session.execute(
                text(
                    "UPDATE public.file_artifacts SET state = 'removed', removed_at = now(), "
                    "last_removal_error = NULL WHERE artifact_id = :artifact_id "
                    "AND state = 'expired' RETURNING artifact_id"
                ),
                {"artifact_id": artifact_id},
            )
            if removed.first() is not None:
                counters.removed += 1

    async def _reclaim_pending(self, artifact_id: UUID, counters: _CleanupCounters) -> None:
        """D1b: the row lock is held throughout, so no producer can interleave."""

        async with self._sessions() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM public.file_artifacts "
                        "WHERE artifact_id = :artifact_id AND storage_scope = :scope "
                        "AND state = 'pending' "
                        "AND lease_expires_at <= now() - interval '1 minute' "
                        "FOR UPDATE SKIP LOCKED"
                    ),
                    {"artifact_id": artifact_id, "scope": self._storage.scope},
                )
            ).mappings().one_or_none()
            if row is None:
                return
            if not self._mac_valid(row):
                await self._record_failure(session, artifact_id, "metadata_invalid", counters)
                return
            error = await _in_thread(
                _removal_error, self._reservation_paths(row["storage_reference"])
            )
            if error is not None:
                await self._record_failure(session, artifact_id, error, counters)
                return
            await session.execute(
                text(
                    "UPDATE public.file_artifacts SET state = 'removed', removed_at = now(), "
                    "lease_expires_at = NULL, last_removal_error = 'reservation_abandoned' "
                    "WHERE artifact_id = :artifact_id"
                ),
                {"artifact_id": artifact_id},
            )
            counters.abandoned += 1

    @staticmethod
    async def _record_failure(
        session: AsyncSession, artifact_id: UUID, error: str, counters: _CleanupCounters
    ) -> None:
        await session.execute(
            text(_RECORD_REMOVAL_FAILURE), {"artifact_id": artifact_id, "error": error}
        )
        counters.failed += 1


def build_file_artifact_service(
    settings: FileArtifactSettings | None, sessions: async_sessionmaker[AsyncSession]
) -> FileArtifactService | None:
    """Open the configured root; a retention override is only legal on a sample root."""

    if settings is None:
        return None
    storage = open_storage(settings.root)
    override = settings.retention_override_seconds
    if (
        storage.kind == "standard"
        and override is not None
        and override != STANDARD_RETENTION_SECONDS
    ):
        raise FileArtifactError("file_artifact_retention_override_forbidden")
    retention = STANDARD_RETENTION_SECONDS if override is None else override
    _LOGGER.info("file_artifact_storage kind=%s retention_seconds=%s", storage.kind, retention)
    return FileArtifactService(
        PostgreSQLFileArtifactStore(sessions, storage, settings.mac_key, retention)
    )


__all__ = (
    "BATCH_SIZE",
    "PostgreSQLFileArtifactStore",
    "STANDARD_RETENTION_SECONDS",
    "build_file_artifact_service",
    "sanitize_display_filename",
)
