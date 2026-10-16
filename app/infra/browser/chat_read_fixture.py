"""Two task-owned records, frozen DOM rules and independent query ledger.

Only construction reads the approved HTML template. Nothing starts at import.
The resolver uses admitted input; only the post-verifier projector reads ledger.
"""

from __future__ import annotations

import hashlib
import html
import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, TypeVar
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel

from app.browser_skill.models import (
    BrowserOwner,
    BrowserSessionRef,
    BrowserSkill,
    ConfirmedBusinessKey,
    DecisionBudget,
    DecisionSource,
    ExecutionContext,
    LocatorHint,
    ModelManifest,
    ObservationPolicy,
    ObservationRequest,
    ParameterRef,
    ReadSpec,
    ScopeBinding,
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
    FrozenSiteAdapter,
    QueryField,
    RegisteredQueryReadRule,
    SiteStepRule,
    canonical_origin,
)
from app.browser_skill.verifier import ReadSpecResolver
from app.infra.browser.playwright_dom_rules import DOMRead, DOMStep, DOMValue, RegisteredDOMRules
from app.infra.browser.playwright_observer import RegisteredRegion
from app.infra.browser.read_execution import BrowserOutputProjector
from app.ports.browser_read_execution import BrowserReadExecutionError
from app.ports.browser_run_store import RunSnapshot
from app.ports.capability_registry import CapabilitySpec
from app.ports.response_projection_contract import canonical_schema_digest
from app.version_binding import capability_version_bindings

CHAT_READ_TENANT = "chat_read_fixture_tenant"
CHAT_READ_USER = "chat_read_fixture_user"
CHAT_READ_CAPABILITY_ID = "browser.synthetic.chat_record_detail"
CHAT_READ_HTML = (
    Path(__file__).resolve().parents[3] / "tests/fixtures/browser/chat-read-only/query.html"
)
FIELD_IDS = ("code", "period", "status", "count", "reference")
_RECORDS = MappingProxyType(
    {
        "DEMO-731": ("DEMO-731", "2026-09", "approved", "1", "REF-731"),
        "DEMO-842": ("DEMO-842", "2026-10", "pending", "2", "REF-842"),
    }
)
_T = TypeVar("_T", bound=BaseModel)


def _signed(model: type[_T], domain: str, **values: Any) -> _T:
    draft = model.model_construct(digest="0" * 64, **values)
    payload = draft.model_dump(mode="json")
    payload["digest"] = publication_digest(domain, payload)
    return model.model_validate_json(canonical_json(payload))


def admitted_arguments(
    private_input: Mapping[str, object], manifest: BrowserPublicationManifest
) -> dict[str, str]:
    arguments = private_input.get("arguments")
    if (
        private_input.get("schema_version") != "browser.request.input.v2"
        or private_input.get("capability_id") != manifest.capability.capability_id
        or type(arguments) is not dict
        or set(arguments) != {"business_key"}
        or type(arguments["business_key"]) is not str
        or arguments["business_key"] not in _RECORDS
    ):
        raise BrowserReadExecutionError("denied")
    key = arguments["business_key"]
    return {"business_key": key, "code": _RECORDS[key][0], "period": _RECORDS[key][1]}


@dataclass(frozen=True, slots=True, repr=False)
class ChatReadFixture:
    manifest: BrowserPublicationManifest
    template: bytes
    region: RegisteredRegion
    rules: RegisteredDOMRules
    site: FrozenSiteAdapter


def build_chat_read_fixture(
    *, origin: str, html_path: Path, decision_manifest: ModelManifest
) -> ChatReadFixture:
    canonical_origin(origin)
    parsed = urlsplit(origin)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port is None:
        raise ValueError("chat_read_origin_invalid")
    if html_path.resolve() != CHAT_READ_HTML.resolve():
        raise ValueError("chat_read_template_path_denied")
    template = html_path.read_bytes()
    if (
        not 1 <= len(template) <= 16384
        or template.count(b"{{RESULT}}") != 1
        or template.count(b"{{QUERY_SEQUENCE}}") != 1
    ):
        raise ValueError("chat_read_template_invalid")
    source_digest = hashlib.sha256(template).hexdigest()
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["business_key"],
        "properties": {"business_key": {"type": "string", "enum": list(_RECORDS)}},
    }
    output_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": list(FIELD_IDS),
        "properties": {
            "code": {"type": "string", "enum": list(_RECORDS)},
            "period": {"type": "string", "pattern": "^2026-(09|10)$"},
            "status": {"type": "string", "enum": ["approved", "pending"]},
            "count": {"type": "integer", "minimum": 1, "maximum": 2},
            "reference": {"type": "string", "enum": ["REF-731", "REF-842"]},
        },
    }
    capability = CapabilitySpec(
        capability_id=CHAT_READ_CAPABILITY_ID,
        name="Fictional record detail",
        type="query",
        intent_tags=["browser.synthetic.chat_record_detail"],
        input_schema=input_schema,
        output_schema=output_schema,
        input_schema_digest=canonical_schema_digest(input_schema),
        output_schema_digest=canonical_schema_digest(output_schema),
        risk_level="low",
        owner="eternalai-platform",
        version="1.0.0",
        status="active",
        target_system="oa",
        short_description=(
            "按明确业务键 DEMO-731（2026-09）或 DEMO-842（2026-10）只读查询虚构记录。"
        ),
        execution_identity="user_delegated",
        binding_required=True,
        policy_digest=None,
    )
    policy = _signed(
        ObservationPolicy,
        "browser_observation_policy.v1",
        policy_id="chat_read_projection",
        allowed_names=("Query code", "Query period", "Run query", "Query result"),
        allowed_roles=("textbox", "button", "row"),
        maximum_candidates=8,
    )
    observation = ObservationRequest(region_id="chat_read_query")
    definitions: tuple[
        tuple[str, Literal["navigate", "fill", "click", "read"], str, str | None], ...
    ] = (
        ("navigate_query", "navigate", "query-form", "query_url"),
        ("fill_code", "fill", "query-code", "code"),
        ("fill_period", "fill", "query-period", "period"),
        ("submit_query", "click", "query-submit", None),
        ("read_result", "read", "query-result", None),
    )
    steps = tuple(
        SkillStep(
            step_id=step_id,
            operation=operation,
            effect="read_only",
            locator=LocatorHint(kind="test_id", value=target),
            value_ref=ParameterRef(name=parameter) if operation == "fill" and parameter else None,
            url_ref=ParameterRef(name=parameter) if operation == "navigate" and parameter else None,
        )
        for step_id, operation, target, parameter in definitions
    )
    read = RegisteredQueryReadRule(
        object_type="chat_read_record",
        key_ref=ParameterRef(name="business_key"),
        fields=tuple(QueryField(field_id=name) for name in FIELD_IDS),
        output_schema_json=canonical_json(output_schema),
        maximum_result_bytes=2048,
    )
    verifier = _signed(
        BrowserVerifierDefinition,
        "browser_verifier.publication.v1",
        verifier_id="chat_read_verifier",
        version="v1",
        read_rule=read,
        contract="independent_query_detail_v1",
    )
    site = _signed(
        BrowserSiteDefinition,
        "browser_site.publication.v1",
        site_id="chat_read_fixture",
        version="v1",
        source=DecisionSource(
            source_id="chat_read_fixture", origin=origin, fixture_digest=source_digest
        ),
        navigation_origins=(origin,),
        policy=policy,
        steps=tuple(
            SiteStepRule(
                step=step,
                observation=observation,
                target_criteria=(step.locator.value,),
                effect=EffectFact(
                    operation=step.operation,
                    actual_effect="read_only",
                    proof_ref="task_owned_query_handler_v1",
                    evidence_digest=source_digest,
                ),
            )
            for step in steps
        ),
        read_rule=read,
        decision_manifest=decision_manifest,
        decision_budget=DecisionBudget(max_candidates=8),
    )
    skill = _signed(
        BrowserSkill,
        "browser_skill.v1",
        skill_id="chat_read_fixture",
        version="v1",
        site_id=site.site_id,
        site_digest=site.digest,
        verifier_id=verifier.verifier_id,
        verifier_digest=verifier.digest,
        parameters=("business_key", "code", "period", "query_url"),
        steps=steps,
    )
    output = _signed(
        BrowserOutputDefinition,
        "browser_output.publication.v1",
        field_ids=FIELD_IDS,
        output_schema_json=canonical_json(output_schema),
        maximum_bytes=2048,
    )
    manifest = _signed(
        BrowserPublicationManifest,
        "browser_publication.durable.v1",
        skill=skill,
        site=site,
        verifier=verifier,
        output=output,
        capability_snapshot_json=canonical_json(capability.model_dump(mode="json")),
        capability_bindings=capability_version_bindings(capability),
    )
    region = RegisteredRegion(
        "chat_read_query",
        policy.policy_id,
        policy.digest,
        "#query-region",
        complete_selector="#projection-complete",
    )
    selectors = ("form", "#code", "#period", '[data-testid="query-submit"]', "#result-row")
    rules = RegisteredDOMRules(
        skill.digest,
        site.digest,
        verifier.digest,
        tuple(
            DOMStep(step.step_id, selector) for step, selector in zip(steps, selectors, strict=True)
        ),
        DOMRead(
            observation,
            "#result-row",
            DOMValue("#record-key"),
            DOMValue("#record-tenant"),
            DOMValue("#record-user"),
            tuple((name, DOMValue("#value-" + name)) for name in FIELD_IDS),
            maximum_rows=1,
            maximum_value_bytes=2048,
            object_type=DOMValue("#record-type"),
        ),
    )
    return ChatReadFixture(
        manifest, template, region, rules, FrozenSiteAdapter((manifest.site_plan(),))
    )


def make_read_spec_resolver(
    run: RunSnapshot,
    admitted_input: Mapping[str, object],
    binding: ScopeBinding,
    manifest: BrowserPublicationManifest,
) -> ReadSpecResolver:
    arguments = admitted_arguments(admitted_input, manifest)
    key = ConfirmedBusinessKey(
        confirmation_ref=run.run_id,
        object_type=manifest.site.read_rule.object_type,
        key_digest=hashlib.sha256(
            run.admission.input_digest + arguments["business_key"].encode()
        ).hexdigest(),
        value_ref=manifest.site.read_rule.key_ref,
    )
    spec = ReadSpec(
        binding=binding,
        business_key=key,
        verifier_id=manifest.verifier.verifier_id,
        verifier_digest=manifest.verifier.digest,
        fields=FIELD_IDS,
        mode="independent_query_detail_v1",
    )

    expected_binding = binding

    async def resolve(
        session: BrowserSessionRef, context: ExecutionContext, binding: ScopeBinding
    ) -> ReadSpec:
        if (
            expected_binding.owner != run.owner
            or expected_binding.authorization_run_id != run.run_id
            or expected_binding.binding_id != run.admission.binding_id
            or expected_binding.binding_revision != run.admission.binding_revision
            or expected_binding.lease_epoch != run.lease_epoch
            or expected_binding.evidence_version != "verified-session-v1"
            or session.binding != expected_binding
            or binding != expected_binding
            or context.expected_binding != expected_binding
            or context.skill != manifest.skill
            or context.source != manifest.site.source
            or run.admission.publication_digest.hex() != manifest.digest
        ):
            raise BrowserReadExecutionError("denied")
        return spec

    return resolve


@dataclass(frozen=True, slots=True, repr=False)
class QueryBinding:
    owner: BrowserOwner
    task_id: str
    run_id: str
    request_id: str
    input_digest: bytes
    publication_digest: bytes
    business_key: str
    code: str
    period: str
    query_sequence: int


@dataclass(frozen=True, slots=True, repr=False)
class QuerySnapshot:
    binding: QueryBinding
    values: tuple[str, ...]


class QueryLedger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sequence = 0
        self._bindings: dict[str, QueryBinding] = {}
        self._queries: dict[str, QuerySnapshot] = {}

    def bind_run(
        self, run: RunSnapshot, request_id: str, arguments: Mapping[str, str]
    ) -> QueryBinding:
        with self._lock:
            if run.run_id in self._bindings or not request_id or len(self._bindings) >= 128:
                raise BrowserReadExecutionError("stale")
            self._sequence += 1
            binding = QueryBinding(
                run.owner,
                run.task_id,
                run.run_id,
                request_id,
                run.admission.input_digest,
                run.admission.publication_digest,
                arguments["business_key"],
                arguments["code"],
                arguments["period"],
                self._sequence,
            )
            self._bindings[run.run_id] = binding
            return binding

    def record_query(self, binding: QueryBinding, params: Mapping[str, list[str]]) -> QuerySnapshot:
        with self._lock:
            if (
                self._bindings.get(binding.run_id) != binding
                or binding.run_id in self._queries
                or dict(params)
                != {
                    "code": [binding.code],
                    "period": [binding.period],
                    "query_sequence": [str(binding.query_sequence)],
                }
            ):
                raise BrowserReadExecutionError("invalid_response")
            values = _RECORDS.get(binding.business_key)
            if values is None or values[:2] != (binding.code, binding.period):
                raise BrowserReadExecutionError("invalid_response")
            snapshot = QuerySnapshot(binding, values)
            self._queries[binding.run_id] = snapshot
            return snapshot

    def snapshot_for_run(self, binding: QueryBinding) -> QuerySnapshot:
        with self._lock:
            snapshot = self._queries.get(binding.run_id)
            if snapshot is None or snapshot.binding != binding:
                raise BrowserReadExecutionError("invalid_response")
            return QuerySnapshot(snapshot.binding, tuple(snapshot.values))


def make_project_output(
    run: RunSnapshot,
    admitted_input: Mapping[str, object],
    query_binding: QueryBinding,
    ledger_reader: QueryLedger,
    manifest: BrowserPublicationManifest,
) -> BrowserOutputProjector:
    arguments = admitted_arguments(admitted_input, manifest)

    def project(fields: Mapping[str, str]) -> Mapping[str, object]:
        binding = query_binding
        if (
            tuple(fields) != FIELD_IDS
            or binding.owner != run.owner
            or binding.task_id != run.task_id
            or binding.run_id != run.run_id
            or binding.input_digest != run.admission.input_digest
            or binding.publication_digest != run.admission.publication_digest
            or binding.business_key != arguments["business_key"]
            or (binding.code, binding.period) != (arguments["code"], arguments["period"])
        ):
            raise BrowserReadExecutionError("invalid_response")
        snapshot = ledger_reader.snapshot_for_run(binding)
        if tuple(fields[name] for name in FIELD_IDS) != snapshot.values:
            raise BrowserReadExecutionError("invalid_response")
        return {
            **{name: fields[name] for name in FIELD_IDS if name != "count"},
            "count": int(fields["count"]),
        }

    return project


class ChatReadHTTPFixture:
    """A server for exactly one Run; no credentials, external assets or upload handler."""

    def __init__(self, source: ChatReadFixture, ledger: QueryLedger, binding: QueryBinding) -> None:
        self.source, self.ledger, self.binding = source, ledger, binding
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.request_threads: set[threading.Thread] = set()
        self.requests: set[Any] = set()
        self.thread_lock = threading.Lock()
        self.closing = False

    def start(self) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_: Any) -> None:
                return

            def reply(self, result: QuerySnapshot | None = None, status: int = 200) -> None:
                block = ""
                if result is not None:
                    values = "".join(
                        '<span id="value-' + name + '">' + html.escape(value) + "</span>"
                        for name, value in zip(FIELD_IDS, result.values, strict=True)
                    )
                    block = (
                        '<div id="result-row" role="row" aria-label="Query result" '
                        'data-testid="query-result">'
                        '<span id="record-key">'
                        + html.escape(owner.binding.business_key)
                        + "</span>"
                        '<span id="record-type">chat_read_record</span><span id="record-tenant">'
                        + html.escape(owner.binding.owner.tenant_id)
                        + '</span><span id="record-user">'
                        + html.escape(owner.binding.owner.user_id)
                        + "</span>"
                        + values
                        + "</div>"
                    )
                body = owner.source.template.replace(b"{{RESULT}}", block.encode()).replace(
                    b"{{QUERY_SEQUENCE}}", str(owner.binding.query_sequence).encode()
                )
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; form-action 'self'; "
                    "base-uri 'none'; frame-ancestors 'none'",
                )
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if (
                    self.path not in {"/", "/query"}
                    or self.headers.get("Host")
                    != urlsplit(owner.source.manifest.site.source.origin).netloc
                ):
                    self.reply(status=404)
                    return
                self.reply()

            def do_POST(self) -> None:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if (
                        self.path != "/result"
                        or not 1 <= length <= 512
                        or self.headers.get("Host")
                        != urlsplit(owner.source.manifest.site.source.origin).netloc
                        or self.headers.get("Content-Type", "").split(";", 1)[0]
                        != "application/x-www-form-urlencoded"
                    ):
                        raise ValueError("query_request_invalid")
                    self.connection.settimeout(2)
                    params = parse_qs(
                        self.rfile.read(length).decode("ascii"),
                        strict_parsing=True,
                        max_num_fields=3,
                    )
                    self.reply(owner.ledger.record_query(owner.binding, params))
                except (ValueError, UnicodeError, BrowserReadExecutionError):
                    self.reply(status=422)

        class Server(ThreadingHTTPServer):
            def process_request(self, request: Any, client_address: Any) -> None:
                with owner.thread_lock:
                    if owner.closing or len(owner.requests) >= 16:
                        self.shutdown_request(request)
                        return
                    thread = threading.Thread(
                        target=self.process_request_thread,
                        args=(request, client_address),
                        name="chat-read-request",
                        daemon=False,
                    )
                    owner.requests.add(request)
                    owner.request_threads.add(thread)
                    try:
                        thread.start()
                    except Exception:
                        owner.requests.discard(request)
                        self.shutdown_request(request)
                        raise

            def process_request_thread(self, request: Any, client_address: Any) -> None:
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    with owner.thread_lock:
                        owner.requests.discard(request)

        port = urlsplit(self.source.manifest.site.source.origin).port
        self.server = Server(("127.0.0.1", port or 0), Handler)
        self.server.daemon_threads = False
        self.server.block_on_close = (
            False  # Join only the explicit owned thread set below, with a deadline.
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="chat-read-fixture",
            daemon=False,
        )
        self.thread.start()

    def close(self, deadline: float | None = None) -> None:
        deadline = time.monotonic() + 3 if deadline is None else deadline
        with self.thread_lock:
            self.closing = True
            requests, threads = tuple(self.requests), tuple(self.request_threads)
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # Fresh closed()/thread inspection determines the outcome.
            request.close()
        if self.server is not None:
            if self.thread is not None and self.thread.is_alive():
                self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=max(0, deadline - time.monotonic()))
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=max(0, deadline - time.monotonic()))

    def closed(self) -> bool:
        with self.thread_lock:
            return (
                self.server is not None
                and self.server.fileno() == -1
                and self.thread is not None
                and not self.thread.is_alive()
                and self.closing
                and not self.requests
                and all(not thread.is_alive() for thread in self.request_threads)
            )
