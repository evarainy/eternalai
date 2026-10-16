"""Publication digests, a legacy synthetic fixture, and reviewed PG transitions.

The process-local reference authority below remains only for synthetic contract
tests. Reviewed teaching calls the existing BrowserPublicationStorePort; it does
not create another catalog or turn a draft/replay digest into authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from threading import Lock
from types import MappingProxyType
from typing import Annotated, Literal, Never, Self, SupportsIndex

from pydantic import BaseModel, Field, TypeAdapter, model_validator

from app.browser_skill.models import BrowserOwner, BrowserSkill, Contract, Digest, Epoch, OpaqueId
from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    BrowserPublicationRecord,
    rollback_version_manifest,
)
from app.ports.browser_publication_store import BrowserPublicationStorePort

Operation = Literal["prepare", "activate", "rollback", "read"]
OwnerKey = tuple[str, str, str]
Authorize = Callable[[BrowserOwner, str, Operation], bool]


class PublicationCatalogError(ValueError):
    """Fixed diagnostic code without manifest or owner data."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class BrowserPublicationCatalog:
    """Immutable installed manifest lookup, not publication or run authority.

    The PG publication head still decides which Skill version a new Run may
    enter. An admitted Run resolves its pinned digest here, even after a later
    head switch. The installer separately verifies each registered source and
    binds its executable Site and Verifier to this exact manifest.
    """

    __slots__ = ("_by_identity", "_by_digest")

    def __init__(self, manifests: tuple[BrowserPublicationManifest, ...]) -> None:
        if not isinstance(manifests, tuple) or not manifests:
            raise PublicationCatalogError("browser_publication_catalog_empty")
        by_identity: dict[tuple[str, str, str], bytes] = {}
        by_digest: dict[str, bytes] = {}
        versions: set[tuple[str, str]] = set()
        for manifest in manifests:
            if not isinstance(manifest, BrowserPublicationManifest):
                raise PublicationCatalogError("browser_publication_catalog_manifest_invalid")
            try:
                frozen = BrowserPublicationManifest.model_validate_json(
                    manifest.model_dump_json()
                )
            except (ValueError, TypeError):
                raise PublicationCatalogError(
                    "browser_publication_catalog_manifest_invalid"
                ) from None
            version_key = (frozen.skill.skill_id, frozen.skill.version)
            if version_key in versions:
                raise PublicationCatalogError("browser_publication_catalog_version_conflict")
            if frozen.digest in by_digest:
                raise PublicationCatalogError("browser_publication_catalog_digest_conflict")
            encoded = frozen.model_dump_json().encode("utf-8")
            by_identity[(*version_key, frozen.digest)] = encoded
            by_digest[frozen.digest] = encoded
            versions.add(version_key)
        self._by_identity = MappingProxyType(by_identity)
        self._by_digest = MappingProxyType(by_digest)

    def resolve_exact(
        self, skill_id: str, version: str, digest: str
    ) -> BrowserPublicationManifest:
        if not all(type(value) is str for value in (skill_id, version, digest)):
            raise PublicationCatalogError("browser_publication_catalog_reference_invalid")
        encoded = self._by_identity.get((skill_id, version, digest))
        if encoded is None:
            raise PublicationCatalogError("browser_publication_catalog_not_found")
        return BrowserPublicationManifest.model_validate_json(encoded)

    def by_digest(self, digest: str) -> BrowserPublicationManifest:
        if type(digest) is not str:
            raise PublicationCatalogError("browser_publication_catalog_reference_invalid")
        encoded = self._by_digest.get(digest)
        if encoded is None:
            raise PublicationCatalogError("browser_publication_catalog_not_found")
        return BrowserPublicationManifest.model_validate_json(encoded)

    def digests(self) -> tuple[str, ...]:
        """Installed candidates for trusted worker dispatch; not active heads."""
        return tuple(self._by_digest)


def canonical_digest(domain: str, payload: Mapping[str, object]) -> str:
    """SHA256(domain + LF + sorted compact UTF-8 JSON), excluding only own digest.

    Nested digests remain covered. Schema and version are ordinary covered fields;
    this explicit omission avoids a self-digest cycle. No NaN/Infinity is allowed.
    """
    body = {key: value for key, value in payload.items() if key != "digest"}
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(domain.encode("ascii") + b"\n" + encoded).hexdigest()


def _check_digest(model: BaseModel, domain: str, digest: str) -> None:
    if canonical_digest(domain, model.model_dump(mode="json")) != digest:
        raise ValueError("publication_digest_mismatch")


class DependencyManifest(Contract):
    schema_version: Literal["browser_dependency.v1"] = "browser_dependency.v1"
    kind: Literal["site", "verifier"]
    dependency_id: OpaqueId
    version: OpaqueId
    digest: Digest
    source_kind: Literal["synthetic_fixture"] = "synthetic_fixture"
    # A non-routable fixture label, never an approved cloud source.
    origin: Literal["https://ecology9.invalid"] = "https://ecology9.invalid"
    contract: Literal["synthetic_site_v1", "synthetic_business_key_verifier_v1"]

    @model_validator(mode="after")
    def integrity(self) -> Self:
        expected = (
            "synthetic_site_v1" if self.kind == "site" else ("synthetic_business_key_verifier_v1")
        )
        if self.contract != expected:
            raise ValueError("dependency_kind_mismatch")
        _check_digest(self, "browser_dependency.v1", self.digest)
        return self


class CapabilityDescriptor(Contract):
    """Local discovery view only; never a second independently mutable catalog."""

    schema_version: Literal["browser_descriptor.v1"] = "browser_descriptor.v1"
    capability_id: OpaqueId
    version: OpaqueId
    digest: Digest
    skill_id: OpaqueId
    skill_version: OpaqueId
    skill_digest: Digest
    target_system: Literal["oa"] = "oa"
    visibility: Literal["internal", "public"]
    http_api_preferred: Literal[True] = True
    execution_state: Literal["synthetic_contract_only"] = "synthetic_contract_only"
    deferred_capabilities: Annotated[tuple[OpaqueId, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if (self.skill_id == "login_assist") != (self.visibility == "internal"):
            raise ValueError("internal_login_visibility_required")
        _check_digest(self, "browser_descriptor.v1", self.digest)
        return self


class Publication(Contract):
    schema_version: Literal["browser_publication.v1"] = "browser_publication.v1"
    version: OpaqueId
    digest: Digest
    skill: BrowserSkill
    site: DependencyManifest
    verifier: DependencyManifest
    descriptor: CapabilityDescriptor
    dependency_digest: Digest

    @model_validator(mode="after")
    def integrity(self) -> Self:
        skill = self.skill
        if self.site.kind != "site" or self.verifier.kind != "verifier":
            raise ValueError("dependency_kind_mismatch")
        if (skill.site_id, skill.site_digest) != (self.site.dependency_id, self.site.digest):
            raise ValueError("site_dependency_mismatch")
        if (skill.verifier_id, skill.verifier_digest) != (
            self.verifier.dependency_id,
            self.verifier.digest,
        ):
            raise ValueError("verifier_dependency_mismatch")
        if (
            (self.descriptor.skill_id, self.descriptor.skill_version, self.descriptor.skill_digest)
            != (skill.skill_id, skill.version, skill.digest)
            or self.version != skill.version
            or self.descriptor.version != self.version
        ):
            raise ValueError("descriptor_dependency_mismatch")
        _check_digest(skill, "browser_skill.v1", skill.digest)
        expected = canonical_digest(
            "browser_dependencies.v1",
            {
                "site": self.site.model_dump(mode="json"),
                "verifier": self.verifier.model_dump(mode="json"),
                "descriptor": self.descriptor.model_dump(mode="json"),
            },
        )
        if expected != self.dependency_digest:
            raise ValueError("publication_dependencies_mismatch")
        _check_digest(self, "browser_publication.v1", self.digest)
        return self


class Activation(Contract):
    revision: Epoch
    publication_digest: Digest | None


@dataclass
class _State:
    revision: int = 0
    active: str | None = None
    prepared: dict[str, bytes] = field(default_factory=dict)
    versions: dict[str, str] = field(default_factory=dict)
    activated: set[str] = field(default_factory=set)


class PublicationError(ValueError):
    """Fixed error codes only; no payload or owner values in diagnostics."""


def _owner_copy(owner: BrowserOwner) -> BrowserOwner:
    # session_id can be excluded from serialization; never use model_dump as a key.
    return BrowserOwner(
        tenant_id=owner.tenant_id, user_id=owner.user_id, session_id=owner.session_id
    )


def _owner_key(owner: BrowserOwner) -> OwnerKey:
    return owner.tenant_id, owner.user_id, owner.session_id


class InMemoryPublicationAuthority:
    """Single writer authority for source tests; no cross-process durability.

    Authorization is mandatory and synchronous. The callback evaluates the
    current grant before each operation; the caller owns its grant source.
    Activation/rollback and both read views share a lock and the same immutable
    bytes. External catalog/database transactions are deliberately not assumed.
    """

    def __init__(self, authorize: Authorize) -> None:
        if not callable(authorize):
            raise TypeError("publication_authorizer_required")
        self._authorize = authorize
        self._states: dict[tuple[OwnerKey, str], _State] = {}
        self._lock = Lock()

    def _authorized(self, owner: BrowserOwner, skill_id: str, operation: Operation) -> BrowserOwner:
        frozen_owner = _owner_copy(owner)
        if self._authorize(_owner_copy(frozen_owner), skill_id, operation) is not True:
            raise PublicationError("publication_denied")
        # Callback does not get the object used for identity after this point.
        return frozen_owner

    @staticmethod
    def _revision(state: _State, expected_revision: int) -> None:
        if type(expected_revision) is not int or expected_revision < 0:
            raise PublicationError("publication_revision_invalid")
        if state.revision != expected_revision:
            raise PublicationError("publication_revision_conflict")

    def prepare(
        self, owner: BrowserOwner, publication: Publication, *, expected_revision: int
    ) -> Activation:
        # JSON revalidation descends into nested models even after model_copy or
        # model_construct; stored bytes are never aliased to caller-owned models.
        checked = Publication.model_validate_json(publication.model_dump_json())
        frozen_owner = self._authorized(owner, checked.skill.skill_id, "prepare")
        key = (_owner_key(frozen_owner), checked.skill.skill_id)
        with self._lock:
            state = self._states.get(key, _State())
            self._revision(state, expected_revision)
            previous = state.versions.get(checked.version)
            if previous is not None and previous != checked.digest:
                raise PublicationError("publication_version_conflict")
            state.prepared[checked.digest] = checked.model_dump_json().encode("utf-8")
            state.versions[checked.version] = checked.digest
            state.revision += 1
            self._states[key] = state
            return Activation(revision=state.revision, publication_digest=state.active)

    def activate(
        self, owner: BrowserOwner, skill_id: str, publication_digest: str, *, expected_revision: int
    ) -> Activation:
        return self._switch(owner, skill_id, publication_digest, expected_revision, "activate")

    def rollback(
        self, owner: BrowserOwner, skill_id: str, publication_digest: str, *, expected_revision: int
    ) -> Activation:
        return self._switch(owner, skill_id, publication_digest, expected_revision, "rollback")

    def _switch(
        self,
        owner: BrowserOwner,
        skill_id: str,
        digest: str,
        expected_revision: int,
        operation: Literal["activate", "rollback"],
    ) -> Activation:
        frozen_owner = self._authorized(owner, skill_id, operation)
        with self._lock:
            state = self._states.get((_owner_key(frozen_owner), skill_id), _State())
            self._revision(state, expected_revision)
            if digest not in state.prepared:
                raise PublicationError("publication_not_prepared")
            checked = Publication.model_validate_json(state.prepared[digest])
            if checked.digest != digest or checked.skill.skill_id != skill_id:
                raise PublicationError("publication_reference_mismatch")
            if operation == "rollback" and digest not in state.activated:
                raise PublicationError("publication_not_previously_active")
            state.active = digest
            state.activated.add(digest)
            state.revision += 1
            return Activation(revision=state.revision, publication_digest=digest)

    def read_active(self, owner: BrowserOwner, skill_id: str) -> Publication | None:
        frozen_owner = self._authorized(owner, skill_id, "read")
        with self._lock:
            state = self._states.get((_owner_key(frozen_owner), skill_id))
            if state is None or state.active is None:
                return None
            checked = Publication.model_validate_json(state.prepared[state.active])
            if checked.digest != state.active or checked.skill.skill_id != skill_id:
                raise PublicationError("publication_reference_mismatch")
            return checked

    def view(self, owner: BrowserOwner) -> PublicationView:
        return PublicationView(self, owner)


class PublicationView:
    """Owner-bound BrowserSkillStorePort and digest-checked descriptor read view.

    A Run retains its previously returned immutable Publication. New lookups
    accept only the active version. Never re-fetch the active pointer for an old
    Run or interpret a prepared draft as its frozen execution manifest.
    """

    def __init__(self, authority: InMemoryPublicationAuthority, owner: BrowserOwner) -> None:
        self._authority = authority
        self._owner = _owner_copy(owner)

    async def get_published(self, skill_id: str, version: str, digest: str) -> BrowserSkill | None:
        publication = self._authority.read_active(self._owner, skill_id)
        if publication is None or (publication.skill.version, publication.skill.digest) != (
            version,
            digest,
        ):
            return None
        return publication.skill

    async def get_descriptor(
        self, skill_id: str, version: str, digest: str
    ) -> CapabilityDescriptor | None:
        publication = self._authority.read_active(self._owner, skill_id)
        if publication is None or (
            publication.descriptor.version,
            publication.descriptor.digest,
        ) != (version, digest):
            return None
        return publication.descriptor


@dataclass(frozen=True, slots=True, repr=False)
class TeachingReviewFact:
    """Private current approval returned by the trusted review authority.

    The digest/reference alone cannot authorize publication. The PG manifest
    authority must independently recheck this fact in its prepare/switch path.
    """

    owner: BrowserOwner
    draft_digest: str
    replay_digest: str
    publication_digest: str
    approval_ref: str
    reviewer_ref: str
    evidence_digest: str
    approved: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", _owner_copy(self.owner))
        for value in (self.draft_digest, self.replay_digest,
                      self.publication_digest, self.evidence_digest):
            TypeAdapter(Digest).validate_python(value, strict=True)
        for value in (self.approval_ref, self.reviewer_ref):
            TypeAdapter(OpaqueId).validate_python(value, strict=True)
        if type(self.approved) is not bool:
            raise PublicationError("teaching_review_fact_invalid")

    def __repr__(self) -> str:
        return "<TeachingReviewFact: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")


@dataclass(frozen=True, slots=True, repr=False)
class ReviewedPublicationAuthority:
    """One PG authority plus trusted replay/review/transactional switch seams."""

    store: BrowserPublicationStorePort
    authorize: Callable[[BrowserOwner, str, str], Awaitable[bool]]
    replay_current: Callable[[BrowserOwner, str, str, str], Awaitable[bool]]
    review_fact: Callable[[BrowserOwner, str, str], Awaitable[TeachingReviewFact | None]]

    def __post_init__(self) -> None:
        if not all(callable(value) for value in (
            self.authorize, self.replay_current, self.review_fact,
        )):
            raise PublicationError("teaching_publication_authority_required")

    def __repr__(self) -> str:
        return "<ReviewedPublicationAuthority: process-local>"

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")

    def __getstate__(self) -> Never:
        raise TypeError("teaching_private_serialization_forbidden")


async def _review_current(
    owner: BrowserOwner, manifest: BrowserPublicationManifest,
    draft_digest: str, replay_digest: str, authority: ReviewedPublicationAuthority,
    operation: str,
) -> TeachingReviewFact:
    if await authority.authorize(owner, manifest.skill.skill_id, operation) is not True:
        raise PublicationError("teaching_publication_denied")
    if await authority.replay_current(
        owner, draft_digest, replay_digest, manifest.digest,
    ) is not True:
        raise PublicationError("teaching_replay_stale")
    raw = await authority.review_fact(owner, draft_digest, replay_digest)
    if raw is None:
        raise PublicationError("teaching_review_required")
    fact = TeachingReviewFact(
        raw.owner, raw.draft_digest, raw.replay_digest,
        raw.publication_digest, raw.approval_ref, raw.reviewer_ref,
        raw.evidence_digest, raw.approved,
    )
    if (
        fact.owner != owner or fact.draft_digest != draft_digest
        or fact.replay_digest != replay_digest
        or fact.publication_digest != manifest.digest or not fact.approved
    ):
        raise PublicationError("teaching_review_mismatch")
    return fact


async def publish_reviewed_candidate(
    owner: BrowserOwner,
    manifest: BrowserPublicationManifest,
    *,
    draft_digest: str,
    replay_digest: str,
    authority: ReviewedPublicationAuthority,
) -> BrowserPublicationRecord:
    """Prepare then atomically switch the existing PG publication head.

    The switch callback must lock the tenant/Skill head, recheck the exact
    approval and Capability/Policy, and retain old immutable rows for old Runs.
    An approved review does not itself grant execution authority.
    """
    try:
        owner = _owner_copy(owner)
        manifest = BrowserPublicationManifest.model_validate_json(manifest.model_dump_json())
        TypeAdapter(Digest).validate_python(draft_digest, strict=True)
        TypeAdapter(Digest).validate_python(replay_digest, strict=True)
        fact = await _review_current(
            owner, manifest, draft_digest, replay_digest, authority, "publish",
        )
        prepared = await authority.store.prepare(owner, manifest)
        if prepared.manifest != manifest or prepared.state != "prepared":
            raise PublicationError("teaching_prepare_mismatch")
        if await _review_current(
            owner, manifest, draft_digest, replay_digest, authority, "publish",
        ) != fact:
            raise PublicationError("teaching_review_stale")
        active = await authority.store.activate_reviewed(
            owner, manifest,
            draft_digest=draft_digest, replay_digest=replay_digest,
            approval_ref=fact.approval_ref, reviewer_ref=fact.reviewer_ref,
            evidence_digest=fact.evidence_digest,
        )
        if active.manifest != manifest or active.state != "active":
            raise PublicationError("teaching_activation_mismatch")
        frozen = await authority.store.get_frozen(owner, manifest.digest)
        current = await authority.store.get_active(owner, manifest.skill.skill_id)
        if frozen != active or current != active:
            raise PublicationError("teaching_publication_head_mismatch")
        return active
    except PublicationError:
        raise
    except Exception:
        raise PublicationError("teaching_publication_unavailable") from None


async def rollback_reviewed_publication(
    owner: BrowserOwner,
    target_digest: str,
    *,
    skill_version: str,
    site_version: str,
    draft_digest: str,
    replay_digest: str,
    authority: ReviewedPublicationAuthority,
) -> BrowserPublicationRecord:
    """Publish reviewed old content as a fresh version with a locked source."""
    try:
        owner = _owner_copy(owner)
        TypeAdapter(Digest).validate_python(target_digest, strict=True)
        TypeAdapter(Digest).validate_python(draft_digest, strict=True)
        TypeAdapter(Digest).validate_python(replay_digest, strict=True)
        target = await authority.store.get_frozen(owner, target_digest)
        if target is None or target.state != "inactive":
            raise PublicationError("teaching_rollback_target_unavailable")
        candidate = rollback_version_manifest(
            target.manifest, skill_version=skill_version,
            site_version=site_version,
        )
        current = await authority.store.get_active(owner, candidate.skill.skill_id)
        if current is None or current.manifest.digest == target_digest:
            raise PublicationError("teaching_rollback_head_invalid")
        fact = await _review_current(
            owner, candidate, draft_digest, replay_digest, authority, "rollback",
        )
        prepared = await authority.store.prepare(owner, candidate)
        if prepared.manifest != candidate or prepared.state != "prepared":
            raise PublicationError("teaching_rollback_prepare_mismatch")
        if await _review_current(
            owner, candidate, draft_digest, replay_digest, authority, "rollback",
        ) != fact:
            raise PublicationError("teaching_review_stale")
        result = await authority.store.activate_reviewed(
            owner, candidate, draft_digest=draft_digest,
            replay_digest=replay_digest, approval_ref=fact.approval_ref,
            reviewer_ref=fact.reviewer_ref, evidence_digest=fact.evidence_digest,
            expected_active_digest=current.manifest.digest,
        )
        if result.manifest != candidate or result.state != "active":
            raise PublicationError("teaching_rollback_mismatch")
        frozen = await authority.store.get_frozen(owner, target_digest)
        active = await authority.store.get_active(owner, candidate.skill.skill_id)
        if frozen is None or frozen.manifest != target.manifest or active != result:
            raise PublicationError("teaching_rollback_head_mismatch")
        return result
    except PublicationError:
        raise
    except Exception:
        raise PublicationError("teaching_rollback_unavailable") from None
