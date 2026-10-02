"""Synthetic publication contract and a process-local reference authority.

This is not the BR-DB-02 durable implementation or a CapabilityRegistry adapter.
Its owner-scoped heads simulate isolation, not the final tenant-level DB heads.
No file is auto-published. Production Site/Verifier implementations, real login/
MFA, registered public mock sources and catalog integration remain deferred.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import Lock
from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.browser_skill.models import BrowserOwner, BrowserSkill, Contract, Digest, Epoch, OpaqueId

Operation = Literal["prepare", "activate", "rollback", "read"]
OwnerKey = tuple[str, str, str]
Authorize = Callable[[BrowserOwner, str, Operation], bool]


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
