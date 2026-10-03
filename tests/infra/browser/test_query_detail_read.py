"""Query read safety through the real adapter and independent verifier.

Reuses the existing mutable DOM boundary; this is not provider or browser proof.
"""

import asyncio
import json
import secrets
from dataclasses import replace

import pytest

from app.browser_skill.models import BrowserOperationError, Coverage, ReadFieldEvidence
from app.browser_skill.site_rules import FrozenSiteAdapter, QueryField, RegisteredQueryReadRule
from app.browser_skill.verifier import IndependentVerifier
from app.infra.browser.playwright_dom_rules import DOMValue
from app.infra.browser.playwright_web_adapter import PlaywrightWebAdapter, RegisteredExecution
from tests.infra.browser.test_playwright_web_adapter import World


def query_world() -> World:
    w = World("read")
    schema = {"type": "object", "required": ["state"], "additionalProperties": False,
              "properties": {"state": {"type": "string", "pattern": "^live_"}}}
    rule = RegisteredQueryReadRule(
        object_type="item", key_ref=w.plan.read_rule.key_ref,
        fields=(QueryField(field_id="state"),),
        output_schema_json=json.dumps(schema, sort_keys=True, separators=(",", ":")),
    )
    w.plan = w.plan.model_copy(update={"read_rule": rule})
    w.site = FrozenSiteAdapter((w.plan,))
    w.confirmed = w.confirmed.model_copy(update={"mode": "independent_query_detail_v1"})
    w.row.values[".object_type"] = "item"
    w.row.values[".state"] = "live_" + secrets.token_hex(8)
    # Query verification cannot obtain an expected dynamic value from admission.
    del w._inputs["expected"]
    w.rules = replace(w.rules, read=replace(w.rules.read, object_type=DOMValue(".object_type")))
    w.adapter = PlaywrightWebAdapter(
        registry=w, observer=w.observer, site=w.site, rules=(w.rules,),
        executions=(RegisteredExecution(w.session, w.context, w.confirmed.business_key,
                                        project_output=lambda fields: dict(fields)),),
    )
    return w


def test_query_reads_dynamic_value_and_consumes_only_exact_verified_read() -> None:
    async def run() -> None:
        w = query_world()
        verifier = IndependentVerifier(w.adapter, w.site, w.read_spec)
        result = await verifier.verify(w.session, w.confirmed, w.context)
        assert result.status == "verified" and w.observer.calls == 2
        value = w.adapter._consume_verified_result(w.session, w.context, result, dict)
        assert value == {"state": w.row.values[".state"]}
        assert w.row.values[".state"] not in result.model_dump_json()
        with pytest.raises(BrowserOperationError) as caught:
            w.adapter._consume_verified_result(w.session, w.context, result, dict)
        assert caught.value.failure.code == "denied"
    asyncio.run(run())


@pytest.mark.parametrize("case,expected", [
    ("empty", "incomplete"), ("duplicate", "mismatch"), ("owner", "mismatch"),
    ("object", "mismatch"), ("missing", "incomplete"), ("schema", "mismatch"),
    ("partial", "incomplete"), ("changed", "stale"), ("oversized", "invalid_response"),
])
def test_query_rejects_incomplete_wrong_or_unstable_detail(case: str, expected: str) -> None:
    async def run() -> None:
        w = query_world()
        if case == "empty":
            w.region.children = []
        elif case == "duplicate":
            w.region.children.append(w.row)
        elif case == "owner":
            w.row.values[".user"] = "other"
        elif case == "object":
            w.row.values[".object_type"] = "other"
        elif case == "missing":
            del w.row.values[".state"]
        elif case == "schema":
            w.row.values[".state"] = "invalid"
        elif case == "partial":
            w.view = w.view.model_copy(update={
                "coverage": Coverage(state="partial", reason="pagination"),
            })
        elif case == "changed":
            def change() -> None:
                if w.observer.calls == 1:
                    w.row.values[".state"] = "live_changed"
            w.authority_hook = change
        else:
            w.row.values[".state"] = "live_" + "x" * w.rules.read.maximum_value_bytes
        verifier = IndependentVerifier(w.adapter, w.site, w.read_spec)
        if expected in {"stale", "invalid_response"}:
            with pytest.raises(BrowserOperationError) as caught:
                await verifier.verify(w.session, w.confirmed, w.context)
            assert caught.value.failure.code == expected
        else:
            result = await verifier.verify(w.session, w.confirmed, w.context)
            assert result.status == expected
        assert w.adapter._private_reads == {}
    asyncio.run(run())


def test_legacy_evidence_cannot_accept_query_presence_or_query_mode() -> None:
    async def run() -> None:
        w = World("read")
        evidence = await w.adapter.read(w.session, w.confirmed, w.context)
        assert all(field.status == "matched" for field in evidence.fields)
        for change in ({"fields": (ReadFieldEvidence(field_id="state", status="present"),)},
                       {"mode": "independent_query_detail_v1"}):
            with pytest.raises(ValueError, match="browser_read_mode_mismatch"):
                evidence.model_copy(update=change).validate_for(w.confirmed, w.binding)
    asyncio.run(run())


def test_previous_verification_cannot_consume_a_later_dynamic_read() -> None:
    async def run() -> None:
        w = query_world()
        verifier = IndependentVerifier(w.adapter, w.site, w.read_spec)
        first = await verifier.verify(w.session, w.confirmed, w.context)
        w.row.values[".state"] = "live_" + secrets.token_hex(8)
        second = await verifier.verify(w.session, w.confirmed, w.context)
        assert first.status == second.status == "verified"
        assert first.evidence_digest != second.evidence_digest
        with pytest.raises(BrowserOperationError) as caught:
            w.adapter._consume_verified_result(w.session, w.context, first, dict)
        assert caught.value.failure.code == "denied"
    asyncio.run(run())
