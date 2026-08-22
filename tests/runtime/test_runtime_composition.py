"""Canonical Runtime composition and formal application smoke tests."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.admin.registry import AdminRegistryService
from app.composition import (
    build_oa_read_adapter,
    build_production_components,
    build_runtime,
    build_trace_port,
    build_trace_query,
)
from app.config import ProductionSettings
from app.credential_polling import CREDENTIAL_POLLING_TASK_TYPE
from app.evaluator import TerminalEvaluator
from app.execution_fabric.mock_adapters.oa.mock_oa_adapter import MockOAAdapter
from app.infra.adapters.oa.adapter import OAReadAdapter
from app.infra.adapters.oa.provider import (
    LiveOAReadProvider,
    ReplayOAReadProvider,
    report_oa_structural_drift,
)
from app.infra.auth.crypto import HMACSessionToken, PrincipalSessionBinder
from app.infra.auth.oa import OACredentialVerifier
from app.infra.health import RedisHealthCheck
from app.infra.identity.postgresql import PostgreSQLOAIdentityMapping
from app.infra.job_queue.in_memory import InMemoryJobQueue
from app.infra.llm.json_structured_output import JSONStructuredOutputProvider
from app.infra.llm.mock_llm.mock_llm_provider import MockLLMProvider
from app.infra.llm.openai_compatible import OpenAICompatibleLLMProvider
from app.infra.observability.noop_trace_writer import NoopTraceWriter
from app.infra.observability.postgresql_trace import (
    PostgreSQLTraceReader,
    PostgreSQLTraceWriter,
)
from app.knowledge import BasicKnowledge
from app.main import create_app, create_production_app
from app.memory import SessionMemory
from app.ports.capability_gateway import ExecutionResult
from app.ports.structured_output import StructuredOutputResult
from app.ports.task_store import SessionRecord, TaskEventRecord, TaskRecord
from app.runtime.models import CapabilityRef
from app.runtime.runtime import RuntimeImpl
from tests.auth_fakes import (
    TEST_CSRF_ALLOWED_ORIGINS,
    TEST_CSRF_HEADERS,
    StaticSessionTokens,
    auth_cookies,
    make_session_binder,
)
from tests.runtime.registry_fakes import StaticCapabilityRegistry


class RecordingTaskStore:
    def __init__(self) -> None:
        self.created: list[TaskRecord] = []

    async def create_task(self, record: TaskRecord) -> TaskRecord:
        self.created.append(record)
        return record

    async def get_task(self, task_id: str) -> TaskRecord | None:
        return None

    async def update_status(
        self, task_id: str, status: str, error_code: str | None = None
    ) -> TaskRecord:
        return self.created[-1].model_copy(
            update={"status": status, "error_code": error_code}
        )

    async def append_event(self, task_id: str, event: Any) -> None:
        return None

    async def list_tasks(
        self,
        *,
        session_id: str | None = None,
        ai_user_id: str | None = None,
    ) -> list[TaskRecord]:
        return []

    async def list_events(self, task_id: str) -> list[TaskEventRecord]:
        return []


class RecordingSessionStore:
    def __init__(self) -> None:
        self.created: list[SessionRecord] = []

    async def create_session(self, record: SessionRecord) -> SessionRecord:
        self.created.append(record)
        return record

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return None


class CompletedGateway:
    def __init__(self, capability_registry: StaticCapabilityRegistry) -> None:
        self.capability_registry = capability_registry

    async def execute_capability(self, *args: Any, **kwargs: Any) -> ExecutionResult:
        return ExecutionResult(
            status="completed",
            data={"result": "ok"},
            trace_id="gateway-trace",
        )


class DeterministicStructuredOutput:
    def __init__(self) -> None:
        self.trace_metadata: list[dict[str, Any]] = []

    async def parse_to_schema(
        self,
        raw_response: str,
        schema_type: type[Any],
        trace_metadata: dict[str, Any] | None = None,
    ) -> StructuredOutputResult:
        self.trace_metadata.append(dict(trace_metadata or {}))
        return StructuredOutputResult(
            parsed=CapabilityRef(capability_id="synthetic.query", arguments={})
        )


class RecordingTracePort:
    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    @property
    def event_types(self) -> list[str]:
        return [str(step["event_type"]) for step in self.steps]

    def set_sanitizer(self, hook: Any) -> None:
        return None

    async def record_event(self, event: Any) -> None:
        self.steps.append(cast(dict[str, Any], event.model_dump()))

    async def start_task_trace(
        self, trace_id: str, task_id: str, session_id: str
    ) -> None:
        return None

    async def record_step(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        event_type: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.steps.append({"event_type": event_type})

    async def record_policy_decision(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def record_gateway_call(
        self,
        trace_id: str,
        task_id: str,
        session_id: str,
        status: str,
        capability_id: str | None = None,
        error_code: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.steps.append({"event_type": "gateway_pre_recorded"})

    async def finalize_task_trace(self, *args: Any, **kwargs: Any) -> None:
        return None


def _valid_body() -> dict[str, Any]:
    return {
        "channel": "web",
        "session_id": "session-1",
        "message": "hello",
        "client_capabilities": {},
    }


def test_formal_http_smoke_uses_builder_backed_runtime() -> None:
    task_store = RecordingTaskStore()
    session_store = RecordingSessionStore()
    trace_port = RecordingTracePort()
    capability_registry = StaticCapabilityRegistry("synthetic.query")
    gateway = CompletedGateway(capability_registry)
    llm_provider = MockLLMProvider()
    structured_output = DeterministicStructuredOutput()
    session_memory = SessionMemory()
    semantic_knowledge = BasicKnowledge()
    evaluator = TerminalEvaluator()
    runtime = build_runtime(
        task_store=task_store,
        session_store=session_store,
        capability_registry=capability_registry,
        gateway=gateway,
        trace_port=trace_port,
        llm_provider=llm_provider,
        structured_output=structured_output,
        intent_model="test-intent-model",
        session_memory=session_memory,
        semantic_knowledge=semantic_knowledge,
        evaluator=evaluator,
    )

    session_tokens = StaticSessionTokens()
    client = TestClient(
        create_app(
            runtime,
            session_tokens=session_tokens,
            session_binder=make_session_binder(),
            session_cookie_ttl_seconds=3600,
            csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
        ),
        base_url="https://testserver",
    )
    client.cookies.update(auth_cookies())
    response = client.post(
        "/api/v1/runtime/handle",
        headers=TEST_CSRF_HEADERS,
        json=_valid_body(),
    )

    assert response.status_code == 200
    envelope = response.json()
    assert envelope["status"] == "completed"
    assert envelope["schema_version"] == "phase0.sdui.v1"
    assert envelope["data"] == {"result": "ok"}
    assert task_store.created
    assert session_store.created
    assert runtime._capability_registry is capability_registry
    assert runtime._session_memory is session_memory
    assert runtime._semantic_knowledge is semantic_knowledge
    assert runtime._evaluator is evaluator
    assert gateway.capability_registry is capability_registry
    assert llm_provider.calls[0]["model"] == "test-intent-model"
    assert llm_provider.calls[0]["response_format"] == {"type": "json_object"}
    messages = llm_provider.calls[0]["messages"]
    assert [message.role for message in messages] == ["system", "system", "user"]
    assert "semantic_system_knowledge" in messages[1].content
    assert "synthetic.query" in messages[1].content
    assert structured_output.trace_metadata == [
        {
            "trace_id": task_store.created[0].trace_id,
            "task_id": task_store.created[0].task_id,
        }
    ]
    assert "session-1" not in repr(structured_output.trace_metadata)
    assert "task_created" in trace_port.event_types
    assert "response_envelope_created" in trace_port.event_types
    assert "task_completed" in trace_port.event_types
    assert "evaluation_recorded" in trace_port.event_types


class CapturingTraceLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def debug(self, _message: str, *, extra: dict[str, Any]) -> None:
        self.events.append(cast(dict[str, Any], extra["trace_event"]))


async def _record_representative_semantic_sequence(trace_port: Any) -> None:
    await trace_port.start_task_trace("trace-equivalence", "task-equivalence", "session")
    await trace_port.record_step(
        "trace-equivalence", "task-equivalence", "session", "task_created", "ok"
    )
    await trace_port.record_gateway_call(
        "trace-equivalence",
        "task-equivalence",
        "session",
        "ok",
        "oa.synthetic.query",
    )
    await trace_port.record_step(
        "trace-equivalence",
        "task-equivalence",
        "session",
        "response_envelope_created",
        "ok",
    )
    await trace_port.record_step(
        "trace-equivalence", "task-equivalence", "session", "task_completed", "ok"
    )
    await trace_port.finalize_task_trace(
        "trace-equivalence", "task-equivalence", "session", "ok"
    )


def test_golden_trace_double_matches_real_writer_semantic_sequence() -> None:
    golden_trace = RecordingTracePort()
    logger = CapturingTraceLogger()
    real_writer = NoopTraceWriter(logger=cast(Any, logger))

    asyncio.run(_record_representative_semantic_sequence(golden_trace))
    asyncio.run(_record_representative_semantic_sequence(real_writer))

    assert [step["event_type"] for step in golden_trace.steps] == [
        event["event_type"] for event in logger.events
    ] == [
        "task_created",
        "gateway_pre_recorded",
        "response_envelope_created",
        "task_completed",
    ]


def test_trace_selector_uses_noop_only_for_explicit_testing_or_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = cast(Any, object())

    monkeypatch.setenv("ENV", "testing")
    monkeypatch.delenv("PHASE0_MOCK_MODE", raising=False)
    assert isinstance(build_trace_port(session_factory=factory), NoopTraceWriter)

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("PHASE0_MOCK_MODE", "true")
    assert isinstance(build_trace_port(session_factory=factory), NoopTraceWriter)


@pytest.mark.parametrize("environment", [None, "production", "staging", "unknown"])
def test_trace_selector_physically_excludes_noop_outside_test_mock(
    monkeypatch: pytest.MonkeyPatch,
    environment: str | None,
) -> None:
    factory = cast(Any, object())
    if environment is None:
        monkeypatch.delenv("ENV", raising=False)
    else:
        monkeypatch.setenv("ENV", environment)
    monkeypatch.delenv("PHASE0_MOCK_MODE", raising=False)

    writer = build_trace_port(session_factory=factory)

    assert isinstance(writer, PostgreSQLTraceWriter)
    assert not isinstance(writer, NoopTraceWriter)


def test_trace_query_selector_always_builds_postgresql_reader() -> None:
    reader = build_trace_query(session_factory=cast(Any, object()))

    assert isinstance(reader, PostgreSQLTraceReader)


def test_production_components_have_no_optional_dependency_gaps() -> None:
    settings = ProductionSettings.from_environment()

    components = build_production_components(settings)
    gateway = components.runtime._gateway

    assert isinstance(components.runtime, RuntimeImpl)
    assert isinstance(components.admin_registry_service, AdminRegistryService)
    assert isinstance(components.authentication, OACredentialVerifier)
    assert isinstance(components.session_tokens, HMACSessionToken)
    assert isinstance(components.session_binder, PrincipalSessionBinder)
    assert isinstance(
        components.runtime._intent_router._llm_provider,
        OpenAICompatibleLLMProvider,
    )
    assert isinstance(
        components.runtime._intent_router._structured_output,
        JSONStructuredOutputProvider,
    )
    assert isinstance(
        gateway._identity_mapping,
        PostgreSQLOAIdentityMapping,
    )
    assert gateway._capability_registry is not None
    assert gateway._identity_mapping is not None
    assert gateway._policy_guard is not None
    assert gateway._trace_port is not None
    assert gateway._adapters is not None
    assert isinstance(gateway._adapters["oa"], MockOAAdapter)
    assert isinstance(components.runtime._trace_port, PostgreSQLTraceWriter)
    assert isinstance(components.credential_polling_job_queue, InMemoryJobQueue)
    assert set(components.credential_polling_job_queue._handlers) == {
        CREDENTIAL_POLLING_TASK_TYPE
    }
    assert (
        components.credential_polling_scheduler._job_queue
        is components.credential_polling_job_queue
    )
    assert set(components.health_checks) == {"database", "redis", "vllm"}
    assert components.session_cookie_ttl_seconds > 0
    assert components.health_timeout_seconds == settings.health_timeout_seconds


def test_production_composition_rejects_missing_credential_polling_job_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.composition.InMemoryJobQueue",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(
        RuntimeError,
        match="Production credential polling requires JobQueuePort",
    ):
        build_production_components(ProductionSettings.from_environment())


def test_production_app_warns_when_session_cookie_secure_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = replace(
        ProductionSettings.from_environment(),
        session_cookie_secure=False,
        csrf_allowed_origins=frozenset({"http://testserver"}),
    )
    components = SimpleNamespace(
        runtime=None,
        admin_registry_service=None,
        work_object_service=None,
        credential_binding_service=None,
        credential_polling_scheduler=None,
        authentication=None,
        session_tokens=None,
        session_binder=SimpleNamespace(bind=lambda *_args: "unused"),
        session_cookie_ttl_seconds=settings.session_cookie_ttl_seconds,
        health_checks={},
        health_timeout_seconds=settings.health_timeout_seconds,
    )
    application = object()
    monkeypatch.setattr(
        "app.main.build_production_components",
        lambda resolved: components if resolved is settings else None,
    )
    monkeypatch.setattr("app.main.create_app", lambda **_kwargs: application)

    with caplog.at_level(logging.WARNING, logger="app.main"):
        result = create_production_app(settings)

    warning_records = [
        record
        for record in caplog.records
        if record.name == "app.main"
        and record.getMessage().startswith("session_cookie_secure_disabled")
    ]
    assert result is application
    assert len(warning_records) == 1
    assert warning_records[0].getMessage() == (
        "session_cookie_secure_disabled key=SESSION_COOKIE_SECURE"
    )
    assert warning_records[0].args == ("SESSION_COOKIE_SECURE",)


@pytest.mark.parametrize(
    "dependency_target",
    [
        "app.composition.PostgreSQLCapabilityRegistry",
        "app.composition.PostgreSQLOAIdentityMapping",
        "app.composition.MinimalPolicyGuard",
        "app.composition.PostgreSQLTraceWriter",
        "app.composition.build_oa_read_adapter",
    ],
)
def test_production_composition_rejects_incomplete_gateway_wiring(
    monkeypatch: pytest.MonkeyPatch,
    dependency_target: str,
) -> None:
    monkeypatch.setattr(
        dependency_target,
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(
        RuntimeError,
        match="Production CapabilityGateway wiring is incomplete",
    ):
        build_production_components(ProductionSettings.from_environment())


def test_production_health_composition_uses_db_redis_and_vllm_checks() -> None:
    contract_pack_dir = (
        Path(__file__).parents[1]
        / "contract_packs"
        / "oa"
        / "ecology9-pending-workflows-v3"
    )
    settings = replace(
        ProductionSettings.from_environment(),
        environment_name="production",
        oa_read_adapter_mode="replay",
        oa_read_contract_pack_dir=contract_pack_dir,
    )

    components = build_production_components(
        settings,
        trace_port=NoopTraceWriter(),
    )

    assert components.work_object_service._gateway is components.runtime._gateway
    assert set(components.health_checks) == {"database", "redis", "vllm"}
    assert isinstance(components.health_checks["database"], partial)
    assert isinstance(components.health_checks["redis"], RedisHealthCheck)
    assert isinstance(components.health_checks["vllm"], partial)
    assert components.health_checks["database"].keywords == {
        "timeout_seconds": settings.health_timeout_seconds
    }
    assert components.health_checks["vllm"].keywords == {
        "timeout_seconds": settings.health_timeout_seconds
    }


def test_default_production_assembly_rejects_implicit_mock_mode() -> None:
    settings = replace(
        ProductionSettings.from_environment(),
        environment_name="production",
        oa_read_adapter_mode="mock",
        phase0_mock_mode=False,
    )

    with pytest.raises(
        RuntimeError,
        match="requires ENV=testing or PHASE0_MOCK_MODE=true",
    ):
        build_production_components(settings)


def test_explicit_phase0_flag_allows_production_mock_adapter() -> None:
    settings = replace(
        ProductionSettings.from_environment(),
        environment_name="production",
        oa_read_adapter_mode="mock",
        phase0_mock_mode=True,
    )

    adapter = build_oa_read_adapter(
        settings=settings,
        credential_store=cast(Any, object()),
    )

    assert isinstance(adapter, MockOAAdapter)


@pytest.mark.parametrize(
    ("mode", "provider_type"),
    [
        ("replay", ReplayOAReadProvider),
        ("live", LiveOAReadProvider),
    ],
)
def test_oa_read_adapter_mode_builds_configured_provider(
    mode: str,
    provider_type: type[ReplayOAReadProvider] | type[LiveOAReadProvider],
) -> None:
    contract_pack_dir = (
        Path(__file__).parents[1]
        / "contract_packs"
        / "oa"
        / "ecology9-pending-workflows-v3"
    )
    system_message_contract_pack_dir = (
        Path(__file__).parents[1]
        / "contract_packs"
        / "oa"
        / "ecology9-system-messages-v1"
    )
    settings = replace(
        ProductionSettings.from_environment(),
        oa_read_adapter_mode=cast(Any, mode),
        oa_read_contract_pack_dir=contract_pack_dir,
        oa_pending_workflows_contract_pack_dir=(
            contract_pack_dir if mode == "live" else None
        ),
        oa_system_messages_contract_pack_dir=(
            system_message_contract_pack_dir if mode == "live" else None
        ),
        oa_message_center_path=(
            "/api/message-center/list" if mode == "live" else None
        ),
        oa_pending_workflows_split_page_key_path=(
            "/api/table/split" if mode == "live" else None
        ),
        oa_pending_workflows_counts_path=(
            "/api/table/counts" if mode == "live" else None
        ),
        oa_pending_workflows_datas_path=(
            "/api/table/datas" if mode == "live" else None
        ),
        oa_pending_workflows_actiontype=(
            "synthetic-action" if mode == "live" else None
        ),
        oa_pending_workflows_hide_no_data_tab=(
            "synthetic-hide" if mode == "live" else None
        ),
        oa_pending_workflows_method=(
            "synthetic-method" if mode == "live" else None
        ),
        oa_pending_workflows_offical_type=(
            "synthetic-offical-type" if mode == "live" else None
        ),
        oa_pending_workflows_view_scope=(
            "synthetic-view-scope" if mode == "live" else None
        ),
        oa_pending_workflows_sort_params=(
            "synthetic-sort" if mode == "live" else None
        ),
        oa_system_messages_category_id=("202" if mode == "live" else None),
        oa_system_messages_bizstate=(
            "system-business-state" if mode == "live" else None
        ),
        oa_system_messages_select_state=(
            "system-selection-state" if mode == "live" else None
        ),
    )

    adapter = build_oa_read_adapter(
        settings=settings,
        credential_store=cast(Any, object()),
    )

    assert isinstance(adapter, OAReadAdapter)
    assert isinstance(adapter._provider, provider_type)
    if mode == "live":
        assert adapter._provider._drift_reporter is report_oa_structural_drift


def test_explicit_production_adapters_and_identity_mapping_take_priority() -> None:
    adapters = {"oa": cast(Any, object())}
    identity_mapping = cast(Any, object())
    settings = replace(
        ProductionSettings.from_environment(),
        environment_name="production",
        oa_read_adapter_mode="mock",
        phase0_mock_mode=False,
    )

    components = build_production_components(
        settings,
        adapters=adapters,
        identity_mapping=identity_mapping,
    )

    assert components.runtime._gateway._adapters == adapters
    assert components.runtime._gateway._identity_mapping is identity_mapping


@pytest.mark.parametrize("override", ["identity_mapping", "adapters"])
def test_live_production_rejects_static_identity_and_adapter_overrides(
    override: str,
) -> None:
    contract_pack_dir = (
        Path(__file__).parents[1]
        / "contract_packs"
        / "oa"
        / "ecology9-pending-workflows-v3"
    )
    system_message_contract_pack_dir = (
        Path(__file__).parents[1]
        / "contract_packs"
        / "oa"
        / "ecology9-system-messages-v1"
    )
    settings = replace(
        ProductionSettings.from_environment(),
        oa_read_adapter_mode="live",
        oa_read_contract_pack_dir=contract_pack_dir,
        oa_pending_workflows_contract_pack_dir=contract_pack_dir,
        oa_system_messages_contract_pack_dir=(
            system_message_contract_pack_dir
        ),
        oa_message_center_path="/api/message-center/list",
        oa_pending_workflows_split_page_key_path="/api/table/split",
        oa_pending_workflows_counts_path="/api/table/counts",
        oa_pending_workflows_datas_path="/api/table/datas",
        oa_pending_workflows_actiontype="synthetic-action",
        oa_pending_workflows_hide_no_data_tab="synthetic-hide",
        oa_pending_workflows_method="synthetic-method",
        oa_pending_workflows_offical_type="synthetic-offical-type",
        oa_pending_workflows_view_scope="synthetic-view-scope",
        oa_pending_workflows_sort_params="synthetic-sort",
        oa_system_messages_category_id="202",
        oa_system_messages_bizstate="system-business-state",
        oa_system_messages_select_state="system-selection-state",
    )
    overrides = {override: cast(Any, object())}

    with pytest.raises(RuntimeError, match="does not allow"):
        build_production_components(settings, **overrides)
