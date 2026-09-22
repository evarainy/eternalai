"""Signed HTTP and PostgreSQL proof for mandatory overview verification."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

from tests.auth_fakes import MemorySessionRevocations
from tests.runtime.test_production_workflow import (
    MESSAGE,
    _run_pg,
)
from tests.runtime.test_production_workflow import (
    pg_workflow_factory as pg_workflow_factory,
)


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("none", "postconditions_satisfied"),
        ("title", "pending_mismatch"),
        ("drop", "pending_mismatch"),
        ("complete", "messages_mismatch"),
        ("missing", "evidence_missing"),
        ("foreign_scope", "evidence_scope_mismatch"),
        ("exception", "evaluator_error"),
    ],
)
def test_production_request_rejects_valid_but_corrupted_overview(
    pg_workflow_factory, fault, reason
):
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    from tests.auth_fakes import TEST_CSRF_ALLOWED_ORIGINS, TEST_CSRF_HEADERS

    async def exercise():
        async with pg_workflow_factory() as h:
            engine = h.runtime._orchestration._workflow_engine
            original = engine.execute

            async def execute(**kwargs):
                result = await original(**kwargs)
                if fault == "title":
                    result.output["pending"]["workflows"][0]["title"] = "synthetic-tampered"
                elif fault == "drop":
                    result.output["pending"].update(
                        workflows=[], returned_count=0, authoritative_count=0
                    )
                elif fault == "complete":
                    result.output["messages"]["is_complete"] = True
                elif fault == "missing":
                    result = replace(result, evaluation_observations=())
                elif fault == "foreign_scope":
                    observations = result.evaluation_observations
                    result = replace(
                        result,
                        evaluation_observations=(
                            replace(
                                observations[0],
                                scope=replace(
                                    observations[0].scope,
                                    tenant_id="foreign-tenant",
                                    ai_user_id="foreign",
                                ),
                            ),
                            observations[1],
                        ),
                    )
                return result

            engine.execute = execute
            if fault == "exception":
                h.runtime._overview_evaluator.evaluate = Mock(
                    side_effect=ValueError("synthetic-private-exception"),
                )
            remember = Mock(wraps=h.runtime._session_memory.remember_completed)
            h.runtime._session_memory.remember_completed = remember
            components = h.components
            app = create_app(
                runtime=h.runtime,
                session_revocations=MemorySessionRevocations(),
                session_tokens=components.session_tokens,
                session_binder=components.session_binder.bind,
                csrf_allowed_origins=TEST_CSRF_ALLOWED_ORIGINS,
                validate_workflows=components.validate_workflows,
            )
            ticket = components.session_tokens.issue(h.principal)
            async with app.router.lifespan_context(app):
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="https://testserver",
                    cookies={"eternalai_session": ticket},
                ) as client:
                    response = await client.post(
                        "/api/v1/runtime/handle",
                        headers=TEST_CSRF_HEADERS,
                        json={
                            "session_id": h.sid,
                            "message": MESSAGE,
                            "channel": "web",
                            "client_capabilities": {},
                        },
                    )
            assert response.status_code == 200
            body = response.json()
            expected = "completed" if fault == "none" else "failed"
            assert body["status"] == expected
            task = await h.runtime._task_store.get_task(body["task_id"])
            assert task.status == expected
            assert task.error_code == (None if fault == "none" else "internal_error")
            if fault == "none":
                assert body["data"] == h.adapter.data
                assert body["data"]["messages"]["is_complete"] is False
                remember.assert_called_once()
            else:
                assert body["data"] is None
                assert body["message"] == "查询已返回，但概览结果未通过核验，本次未展示。"
                remember.assert_not_called()
            query = components.admin_registry_service._trace_query
            events = await query.list_events_by_task(task.task_id, tenant_id=task.tenant_id)
            evaluations = [event for event in events if event.event_type == "evaluation_recorded"]
            assert len(evaluations) == 1
            event = evaluations[0]
            assert (event.task_id, event.session_id, event.tenant_id, event.ai_user_id) == (
                task.task_id,
                task.session_id,
                task.tenant_id,
                task.ai_user_id,
            )
            assert event.attributes["evaluation_scope"] == "terminal_status"
            assert event.attributes["execution_status"] == "completed"
            assert event.attributes["business_status"] == expected
            business = event.attributes["business_verification"]
            assert set(business) == {"rule_id", "result", "structure_result", "reason", "checks"}
            assert set(business["checks"]) == {
                "source_binding",
                "pending_preserved",
                "messages_preserved",
            }
            assert business["rule_id"] == "oa_read_overview_v1" and business["reason"] == reason
            assert business["result"] == (
                "passed"
                if fault == "none"
                else "error"
                if fault in {"missing", "foreign_scope", "exception"}
                else "failed"
            )
            assert sum(event.event_type == "task_completed" for event in events) == (
                1 if fault == "none" else 0
            )
            assert await query.list_events_by_task(task.task_id, tenant_id="foreign-tenant") == []
            assert "synthetic-private-exception" not in repr(events)
            assert "synthetic-tampered" not in repr(events)
            assert h.gateway.execute_capability.await_count == len(h.adapter.calls) == 2

    _run_pg(exercise())


@pytest.mark.parametrize("change", ["session", "tenant", "user"])
def test_cross_scope_observation_is_rejected_with_real_stores(pg_workflow_factory, change):
    from tests.runtime.principal_fakes import runtime_principal

    async def exercise():
        async with pg_workflow_factory() as h:
            engine = h.runtime._orchestration._workflow_engine
            original = engine.execute
            captured = []

            async def execute(**kwargs):
                result = await original(**kwargs)
                if captured:
                    return replace(result, evaluation_observations=captured[0])
                captured.append(result.evaluation_observations)
                return result

            engine.execute = execute
            from unittest.mock import AsyncMock

            from app.infra.gateway.capability_gateway import CapabilityGateway

            bound = {
                tenant: CapabilityGateway(
                    capability_registry=h.gateway._capability_registry,
                    identity_mapping=h.gateway._identity_mapping,
                    policy_guard=h.gateway._policy_guard,
                    adapters=h.gateway._adapters,
                    trace_port=h.gateway._trace_port,
                    human_gate_port=h.gateway._human_gate_port,
                    tenant_id=tenant,
                )
                for tenant in (h.principal.org_ctx.tenant_id, "other-tenant")
            }

            async def dispatch_scope(*args, **kwargs):
                context = kwargs.get("request_context") or args[-1]
                return await bound[context.tenant_id].execute_capability(*args, **kwargs)

            h.gateway.execute_capability = AsyncMock(side_effect=dispatch_scope)
            first = await h.start()
            assert first.status == "completed"
            first_task = await h.runtime._task_store.get_task(first.task_id)
            query = h.components.admin_registry_service._trace_query
            first_events = await query.list_events_by_task(
                first.task_id,
                tenant_id=first_task.tenant_id,
            )
            tenant = "other-tenant" if change == "tenant" else h.principal.org_ctx.tenant_id
            user = "other-user" if change == "user" else h.principal.ai_user_id
            second_principal = runtime_principal(user, tenant_id=tenant)
            # Distinct session IDs are legitimate requests; do not weaken session ownership.
            second = await h.runtime.handle_user_message(
                channel="web",
                principal=second_principal,
                session_id=h.sid + "-second",
                message=MESSAGE,
                client_capabilities={},
            )
            assert second.status == "failed" and second.data is None
            second_task = await h.runtime._task_store.get_task(second.task_id)
            assert (second_task.ai_user_id, second_task.tenant_id) == (user, tenant)
            events = await query.list_events_by_task(second.task_id, tenant_id=tenant)
            evaluation = [event for event in events if event.event_type == "evaluation_recorded"]
            assert len(evaluation) == 1
            assert evaluation[0].attributes["business_verification"]["reason"] == (
                "evidence_scope_mismatch"
            )
            assert all(
                event.ai_user_id == user and event.session_id == second_task.session_id
                for event in events
            )
            assert await h.runtime._task_store.get_task(first.task_id) == first_task
            assert (
                await query.list_events_by_task(
                    first.task_id,
                    tenant_id=first_task.tenant_id,
                )
                == first_events
            )
            assert h.gateway.execute_capability.await_count == len(h.adapter.calls) == 4

    _run_pg(exercise())


def test_trace_write_failure_cannot_return_business_success(pg_workflow_factory):
    async def exercise():
        async with pg_workflow_factory() as h:
            writer = h.runtime._trace_port
            original = writer.record_step

            async def record(*args, **kwargs):
                if kwargs.get("event_type") == "evaluation_recorded":
                    raise RuntimeError("synthetic-trace-write-failure")
                return await original(*args, **kwargs)

            writer.record_step = record
            h.runtime._session_memory.remember_completed = Mock()
            with pytest.raises(RuntimeError, match="^synthetic-trace-write-failure$"):
                await h.start()
            h.runtime._session_memory.remember_completed.assert_not_called()
            assert h.gateway.execute_capability.await_count == len(h.adapter.calls) == 2

    _run_pg(exercise())


def test_historical_trace_does_not_acquire_business_verification(pg_workflow_factory):
    async def exercise():
        async with pg_workflow_factory() as h:
            response = await h.start()
            task = await h.runtime._task_store.get_task(response.task_id)
            legacy = {
                "rule_id": "terminal_status_v1", "business_status": "completed",
                "business_error_code": None, "evaluation_result": "passed",
                "reason": "business_completed",
            }
            await h.runtime._trace_port.record_step(
                "synthetic-historical-trace", task.task_id, task.session_id,
                tenant_id=task.tenant_id, ai_user_id=task.ai_user_id,
                event_type="evaluation_recorded", status="ok", attributes=legacy,
            )
            query = h.components.admin_registry_service._trace_query
            history = await query.list_events_by_trace(
                "synthetic-historical-trace", tenant_id=task.tenant_id,
            )
            assert len(history) == 1 and history[0].attributes == legacy
            assert "business_verification" not in history[0].attributes
            assert await query.list_events_by_trace(
                "synthetic-historical-trace", tenant_id="foreign-tenant",
            ) == []

    _run_pg(exercise())


@pytest.mark.parametrize("state", ["active", "old", "disabled"])
def test_production_lifespan_consumes_required_rule_check(monkeypatch, state):
    import asyncio
    from unittest.mock import AsyncMock

    import app.composition as composition
    from app.config import ProductionSettings
    from app.infra.adapters.oa.capabilities import expected_oa_capabilities
    from app.infra.workflow.catalog import production_workflow_capabilities
    from app.main import create_production_app

    descriptor = production_workflow_capabilities()[0]
    if state == "old":
        descriptor = descriptor.model_copy(update={"version": "1.0.0"})
    elif state == "disabled":
        descriptor = descriptor.model_copy(update={"status": "disabled"})
    rows = {item.capability_id: item for item in (*expected_oa_capabilities(), descriptor)}
    order = []

    async def list_rows(self, **kwargs):
        order.append("validate")
        return [descriptor] if state != "disabled" else []

    async def get_row(self, capability_id):
        return rows.get(capability_id)

    monkeypatch.setattr(composition.PostgreSQLCapabilityRegistry, "list", list_rows)
    monkeypatch.setattr(composition.PostgreSQLCapabilityRegistry, "get", get_row)
    for scheduler, label in (
        (composition.CredentialPollingScheduler, "poll"),
        (composition.OrganizationDirectoryScheduler, "directory"),
    ):

        async def start(self, label=label):
            order.append(label)

        monkeypatch.setattr(scheduler, "start", start)
        monkeypatch.setattr(scheduler, "stop", AsyncMock())
    gateway = AsyncMock(side_effect=AssertionError("startup must not execute business"))
    monkeypatch.setattr(composition.CapabilityGateway, "execute_capability", gateway)
    application = create_production_app(
        replace(ProductionSettings.from_environment(), oa_read_adapter_mode="mock"),
    )

    async def exercise():
        async with application.router.lifespan_context(application):
            order.append("ready")

    if state == "old":
        with pytest.raises(RuntimeError, match="^workflow_contract_mismatch$"):
            asyncio.run(exercise())
        assert order == ["validate"]
    else:
        asyncio.run(exercise())
        assert order == ["validate", "poll", "directory", "ready"]
    gateway.assert_not_awaited()
