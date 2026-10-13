"""Controlled prepared -> active -> inactive publication in one durable record.

Transactions are short local/DB checks only. The existing capabilities table is
locked and compared; there is no independent browser capability catalog.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Literal, cast

from pydantic import TypeAdapter
from sqlalchemy import select, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.browser_skill.models import BrowserOwner, Digest, OpaqueId
from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    BrowserPublicationRecord,
    canonical_json,
)
from app.infra.persistence.capability_registry.schema import capabilities
from app.ports.browser_publication_store import (
    BrowserManifestAuthorityPort,
    BrowserPublicationError,
    BrowserPublicationReviewPort,
    PublicationOperation,
)
from app.ports.capability_registry import CapabilityRegistryPort, CapabilitySpec
from app.version_binding import capability_version_bindings

_OWNER_DIGEST = "tenant_id=:tenant_id AND publication_digest=:digest"
_OWNER_SKILL = "tenant_id=:tenant_id AND skill_id=:skill_id"


def _owner(value: BrowserOwner) -> BrowserOwner:
    return BrowserOwner(tenant_id=value.tenant_id, user_id=value.user_id,
                        session_id=value.session_id)


def _manifest(value: BrowserPublicationManifest) -> BrowserPublicationManifest:
    try:
        return BrowserPublicationManifest.model_validate_json(value.model_dump_json())
    except Exception:
        raise BrowserPublicationError("browser_publication_manifest_invalid") from None


def _params(owner: BrowserOwner, skill_id: str | None = None,
            digest: str | None = None) -> dict[str, object]:
    params: dict[str, object] = {"tenant_id": owner.tenant_id}
    try:
        if skill_id is not None:
            TypeAdapter(OpaqueId).validate_python(skill_id, strict=True)
            params["skill_id"] = skill_id
        if digest is not None:
            TypeAdapter(Digest).validate_python(digest, strict=True)
            params["digest"] = bytes.fromhex(digest)
    except Exception:
        raise BrowserPublicationError("browser_publication_reference_invalid") from None
    return params


def _record(row: RowMapping) -> BrowserPublicationRecord:
    try:
        manifest = BrowserPublicationManifest.model_validate_json(canonical_json(row["manifest"]))
        if (bytes(cast(bytes, row["publication_digest"])).hex() != manifest.digest
                or row["skill_id"] != manifest.skill.skill_id
                or row["skill_version"] != manifest.skill.version
                or row["capability_id"] != manifest.capability.capability_id
                or bytes(cast(bytes, row["capability_digest"])).hex()
                != manifest.capability_digest):
            raise ValueError("reference_mismatch")
        return BrowserPublicationRecord.model_validate({
            "manifest": manifest, "state": row["state"],
            "activation_revision": row["activation_revision"],
        })
    except Exception:
        raise BrowserPublicationError("browser_publication_storage_invalid") from None


def _rollback_compatible(
    source: BrowserPublicationManifest, candidate: BrowserPublicationManifest,
) -> bool:
    """The candidate may change identity/version, never audited behavior."""
    return (
        candidate.source_kind == source.source_kind == "registered_site"
        and candidate.rollback_source_digest == source.digest
        and candidate.skill.skill_id == source.skill.skill_id
        and candidate.skill.version != source.skill.version
        and candidate.site.version != source.site.version
        and candidate.site.model_dump(exclude={"version", "digest"})
        == source.site.model_dump(exclude={"version", "digest"})
        and candidate.skill.model_dump(exclude={"version", "digest", "site_digest"})
        == source.skill.model_dump(exclude={"version", "digest", "site_digest"})
        and candidate.verifier == source.verifier
        and candidate.output == source.output
        and candidate.capability_snapshot_json == source.capability_snapshot_json
        and candidate.capability_bindings == source.capability_bindings
        and candidate.effect == source.effect
        and candidate.execution_mode == source.execution_mode
    )


class PostgreSQLBrowserPublicationStore:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession],
        capability_registry: CapabilityRegistryPort,
        manifest_authority: BrowserManifestAuthorityPort,
        *,
        review_authority: BrowserPublicationReviewPort | None = None,
        authorize_in_session: Callable[
            [AsyncSession, BrowserOwner, str, PublicationOperation], Awaitable[bool]
        ] | None = None,
        capability_in_session: Callable[
            [AsyncSession, str], Awaitable[CapabilitySpec | None]
        ] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._registry = capability_registry
        self._authority = manifest_authority
        self._review = review_authority
        self._authorize_session = authorize_in_session
        self._capability_session = capability_in_session

    async def _authorize(self, owner: BrowserOwner, skill_id: str,
                         operation: PublicationOperation,
                         session: AsyncSession | None = None) -> None:
        try:
            if session is not None and self._authorize_session is not None:
                permitted = await self._authorize_session(
                    session, _owner(owner), skill_id, operation,
                )
            else:
                permitted = await self._authority.authorize(_owner(owner), skill_id, operation)
        except Exception:
            raise BrowserPublicationError("browser_publication_denied") from None
        if permitted is not True:
            raise BrowserPublicationError("browser_publication_denied")

    async def _source(self, owner: BrowserOwner, manifest: BrowserPublicationManifest) -> None:
        try:
            permitted = await self._authority.verify_manifest(_owner(owner), _manifest(manifest))
        except Exception:
            raise BrowserPublicationError("browser_publication_source_denied") from None
        if permitted is not True:
            raise BrowserPublicationError("browser_publication_source_denied")

    @staticmethod
    def _compare(manifest: BrowserPublicationManifest, current: CapabilitySpec | None) -> None:
        expected_type = "action" if manifest.effect == "controlled_write" else "query"
        if current is None or current.status != "active" or current.type != expected_type:
            raise BrowserPublicationError("browser_publication_capability_unavailable")
        if (canonical_json(current.model_dump(mode="json")) != manifest.capability_snapshot_json
                or capability_version_bindings(current) != manifest.capability_bindings):
            raise BrowserPublicationError("browser_publication_capability_changed")

    async def _current(self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
                       session: AsyncSession, *, source_required: bool = True,
                       ) -> CapabilitySpec:
        if self._capability_session is not None:
            # Installed only by composition after checking the concrete PG
            # registry and identical session factory. This locked row replaces
            # the earlier independent-transaction observation; no fallback.
            try:
                current = await self._capability_session(
                    session, manifest.capability.capability_id,
                )
            except SQLAlchemyError:
                raise  # Preserve the enclosing transaction's store error mapping.
            except Exception:
                raise BrowserPublicationError("browser_publication_registry_unavailable") from None
            self._compare(manifest, current)
        else:
            # Generic installations retain both existing authority observations.
            try:
                current = await self._registry.get(manifest.capability.capability_id)
            except Exception:
                raise BrowserPublicationError("browser_publication_registry_unavailable") from None
            self._compare(manifest, current)
            row = (await session.execute(select(capabilities).where(
                capabilities.c.capability_id == manifest.capability.capability_id
            ).with_for_update(read=True))).mappings().first()
            self._compare(
                manifest, None if row is None else CapabilitySpec.model_validate(dict(row))
            )
        if source_required:
            await self._source(owner, manifest)
        assert current is not None
        return current

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        try:
            async with self._sessions() as session, session.begin():
                await session.execute(text("SELECT pg_advisory_xact_lock_shared(746420212000)"))
                yield session
        except IntegrityError:
            raise BrowserPublicationError("browser_publication_conflict") from None
        except SQLAlchemyError:
            raise BrowserPublicationError("browser_publication_store_unavailable") from None

    @staticmethod
    async def _skill_lock(session: AsyncSession, owner: BrowserOwner, skill_id: str) -> None:
        # Serializes first insertion as well as transitions; tuple encoding avoids
        # tenant/skill concatenation collisions. The unique index remains final.
        key = int.from_bytes(hashlib.sha256(canonical_json(
            ["browser_publication", owner.tenant_id, skill_id]
        ).encode("utf-8")).digest()[:8], "big", signed=True)
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    async def prepare(self, owner: BrowserOwner,
                      manifest: BrowserPublicationManifest) -> BrowserPublicationRecord:
        return await self._prepare(owner, manifest)

    async def prepare_diagnostic_second(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
    ) -> BrowserPublicationRecord:
        """Append only this task's frozen second version; never resurrect its predecessor."""
        from app.infra.browser.fixed_synthetic_seed import (
            SYNTHETIC_TENANT,
            build_fixed_synthetic_diagnostic_source,
            build_fixed_synthetic_query_source,
        )
        from app.infra.browser.synthetic_configuration import synthetic_jev_manifest

        decision = synthetic_jev_manifest()
        successor = build_fixed_synthetic_diagnostic_source(decision).manifest
        predecessor = build_fixed_synthetic_query_source(decision).manifest
        if owner.tenant_id != SYNTHETIC_TENANT or _manifest(manifest) != successor:
            raise BrowserPublicationError("browser_publication_source_denied")
        return await self._prepare(owner, manifest, predecessor=predecessor)

    async def prepare_visible_complete(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
    ) -> BrowserPublicationRecord:
        """Append the fixed visible source after both exact inactive trial publications."""
        from app.infra.browser.fixed_synthetic_seed import (
            SYNTHETIC_TENANT,
            build_fixed_synthetic_diagnostic_source,
            build_fixed_synthetic_query_source,
            build_fixed_synthetic_visible_query_source,
        )
        from app.infra.browser.synthetic_configuration import synthetic_jev_manifest

        decision = synthetic_jev_manifest()
        successor = build_fixed_synthetic_visible_query_source(decision).manifest
        if owner.tenant_id != SYNTHETIC_TENANT or _manifest(manifest) != successor:
            raise BrowserPublicationError("browser_publication_source_denied")
        return await self._prepare(
            owner, manifest, predecessor=build_fixed_synthetic_query_source(decision).manifest,
            diagnostic_predecessor=build_fixed_synthetic_diagnostic_source(decision).manifest,
        )

    async def prepare_observe_only(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
        *, attempt_id: str | None = None,
    ) -> BrowserPublicationRecord:
        """Append a fully verified fixed observation instance without changing history."""
        from app.infra.browser.fixed_synthetic_seed import (
            SYNTHETIC_TENANT,
            build_fixed_synthetic_diagnostic_source,
            build_fixed_synthetic_observe_only_source,
            build_fixed_synthetic_query_source,
            build_fixed_synthetic_visible_query_source,
        )
        from app.infra.browser.synthetic_configuration import synthetic_jev_manifest

        decision = synthetic_jev_manifest()
        successor = build_fixed_synthetic_observe_only_source(
            decision, attempt_id=attempt_id
        ).manifest
        if owner.tenant_id != SYNTHETIC_TENANT or _manifest(manifest) != successor:
            raise BrowserPublicationError("browser_publication_source_denied")
        if attempt_id is not None:
            return await self._prepare(owner, manifest, observe_attempt_id=attempt_id)
        return await self._prepare(
            owner, manifest, predecessor=build_fixed_synthetic_query_source(decision).manifest,
            diagnostic_predecessor=build_fixed_synthetic_diagnostic_source(decision).manifest,
            visible_predecessor=build_fixed_synthetic_visible_query_source(decision).manifest,
        )

    async def _prepare(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest, *,
        predecessor: BrowserPublicationManifest | None = None,
        diagnostic_predecessor: BrowserPublicationManifest | None = None,
        visible_predecessor: BrowserPublicationManifest | None = None,
        observe_attempt_id: str | None = None,
    ) -> BrowserPublicationRecord:
        owner, manifest = _owner(owner), _manifest(manifest)
        if observe_attempt_id is not None:
            from app.infra.browser.fixed_synthetic_seed import (
                SYNTHETIC_TENANT,
                build_fixed_synthetic_observe_only_source,
            )
            from app.infra.browser.synthetic_configuration import synthetic_jev_manifest

            expected = build_fixed_synthetic_observe_only_source(
                synthetic_jev_manifest(), attempt_id=observe_attempt_id,
            ).manifest
            if (owner.tenant_id != SYNTHETIC_TENANT or manifest != expected
                    or any(item is not None for item in (
                        predecessor, diagnostic_predecessor, visible_predecessor,
                    ))):
                raise BrowserPublicationError("browser_publication_source_denied")
        if diagnostic_predecessor is not None and predecessor is None:
            raise BrowserPublicationError("browser_publication_source_denied")
        if visible_predecessor is not None and diagnostic_predecessor is None:
            raise BrowserPublicationError("browser_publication_source_denied")
        skill_id = manifest.skill.skill_id
        await self._authorize(owner, skill_id, "prepare")
        async with self._transaction() as session:
            await self._skill_lock(session, owner, skill_id)
            await self._authorize(owner, skill_id, "prepare")
            await self._current(owner, manifest, session)
            params = _params(owner, skill_id, manifest.digest)
            previous = (await session.execute(text(
                "SELECT * FROM browser_publications WHERE " + _OWNER_SKILL + " FOR UPDATE"
            ), params)).mappings().all()
            if manifest.rollback_source_digest is not None:
                source = [
                    _record(row) for row in previous
                    if bytes(cast(bytes, row["publication_digest"])).hex()
                    == manifest.rollback_source_digest
                ]
                if (len(source) != 1 or source[0].state != "inactive"
                        or not _rollback_compatible(source[0].manifest, manifest)):
                    raise BrowserPublicationError(
                        "browser_publication_rollback_source_invalid"
                    )
            # Independent observe instances append only; existing records stay unchanged.
            # No inactive record may return to prepared.
            if observe_attempt_id is not None:
                records = [_record(row) for row in previous]
                new = [record for record in records if record.manifest == manifest]
                if (len(new) > 1 or any(record.state != "inactive"
                                       for record in records if record.manifest != manifest)):
                    raise BrowserPublicationError("browser_publication_already_prepared")
                if new:
                    if new[0].state == "prepared" and new[0].activation_revision == 0:
                        return new[0]
                    raise BrowserPublicationError("browser_publication_already_prepared")
            elif predecessor is not None:
                records = [_record(row) for row in previous]
                history: tuple[BrowserPublicationManifest | None, ...] = (
                    (predecessor,) if diagnostic_predecessor is None
                           else (predecessor, diagnostic_predecessor))
                if visible_predecessor is not None:
                    history = (predecessor, diagnostic_predecessor, visible_predecessor)
                prior = [[record for record in records if record.manifest == expected]
                         for expected in history]
                new = [record for record in records if record.manifest == manifest]
                if (any(len(matches) != 1 or matches[0].state != "inactive"
                        or matches[0].activation_revision != 2 for matches in prior)
                        or len(records) != len(history) + len(new) or len(new) > 1):
                    raise BrowserPublicationError("browser_publication_already_prepared")
                if new:
                    if new[0].state == "prepared" and new[0].activation_revision == 0:
                        return new[0]
                    raise BrowserPublicationError("browser_publication_already_prepared")
            elif manifest.source_kind == "registered_site":
                # A registered Site may add an immutable Skill version while the
                # previous head serves existing Runs. Keep only one unreviewed
                # candidate per Skill and never recycle a version or inactive row.
                records = [_record(row) for row in previous]
                same = [record for record in records if record.manifest == manifest]
                if same:
                    if len(same) == 1 and same[0].state == "prepared":
                        return same[0]
                    raise BrowserPublicationError("browser_publication_already_prepared")
                if any(
                    record.state == "prepared"
                    or record.manifest.skill.version == manifest.skill.version
                    for record in records
                ):
                    raise BrowserPublicationError("browser_publication_version_conflict")
            elif previous:
                existing = _record(previous[0])
                if (len(previous) == 1 and existing.manifest == manifest
                        and existing.state == "prepared"):
                    return existing
                raise BrowserPublicationError("browser_publication_already_prepared")
            params.update({"version": manifest.skill.version,
                           "capability_id": manifest.capability.capability_id,
                           "capability_digest": bytes.fromhex(manifest.capability_digest),
                           "manifest": manifest.model_dump_json()})
            row = (await session.execute(text(
                "INSERT INTO browser_publications (tenant_id,publication_digest,skill_id,"
                "skill_version,capability_id,capability_digest,manifest) VALUES "
                "(:tenant_id,:digest,:skill_id,:version,:capability_id,:capability_digest,"
                "CAST(:manifest AS jsonb)) RETURNING *"
            ), params)).mappings().one()
            return _record(row)

    async def activate(self, owner: BrowserOwner, skill_id: str, publication_digest: str,
                       *, expected_revision: int = 0) -> BrowserPublicationRecord:
        return await self._transition(owner, skill_id, publication_digest,
                                      expected_revision, "activate")

    async def activate_reviewed(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest, *,
        draft_digest: str, replay_digest: str, approval_ref: str,
        reviewer_ref: str, evidence_digest: str,
        expected_active_digest: str | None = None,
    ) -> BrowserPublicationRecord:
        """Switch a registered version only after a current stored review check."""
        checked = _manifest(manifest)
        if checked.source_kind != "registered_site" or self._review is None:
            raise BrowserPublicationError("browser_publication_review_required")
        if checked.rollback_source_digest is not None and expected_active_digest is None:
            raise BrowserPublicationError("browser_publication_head_required")
        if expected_active_digest is not None:
            _params(_owner(owner), digest=expected_active_digest)
        return await self._transition(
            owner, checked.skill.skill_id, checked.digest, 0, "activate",
            review=(checked, draft_digest, replay_digest, approval_ref,
                    reviewer_ref, evidence_digest),
            expected_active_digest=expected_active_digest,
        )

    async def activate_initial(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest, *,
        task_id: str, run_id: str, approval_ref: str,
    ) -> BrowserPublicationRecord:
        """First registered version requires a verified prepared Run and HumanGate."""
        checked = _manifest(manifest)
        if (checked.source_kind != "registered_site"
                or checked.effect not in {"read_only", "controlled_write"}
                or checked.rollback_source_digest is not None or self._review is None):
            raise BrowserPublicationError("browser_publication_initial_invalid")
        for identity in (task_id, run_id, approval_ref):
            TypeAdapter(OpaqueId).validate_python(identity, strict=True)
        return await self._transition(
            owner, checked.skill.skill_id, checked.digest, 0, "activate",
            initial=(checked, task_id, run_id, approval_ref),
        )

    async def activate_fixed_initial(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest,
    ) -> BrowserPublicationRecord:
        """Activate only the installed D FILL_STOP seed with its explicit grant."""
        checked = _manifest(manifest)
        authorize = getattr(self._authority, "authorize_fixed_initial", None)
        if not callable(authorize) or await authorize(_owner(owner), checked) is not True:
            raise BrowserPublicationError("browser_publication_review_required")
        return await self._transition(
            owner, checked.skill.skill_id, checked.digest, 0, "activate",
            fixed_initial=checked,
        )

    async def deactivate(self, owner: BrowserOwner, skill_id: str, publication_digest: str,
                         *, expected_revision: int) -> BrowserPublicationRecord:
        return await self._transition(owner, skill_id, publication_digest,
                                      expected_revision, "deactivate")

    async def _transition(self, owner: BrowserOwner, skill_id: str, digest: str,
                          expected_revision: int,
                          operation: PublicationOperation, *,
                          review: tuple[BrowserPublicationManifest, str, str, str, str, str]
                          | None = None,
                          initial: tuple[BrowserPublicationManifest, str, str, str]
                          | None = None,
                          fixed_initial: BrowserPublicationManifest | None = None,
                          expected_active_digest: str | None = None,
                          ) -> BrowserPublicationRecord:
        owner = _owner(owner)
        params = _params(owner, skill_id, digest)
        if type(expected_revision) is not int or expected_revision not in (0, 1):
            raise BrowserPublicationError("browser_publication_revision_invalid")
        await self._authorize(owner, skill_id, operation)
        async with self._transaction() as session:
            await self._skill_lock(session, owner, skill_id)
            row = (await session.execute(text(
                "SELECT * FROM browser_publications WHERE " + _OWNER_DIGEST + " FOR UPDATE"
            ), params)).mappings().first()
            if row is None:
                raise BrowserPublicationError("browser_publication_not_found")
            record = _record(row)
            before, after = ("prepared", "active") if operation == "activate" else (
                "active", "inactive")
            if (record.manifest.skill.skill_id != skill_id or record.state != before
                    or record.activation_revision != expected_revision):
                raise BrowserPublicationError("browser_publication_revision_conflict")
            if operation == "activate" and record.manifest.source_kind == "registered_site":
                if fixed_initial is not None:
                    authorize = getattr(self._authority, "authorize_fixed_initial", None)
                    if (review is not None or initial is not None
                            or fixed_initial != record.manifest
                            or not callable(authorize)
                            or await authorize(owner, fixed_initial) is not True):
                        raise BrowserPublicationError("browser_publication_review_required")
                else:
                    if self._review is None or (review is None) == (initial is None):
                        raise BrowserPublicationError("browser_publication_review_required")
                    try:
                        if initial is not None:
                            checked, task_id, run_id, approval = initial
                            if checked != record.manifest:
                                raise BrowserPublicationError("browser_publication_review_required")
                            accepted = await self._review.check_initial_in_session(
                                session, owner, checked, task_id=task_id,
                                run_id=run_id, approval_ref=approval,
                            )
                        else:
                            assert review is not None
                            checked, draft, replay, approval, reviewer, evidence = review
                            if checked != record.manifest:
                                raise BrowserPublicationError("browser_publication_review_required")
                            accepted = await self._review.check_in_session(
                                session, owner, checked, draft_digest=draft,
                                replay_digest=replay, approval_ref=approval,
                                reviewer_ref=reviewer, evidence_digest=evidence,
                            )
                    except Exception:
                        raise BrowserPublicationError(
                            "browser_publication_review_unavailable"
                        ) from None
                    if accepted is not True:
                        raise BrowserPublicationError("browser_publication_review_denied")
            await self._authorize(owner, skill_id, operation)
            # Deactivation must remain possible after source/Capability revocation.
            if operation == "activate":
                await self._current(owner, record.manifest, session)
                if record.manifest.source_kind == "registered_site":
                    # The existing PG partial unique index enforces one active
                    # head. Retire it and activate the reviewed candidate in
                    # this same transaction; rollback keeps the old head live.
                    prior = (await session.execute(text(
                        "SELECT * FROM browser_publications WHERE " + _OWNER_SKILL
                        + " AND state='active' FOR UPDATE"
                    ), params)).mappings().first()
                    if expected_active_digest is not None and (
                        prior is None or bytes(cast(bytes, prior["publication_digest"])).hex()
                        != expected_active_digest
                    ):
                        raise BrowserPublicationError("browser_publication_head_changed")
                    if (initial is not None or fixed_initial is not None) and prior is not None:
                        raise BrowserPublicationError("browser_publication_head_changed")
                    if prior is not None:
                        previous = _record(prior)
                        await self._authorize(owner, skill_id, "deactivate", session)
                        await session.execute(text(
                            "UPDATE browser_publications SET state='inactive',"
                            "activation_revision=activation_revision+1 WHERE "
                            + _OWNER_DIGEST + " AND state='active' AND activation_revision=1"
                        ), _params(owner, skill_id, previous.manifest.digest))
            params.update({"before": before, "after": after, "revision": expected_revision})
            updated = (await session.execute(text(
                "UPDATE browser_publications SET state=:after,"
                "activation_revision=activation_revision+1,"
                "activated_at=COALESCE(activated_at,CURRENT_TIMESTAMP) WHERE "
                + _OWNER_DIGEST + " AND state=:before AND activation_revision=:revision RETURNING *"
            ), params)).mappings().one()
            return _record(updated)

    async def get_active(self, owner: BrowserOwner,
                         skill_id: str) -> BrowserPublicationRecord | None:
        owner = _owner(owner)
        params = _params(owner, skill_id)
        await self._authorize(owner, skill_id, "read")
        async with self._transaction() as session:
            row = (await session.execute(text(
                "SELECT * FROM browser_publications WHERE " + _OWNER_SKILL
                + " AND state='active' FOR SHARE"
            ), params)).mappings().first()
            if row is None:
                return None
            record = _record(row)
            await self._current(owner, record.manifest, session)
            await self._authorize(owner, skill_id, "read")
            return record

    async def get_frozen(self, owner: BrowserOwner,
                         publication_digest: str) -> BrowserPublicationRecord | None:
        owner = _owner(owner)
        params = _params(owner, digest=publication_digest)
        async with self._transaction() as session:
            row = (await session.execute(text(
                "SELECT * FROM browser_publications WHERE " + _OWNER_DIGEST
                + " AND state IN ('active','inactive') FOR SHARE"
            ), params)).mappings().first()
            if row is None:
                return None
            record = _record(row)
            await self._authorize(owner, record.manifest.skill.skill_id, "read")
            # Historical metadata/result access still requires the live
            # Capability and grant, but the old browser source need not stay
            # installed after a reviewed version switch.
            await self._current(owner, record.manifest, session,
                                source_required=False)
            return record

    async def assert_current(self, owner: BrowserOwner,
                             manifest: BrowserPublicationManifest) -> None:
        async with self._transaction() as session:
            await self.assert_current_in_session(session, owner, manifest, require_active=False)

    async def assert_current_in_session(
        self, session: AsyncSession, owner: BrowserOwner,
        manifest: BrowserPublicationManifest, *, require_active: bool = True,
        operation: Literal["read", "execute"] = "execute",
    ) -> CapabilitySpec:
        """One exact publication read, with live grants on both sides of DB/source checks."""
        owner, manifest = _owner(owner), _manifest(manifest)
        await session.execute(text("SELECT pg_advisory_xact_lock_shared(746420212000)"))
        await self._authorize(owner, manifest.skill.skill_id, operation, session)
        row = (await session.execute(text(
            "SELECT * FROM browser_publications WHERE " + _OWNER_DIGEST
            + " AND state IN ('active','inactive') FOR SHARE"
        ), _params(owner, digest=manifest.digest))).mappings().first()
        if row is None:
            raise BrowserPublicationError("browser_publication_not_found")
        record = _record(row)
        if record.manifest != manifest:
            raise BrowserPublicationError("browser_publication_not_found")
        if require_active and record.state != "active":
            raise BrowserPublicationError("browser_publication_inactive")
        await self._authorize(owner, manifest.skill.skill_id, "read", session)
        capability = await self._current(
            owner, manifest, session,
            source_required=require_active or operation == "execute",
        )
        await self._authorize(owner, manifest.skill.skill_id, "read", session)
        await self._authorize(owner, manifest.skill.skill_id, operation, session)
        return capability
