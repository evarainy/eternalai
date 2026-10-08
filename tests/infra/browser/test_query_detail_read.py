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
from tests.infra.browser.test_playwright_web_adapter import Node, World


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


@pytest.mark.parametrize("boundary,selectors", [
    ("key", [".key"]), ("owner", [".key", ".tenant", ".user"]),
    ("object", [".key", ".tenant", ".user", ".object_type"]),
])
def test_compare_rows_never_reads_later_fields_before_key_owner_and_type(boundary, selectors) -> None:
    async def run():
        w = query_world()
        wrong = {"key": ".key", "owner": ".tenant", "object": ".object_type"}[boundary]
        w.row.values[wrong] = "other"
        evidence = await w.adapter.read(w.session, w.confirmed, w.context)
        assert w.private_selectors == selectors * 2
        assert ".state" not in w.private_selectors
        assert not evidence.schema_validated and w.adapter._private_reads == {}

    asyncio.run(run())


@pytest.mark.parametrize("rows,over_budget", [(43, False), (44, True)])
def test_unmatched_unicode_keys_still_consume_python_json_fingerprint_budget(rows, over_budget) -> None:
    async def scenario():
        w = query_world()
        private_key = "雨" * 1000
        assert len(private_key.encode()) == 3000
        encoded_bytes = len(json.dumps(private_key).encode()) * rows
        assert (encoded_bytes > 262144) is over_budget
        w.row.values[".key"] = private_key
        w.region.children = [w.row, *(Node(selector=".record", parent=w.region,
                                          values={".key": private_key}) for _ in range(rows - 1))]
        w.rules = replace(w.rules, read=replace(w.rules.read, maximum_rows=64, maximum_value_bytes=4096))
        w.adapter._rules[w.plan.skill_digest] = w.rules
        if over_budget:
            with pytest.raises(BrowserOperationError) as caught:
                await w.adapter.read(w.session, w.confirmed, w.context)
            assert caught.value.failure.code == "unsupported"
            assert w.private_selectors == [".key"] * rows
        else:
            evidence = await w.adapter.read(w.session, w.confirmed, w.context)
            assert evidence.match_count == 0 and not evidence.schema_validated
            assert w.private_selectors == [".key"] * (rows * 2)
        assert w.adapter._private_reads == {}
        assert ".state" not in w.private_selectors

    asyncio.run(scenario())
