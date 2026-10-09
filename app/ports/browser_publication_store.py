"""Durable browser publications and trusted tenant operation/source authority."""

from typing import Literal, Protocol

from app.browser_skill.models import BrowserOwner
from app.browser_skill.publication_contracts import (
    BrowserPublicationManifest,
    BrowserPublicationRecord,
)

PublicationOperation = Literal[
    "prepare", "activate", "deactivate", "read", "execute", "teach",
]


class BrowserPublicationError(ValueError):
    """Fixed diagnostic codes; no source, manifest or owner values."""


class BrowserManifestAuthorityPort(Protocol):
    """Trusted injected authority; never implement from client DTO assertions.

    Both checks must be bounded current local/DB operations, with no provider or
    model IO. Verify the exact registered source, origins, projection, effect
    proofs, verifier and output implementation. Synthetic fixtures retain their
    exact seed restriction. Authorization must bind the authenticated service/user
    identity to this exact tenant and operation; identifiers alone do not grant.
    """

    async def authorize(
        self, owner: BrowserOwner, skill_id: str, operation: PublicationOperation
    ) -> bool: ...

    async def verify_manifest(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest
    ) -> bool: ...


class BrowserPublicationReviewPort(Protocol):
    """Re-read a stored, current review inside the publication switch transaction.

    The implementation must match owner, draft, replay Run, publication, Site,
    schema, reviewer, expiry and current approval authority. Caller-provided
    ``approved`` flags or a model verdict are never acceptable evidence.
    """

    async def check_in_session(
        self, session: object, owner: BrowserOwner,
        manifest: BrowserPublicationManifest, *, draft_digest: str,
        replay_digest: str, approval_ref: str, reviewer_ref: str,
        evidence_digest: str,
    ) -> bool: ...

    async def check_initial_in_session(
        self, session: object, owner: BrowserOwner,
        manifest: BrowserPublicationManifest, *, task_id: str,
        run_id: str, approval_ref: str,
    ) -> bool:
        """Require one actual verified prepared Run and its HumanGate decision."""
        ...


class BrowserPublicationStorePort(Protocol):
    async def prepare(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest
    ) -> BrowserPublicationRecord: ...

    async def activate(
        self, owner: BrowserOwner, skill_id: str, publication_digest: str,
        *, expected_revision: int = 0,
    ) -> BrowserPublicationRecord: ...

    async def activate_reviewed(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest, *,
        draft_digest: str, replay_digest: str, approval_ref: str,
        reviewer_ref: str, evidence_digest: str,
        expected_active_digest: str | None = None,
    ) -> BrowserPublicationRecord: ...

    async def activate_initial(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest, *,
        task_id: str, run_id: str, approval_ref: str,
    ) -> BrowserPublicationRecord: ...

    async def deactivate(
        self, owner: BrowserOwner, skill_id: str, publication_digest: str,
        *, expected_revision: int,
    ) -> BrowserPublicationRecord: ...

    async def get_active(
        self, owner: BrowserOwner, skill_id: str
    ) -> BrowserPublicationRecord | None: ...

    async def get_frozen(
        self, owner: BrowserOwner, publication_digest: str
    ) -> BrowserPublicationRecord | None: ...

    async def assert_current(
        self, owner: BrowserOwner, manifest: BrowserPublicationManifest
    ) -> None:
        """Recheck before admission/action; never grants Run/binding authority."""
        ...
