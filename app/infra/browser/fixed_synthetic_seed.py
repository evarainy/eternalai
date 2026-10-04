"""Code-owned local synthetic source, exact seed and explicit dependency installer.

This registers static source content, not a new browser provider or a deployment
acceptance proof. A real injected factory must serve these exact bytes and enforce
source isolation, SUBJECT, lease, dispatch and cleanup authority. Nothing here
starts a server/browser, creates a Capability or automatically publishes a seed.
"""

from __future__ import annotations

import hashlib
import html
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Any, TypeVar

from pydantic import BaseModel, Field

from app.browser_skill.models import (
    BrowserSkill,
    Contract,
    DecisionBudget,
    DecisionSource,
    LocatorHint,
    ModelManifest,
    ObservationPolicy,
    ObservationRequest,
    ParameterRef,
    SkillStep,
)
from app.browser_skill.publication_contracts import (
    BrowserOutputDefinition,
    BrowserPublicationManifest,
    BrowserSiteDefinition,
    BrowserVerifierDefinition,
    canonical_json,
    publication_digest,
)
from app.browser_skill.site_rules import (
    EffectFact,
    ExpectedField,
    FrozenSiteAdapter,
    QueryField,
    RegisteredQueryReadRule,
    RegisteredReadRule,
    SiteStepRule,
)
from app.infra.adapters.oa.capabilities import expected_oa_capabilities
from app.infra.adapters.oa.contracts import OASystemMessageCollection
from app.infra.browser.composition import (
    BrowserVerticalComponents,
    BrowserVerticalDependencies,
)
from app.infra.browser.playwright_dom_rules import DOMRead, DOMStep, DOMValue, RegisteredDOMRules
from app.infra.browser.playwright_observer import RegisteredRegion
from app.infra.browser.read_execution import BrowserReadExecutionFactory, RegisteredReadExecution
from app.ports.browser_publication_store import BrowserPublicationError
from app.ports.browser_read_execution import BrowserReadExecutionError, BrowserWorkerCheckpoint
from app.ports.browser_run_store import RunSnapshot
from app.ports.capability_registry import CapabilitySpec
from app.ports.llm_provider import LLMProviderPort
from app.ports.response_projection_contract import canonical_schema_digest
from app.ports.structured_output import StructuredOutputPort
from app.version_binding import capability_version_bindings

SYNTHETIC_ORIGIN = "http://127.0.0.1:8765"
SYNTHETIC_TENANT = "browser_fixture_tenant"
SYNTHETIC_USER = "browser_fixture_user"
SYNTHETIC_KEY = "fixture_system_messages"
SYNTHETIC_DETAIL_CAPABILITY_ID = "browser.synthetic.system_message_detail"
DIAGNOSTIC_SKILL_VERSION = "v2_diagnostic_2"
VISIBLE_QUERY_SKILL_VERSION = "v3_visible_complete"

_COLLECTION = canonical_json({
    "messages": [{
        "message_id": "fixture_message_1", "title": "Synthetic message",
        "content": "Static browser verification fixture", "source_name": "Synthetic source",
        "occurred_at": "2026-01-01T00:00:00Z", "business_state": "unread",
        "link": None, "mobile_link": None,
    }],
    "returned_count": 1, "is_complete": True,
})
# There is no script, form, external asset, event handler or navigation target.
# Owner/key/collection are synthetic fixture content, never actual credentials.
_HTML = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Synthetic messages</title>'
    '</head><body><section id="fixture-region"><div id="fixture-row" '
    'role="row" aria-label="Synthetic messages" data-testid="fixture_row">'
    f'<span id="fixture-key">{SYNTHETIC_KEY}</span>'
    f'<span id="fixture-tenant">{SYNTHETIC_TENANT}</span>'
    f'<span id="fixture-user">{SYNTHETIC_USER}</span>'
    f'<span id="fixture-collection">{html.escape(_COLLECTION)}</span>'
    '</div><span id="complete" aria-hidden="true">Complete</span></section></body></html>'
).encode("utf-8")
_QUERY_HTML = _HTML.replace(
    b'<span id="fixture-key">',
    b'<span id="fixture-object-type">system_message_collection</span><span id="fixture-key">',
).replace(
    b'<span id="complete" aria-hidden="true">',
    b'<div id="fixture-alternative" role="row" aria-label="Other synthetic row" '
    b'data-testid="fixture_row"><span>Other synthetic row</span></div>'
    b'<span id="complete" aria-hidden="true">',
)
_T = TypeVar("_T", bound=BaseModel)


class SyntheticDetailArguments(Contract):
    """Admitted lookup key only; output and ownership are never input arguments."""

    business_key: Annotated[
        str, Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
    ]


def synthetic_detail_capability_snapshot() -> CapabilitySpec:
    """Code-owned desired snapshot, NOT a registration or execution authority.

    The existing PostgreSQL registry must independently contain this exact active
    snapshot before the publication store permits preparation/activation/admission.
    Canonical zero-argument OA capabilities are neither changed nor shadowed.
    """
    input_schema = SyntheticDetailArguments.model_json_schema()
    output_schema = expected_oa_capabilities()[1].output_schema
    return CapabilitySpec(
        capability_id=SYNTHETIC_DETAIL_CAPABILITY_ID, name="Synthetic message detail",
        type="query", intent_tags=["browser.synthetic.message_detail"],
        input_schema=input_schema, output_schema=output_schema,
        input_schema_digest=canonical_schema_digest(input_schema),
        output_schema_digest=canonical_schema_digest(output_schema),
        risk_level="low", owner="eternalai-platform", version="1.0.0", status="active",
        short_description="Read a synthetic message collection by its explicitly supplied key.",
        target_system="oa", execution_identity="user_delegated", binding_required=True,
        policy_digest=None,
    )


def _signed(model: type[_T], domain: str, **values: Any) -> _T:
    """Include every schema default before hashing, then perform real validation."""
    draft = model.model_construct(digest="0" * 64, **values)
    payload = draft.model_dump(mode="json")
    payload["digest"] = publication_digest(domain, payload)
    return model.model_validate_json(canonical_json(payload))


class FixedSyntheticOutputProjector:
    """Project actual verified DOM data; never return the known fixture as an oracle."""

    def __call__(self, fields: Mapping[str, str]) -> Mapping[str, object]:
        if tuple(fields) != ("collection",) or type(fields["collection"]) is not str:
            raise BrowserReadExecutionError("invalid_response")
        encoded = fields["collection"]
        if len(encoded.encode("utf-8")) > 8192:
            raise BrowserReadExecutionError("invalid_response")
        try:
            actual = OASystemMessageCollection.model_validate_json(encoded)
        except Exception:
            raise BrowserReadExecutionError("invalid_response") from None
        return actual.model_dump(mode="json")


@dataclass(frozen=True, slots=True, repr=False)
class FixedSyntheticSource:
    manifest: BrowserPublicationManifest
    html: bytes
    region: RegisteredRegion
    rules: RegisteredDOMRules
    site: FrozenSiteAdapter
    projector: FixedSyntheticOutputProjector

    @property
    def fixture_reference_values(self) -> Mapping[str, str]:
        """Reference data only, NOT independently admitted expected parameters.

        Current zero-argument OA queries do not admit these expected-output values.
        A separate approved query-verification contract is required before they
        could participate in execution. Never use this mapping as a resolver or
        populate it from observed output. Publication activation stays blocked.
        """
        if isinstance(self.manifest.site.read_rule, RegisteredQueryReadRule):
            raise BrowserPublicationError("browser_query_fixture_expected_values_forbidden")
        return MappingProxyType({
            "fixture_business_key": SYNTHETIC_KEY, "fixture_collection": _COLLECTION,
        })


def build_fixed_synthetic_source(decision_manifest: ModelManifest) -> FixedSyntheticSource:
    """Legacy expected-value source; never activates for the zero-argument OA query."""
    return _build_fixed_source(decision_manifest, query=False)


def build_fixed_synthetic_query_source(decision_manifest: ModelManifest) -> FixedSyntheticSource:
    """Key-detail source with no expected-result oracle; local deployment stays off."""
    return _build_fixed_source(decision_manifest, query=True)


def build_fixed_synthetic_diagnostic_source(
    decision_manifest: ModelManifest,
) -> FixedSyntheticSource:
    """The one approved second publication; identical source, Capability and permissions."""
    return _build_fixed_source(decision_manifest, query=True, diagnostic=True)


def build_fixed_synthetic_visible_query_source(
    decision_manifest: ModelManifest,
) -> FixedSyntheticSource:
    """Visible completion source; execution still requires publication and trial authority."""
    return _build_fixed_source(decision_manifest, query=True, visible_complete=True)


def _build_fixed_source(decision_manifest: ModelManifest, *, query: bool,
                        diagnostic: bool = False,
                        visible_complete: bool = False) -> FixedSyntheticSource:
    if (type(diagnostic) is not bool or type(visible_complete) is not bool
            or ((diagnostic or visible_complete) and not query)
            or (diagnostic and visible_complete)):
        raise ValueError("browser_local_source_invalid")
    capability = synthetic_detail_capability_snapshot() if query else expected_oa_capabilities()[1]
    content = _QUERY_HTML if query else _HTML
    if visible_complete:
        hidden_marker = b'<span id="complete" aria-hidden="true">'
        if content.count(hidden_marker) != 1:
            raise ValueError("browser_local_source_invalid")
        content = content.replace(hidden_marker, b'<span id="complete">', 1)
    fixture_digest = hashlib.sha256(content).hexdigest()
    policy = _signed(
        ObservationPolicy, "browser_observation_policy.v1", policy_id="fixture_projection",
        allowed_names=("Synthetic messages", "Other synthetic row") if query else (
            "Synthetic messages",
        ),
        allowed_roles=("row",), maximum_candidates=2 if query else 1,
    )
    step = SkillStep(
        step_id="read_collection", operation="read", effect="read_only",
        locator=LocatorHint(kind="test_id", value="fixture_row"),
    )
    observation = ObservationRequest(region_id="fixture_messages")
    read: RegisteredReadRule | RegisteredQueryReadRule
    if query:
        read = RegisteredQueryReadRule(
            object_type="system_message_collection", key_ref=ParameterRef(name="business_key"),
            fields=(QueryField(field_id="collection"),),
            output_schema_json=canonical_json(capability.output_schema), maximum_result_bytes=8192,
        )
    else:
        read = RegisteredReadRule(
            object_type="system_message_collection",
            key_ref=ParameterRef(name="fixture_business_key"),
            fields=(ExpectedField(field_id="collection", value_ref=ParameterRef(
                name="fixture_collection")),),
        )
    verifier = _signed(
        BrowserVerifierDefinition, "browser_verifier.publication.v1",
        verifier_id="fixture_collection_verifier", version="v1", read_rule=read,
        contract="independent_query_detail_v1" if query else "independent_confirmed_key_v1",
    )
    site = _signed(
        BrowserSiteDefinition, "browser_site.publication.v1",
        site_id="fixed_synthetic_message_detail" if query else "fixed_synthetic_messages",
        version="v1",
        source=DecisionSource(source_id="fixed_synthetic_messages",
                              origin=SYNTHETIC_ORIGIN, fixture_digest=fixture_digest),
        navigation_origins=(SYNTHETIC_ORIGIN,), policy=policy,
        steps=(SiteStepRule(step=step, observation=observation,
                           target_criteria=("Select the row named Synthetic messages",)
                           if query else ("Read the synthetic messages row",),
                           effect=EffectFact(
                               operation="read", actual_effect="read_only",
                               proof_ref="static_html_read_only_v1", evidence_digest=fixture_digest,
                           )),),
        read_rule=read, decision_manifest=decision_manifest, decision_budget=DecisionBudget(),
    )
    skill = _signed(
        BrowserSkill, "browser_skill.v1",
        skill_id="fixed_synthetic_message_detail" if query else "fixed_synthetic_messages",
        version=(VISIBLE_QUERY_SKILL_VERSION if visible_complete
                 else DIAGNOSTIC_SKILL_VERSION if diagnostic else "v1"),
        site_id=site.site_id, site_digest=site.digest,
        verifier_id=verifier.verifier_id, verifier_digest=verifier.digest,
        parameters=("business_key",) if query else ("fixture_business_key", "fixture_collection"),
        steps=(step,),
    )
    output = _signed(
        BrowserOutputDefinition, "browser_output.publication.v1",
        output_schema_json=canonical_json(capability.output_schema), field_ids=("collection",),
        maximum_bytes=8192,
    )
    manifest = _signed(
        BrowserPublicationManifest, "browser_publication.durable.v1",
        skill=skill, site=site, verifier=verifier, output=output,
        capability_snapshot_json=canonical_json(capability.model_dump(mode="json")),
        capability_bindings=capability_version_bindings(capability),
    )
    region = RegisteredRegion(
        region_id="fixture_messages", policy_id=policy.policy_id, policy_digest=policy.digest,
        region_selector="#fixture-region", complete_selector="#complete",
    )
    rules = RegisteredDOMRules(
        skill_digest=skill.digest, site_digest=site.digest, verifier_digest=verifier.digest,
        steps=(DOMStep(step_id=step.step_id, selector='[data-testid="fixture_row"]')
               if query else DOMStep(step_id=step.step_id, selector="#fixture-row"),),
        read=DOMRead(observation=observation, row_selector="#fixture-row",
                     key=DOMValue("#fixture-key"), tenant=DOMValue("#fixture-tenant"),
                     user=DOMValue("#fixture-user"),
                     fields=(("collection", DOMValue("#fixture-collection")),),
                     maximum_rows=1, maximum_value_bytes=8192,
                     object_type=DOMValue("#fixture-object-type") if query else None),
    )
    return FixedSyntheticSource(
        manifest, content, region, rules, FrozenSiteAdapter((manifest.site_plan(),)),
        FixedSyntheticOutputProjector(),
    )


class FixedSourceReadFactory:
    """Enforce exact static installation on an independently authorized real factory.

    The delegate still owns serving exact source bytes, provider deployment proof,
    live SUBJECT, protected parameters, lease/cancellation and restore guarantees.
    This wrapper cannot turn an absent or unsupported provider into an accepted one.
    """

    def __init__(self, delegate: BrowserReadExecutionFactory, source: FixedSyntheticSource) -> None:
        if not callable(getattr(delegate, "open", None)):
            raise ValueError("browser_real_execution_factory_required")
        self._delegate, self.source = delegate, source

    async def open(
        self, run: RunSnapshot, manifest: BrowserPublicationManifest,
        private_input: Mapping[str, object], checkpoint: BrowserWorkerCheckpoint,
    ) -> RegisteredReadExecution:
        if (manifest != self.source.manifest or run.owner.tenant_id != SYNTHETIC_TENANT
                or run.owner.user_id != SYNTHETIC_USER
                or private_input.get("capability_id") != manifest.capability.capability_id):
            raise BrowserReadExecutionError("denied")
        if not isinstance(manifest.site.read_rule, RegisteredQueryReadRule):
            raise BrowserReadExecutionError("unsupported")
        try:
            SyntheticDetailArguments.model_validate(private_input.get("arguments"))
        except Exception:
            raise BrowserReadExecutionError("denied") from None
        # This validates shape only. The real delegate must bind this exact
        # protected admitted key through ReadSpecResolver/SealedParameter and
        # independently recheck its confirmation. A model/string is no authority.
        execution = await self._delegate.open(run, manifest, private_input, checkpoint)
        await checkpoint.refresh()
        if (execution.rules != (self.source.rules,)
                or execution.site.bootstrap(manifest.skill) != manifest.site_plan()
                or execution.observer._regions != {self.source.region.region_id: self.source.region}
                or execution.project_output is not self.source.projector
                or execution.context.source != manifest.site.source
                or execution.context.navigation_origins != (SYNTHETIC_ORIGIN,)):
            raise BrowserReadExecutionError("denied")
        return execution


class FixedSyntheticSourceVerifier:
    """Check code registration without inferring approved provider placement.

    Legacy zero-argument execution still lacks expected-input authority. The
    query-detail route additionally requires the concrete controlled local factory
    and its installed durable authority. Matching bytes grants no user permission.
    """

    def __init__(self, factory: FixedSourceReadFactory) -> None:
        if type(factory) is not FixedSourceReadFactory:
            raise ValueError("browser_source_factory_required")
        self._factory = factory

    async def verify(self, manifest: BrowserPublicationManifest) -> bool:
        source = self._factory.source
        query = isinstance(manifest.site.read_rule, RegisteredQueryReadRule)
        expected = (build_fixed_synthetic_visible_query_source(manifest.site.decision_manifest)
                    if manifest.skill.version == VISIBLE_QUERY_SKILL_VERSION
                    else _build_fixed_source(
                        manifest.site.decision_manifest, query=query,
                        diagnostic=manifest.skill.version == DIAGNOSTIC_SKILL_VERSION,
                    ))
        registered = (
            type(source) is FixedSyntheticSource
            and manifest == expected.manifest == source.manifest
            and source.html == expected.html
            and hashlib.sha256(source.html).hexdigest() == manifest.site.source.fixture_digest
            and source.rules == expected.rules and source.region == expected.region
            and source.site.bootstrap(manifest.skill) == manifest.site_plan()
            and type(source.projector) is FixedSyntheticOutputProjector
        )
        if not registered:
            return False
        if not query:
            raise BrowserPublicationError("browser_fixed_seed_expected_input_contract_unapproved")
        # Import only here to keep source contracts independent of construction
        # order. Arbitrary delegates and cloud factories cannot certify localhost.
        from app.infra.browser.local_read_execution import LocalBrowserReadExecutionFactory

        delegate = self._factory._delegate
        if type(delegate) is not LocalBrowserReadExecutionFactory:
            raise BrowserPublicationError("browser_fixed_seed_source_placement_unapproved")
        return await delegate.verify_source(manifest) is True


def install_fixed_synthetic_seed(
    dependencies: BrowserVerticalDependencies, *, source: FixedSyntheticSource,
    llm_provider: LLMProviderPort, structured_output: StructuredOutputPort,
    intent_model: str, enabled: bool = False,
) -> BrowserVerticalComponents | None:
    """Refuse enablement before assembling or advertising any service.

    Source builders are independently reviewable. Query-detail installation waits
    for approved source placement; legacy installation also lacks an approved
    expected-input contract. Dependencies and flags cannot discharge either gate.
    """
    if type(enabled) is not bool:
        raise ValueError("browser_seed_enablement_invalid")
    if not enabled:
        return None
    if isinstance(source.manifest.site.read_rule, RegisteredQueryReadRule):
        raise BrowserPublicationError("browser_fixed_seed_source_placement_unapproved")
    raise BrowserPublicationError("browser_fixed_seed_expected_input_contract_unapproved")
