"""Independent browser ports. Implementations own resources and vendor protocols."""

from typing import Protocol

from app.browser_skill.models import (
    ActionCommand,
    BrowserCapabilities,
    BrowserSessionRef,
    BrowserSkill,
    DecisionCallContext,
    DecisionRequest,
    DecisionResult,
    DispatchReceipt,
    ExecutionContext,
    ObservationPolicy,
    ObservationRequest,
    ProfileRef,
    ReadEvidence,
    ReadSpec,
    ResourceOutcome,
    ScopeBinding,
    SessionStartResult,
    SkillStep,
    TargetRef,
    VerificationResult,
    VisibleCandidate,
    VisibleProjection,
)
from app.browser_skill.site_rules import RegisteredSitePlan


class BrowserProvider(Protocol):
    async def capabilities(self) -> BrowserCapabilities: ...
    async def acquire(self, binding: ScopeBinding) -> BrowserSessionRef:
        """Reserve an unlaunched handle; no remote browser is started yet."""
        ...

    async def restore(
        self,
        session: BrowserSessionRef,
        profile: ProfileRef | None,
    ) -> SessionStartResult:
        """Launch with immutable profile or isolated fresh context.

        None yields fresh login-assist-only state, never business authority.
        Profile restore verifies actual subject and current authorization before
        returning subject_verified. There is no post-connect restore assumption.
        """
        ...

    async def capture(self, session: BrowserSessionRef) -> ProfileRef:
        """Capture a NEW immutable generation, never overwrite an existing profile."""
        ...

    async def release(self, session: BrowserSessionRef) -> ResourceOutcome: ...
    async def terminate(self, session: BrowserSessionRef) -> ResourceOutcome: ...


class WebAdapter(Protocol):
    async def target_candidates(
        self,
        session: BrowserSessionRef,
        step: SkillStep,
        projection: VisibleProjection,
        context: ExecutionContext,
    ) -> tuple[VisibleCandidate, ...]:
        """Match the frozen locator against current private DOM facts.

        Authorize first, preserve actual ancestor epochs, and return an exact
        subset of the supplied approved projection. Resolve business-key inputs
        only through the sealed resolver; never project key/input values.
        """
        ...

    async def observe(
        self,
        session: BrowserSessionRef,
        request: ObservationRequest,
        policy: ObservationPolicy,
    ) -> VisibleProjection:
        """Resolve only registered regions; generate actual scope and reject stale preconditions.

        Current binding/phase and actual DOM refs come from the provider registry.
        Empty candidate lists never establish a trusted empty business result.
        """
        ...

    async def revalidate(
        self,
        session: BrowserSessionRef,
        command: ActionCommand,
        context: ExecutionContext,
    ) -> None:
        """Recheck current owner/binding/lease, exact node identity and actionability."""
        ...

    async def execute(
        self,
        session: BrowserSessionRef,
        command: ActionCommand,
        context: ExecutionContext,
    ) -> DispatchReceipt:
        """Independently recheck every authority/ref inside the dispatch barrier.

        Direct calls cannot bypass current authorization, frozen Skill/parameters,
        source/Site effect facts, cancellation, exact DOM identity or actionability.
        Navigation enforces registered exact origins on every redirect/popup hop.
        may_write is denied in M0/M1; unknown effects are never automatically replayed.
        Acknowledgment is not business success. Exceptions must be neutral failures.
        """
        ...

    async def option_candidates(
        self,
        session: BrowserSessionRef,
        target: TargetRef,
        step: SkillStep,
        context: ExecutionContext,
    ) -> tuple[VisibleCandidate, ...]:
        """Filter actual options by the Skill's sealed option_ref before any model call.

        Recheck current authorization and exact parent/option identity. Return only
        permitted option IDs and allowlisted labels; never option values. execute
        repeats the membership and parent check immediately before dispatch.
        """
        ...

    async def read(
        self,
        session: BrowserSessionRef,
        spec: ReadSpec,
        context: ExecutionContext,
    ) -> ReadEvidence:
        """Independent confirmed-key lookup under current owner and frozen verifier.

        Resolve key/expected inputs through the trusted sealed resolver. Never use
        the selected row, model output or test oracle as an expected result. Return
        only declared field comparisons, coverage and sanitized evidence.
        """
        ...


class SiteAdapter(Protocol):
    """Pure site rules; no resource acquisition, network or model invocation."""

    def bootstrap(self, skill: BrowserSkill) -> RegisteredSitePlan:
        """Resolve immutable registered steps/regions/criteria/effect/verifier facts."""
        ...

    def observation_policy(self, skill: BrowserSkill) -> ObservationPolicy: ...
    def permits(self, skill: BrowserSkill, command: ActionCommand) -> bool:
        """Require frozen Site effect evidence; the step's read_only label is insufficient."""
        ...


class DecisionProvider(Protocol):
    async def decide(
        self,
        request: DecisionRequest,
        context: DecisionCallContext,
    ) -> DecisionResult: ...


class BrowserVerifierPort(Protocol):
    async def verify(
        self,
        session: BrowserSessionRef,
        spec: ReadSpec,
        context: ExecutionContext,
    ) -> VerificationResult: ...


class BrowserSkillStorePort(Protocol):
    async def get_published(
        self,
        skill_id: str,
        version: str,
        digest: str,
    ) -> BrowserSkill | None:
        """Return only active immutable publications with matching dependency digests."""
        ...
