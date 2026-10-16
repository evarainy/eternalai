"""Query read safety through the real adapter and independent verifier.

Reuses the existing mutable DOM boundary; this is not provider or browser proof.
"""

import asyncio
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser_skill.models import BrowserOperationError, Coverage, ReadFieldEvidence
from app.browser_skill.site_rules import (
    ExpectedDownload,
    ExpectedPopup,
    FrozenSiteAdapter,
    QueryField,
    RegisteredQueryReadRule,
    RegisteredTraversal,
)
from app.browser_skill.verifier import IndependentVerifier, failure
from app.infra.browser.local_file_artifacts import FileArtifactOwner, StoredFileArtifact
from app.infra.browser.playwright_dom_rules import DOMTraversal, DOMValue
from app.infra.browser.playwright_web_adapter import PlaywrightWebAdapter, RegisteredExecution
from tests.infra.browser.test_playwright_web_adapter import Handle, Node, World


def query_world(operation: str = "read") -> World:
    w = World(operation)
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


def traversal_world() -> World:
    w = query_world("click")
    private = DOMTraversal(
        page_key=DOMValue(".page"), row_key=DOMValue(".key"),
        ordinal=DOMValue(".ordinal"), total=DOMValue(".total"),
        terminal_selector="#terminal",
    )
    digests = private.rule_digests()
    w.plan = w.plan.model_copy(update={"traversal": RegisteredTraversal(
        region_id="inbox", object_type="item", mode="paged",
        advance_step_id=w.skill.steps[0].step_id,
        max_pages=2, max_rows=4,
        page_key_rule_digest=digests["page_key"],
        row_key_rule_digest=digests["row_key"],
        ordinal_rule_digest=digests["ordinal"],
        total_rule_digest=digests["total"],
        terminal_rule_digest=digests["terminal"],
    )})
    w.site = FrozenSiteAdapter((w.plan,))
    w.rules = replace(w.rules, traversal=private)
    w.region.values.update({".page": "page-A", ".total": "1"})
    w.row.values[".ordinal"] = "0"
    terminal = Node(selector="#terminal", parent=w.region)
    w.region.children.append(terminal)
    w.adapter = PlaywrightWebAdapter(
        registry=w, observer=w.observer, site=w.site, rules=(w.rules,),
        executions=(RegisteredExecution(
            w.session, w.context, w.confirmed.business_key,
            project_output=lambda fields: dict(fields),
        ),),
    )
    return w


def test_page_evidence_uses_registered_private_keys_without_claiming_completion() -> None:
    async def run() -> None:
        w = traversal_world()
        first = await w.adapter.page_evidence(w.session, "inbox", w.context)
        assert first.row_ordinals == (0,) and first.declared_total == 1
        assert first.terminal_seen is True
        assert "page-A" not in first.model_dump_json()
        assert w.row.values[".key"] not in first.model_dump_json()
        same = await w.adapter.page_evidence(w.session, "inbox", w.context)
        assert same.page_key_digest == first.page_key_digest
        assert same.row_key_digests == first.row_key_digests
        w.region.children[-1].visible = False
        partial = await w.adapter.page_evidence(w.session, "inbox", w.context)
        assert partial.terminal_seen is False

    asyncio.run(run())


def test_page_evidence_rejects_duplicate_or_missing_row_ordinal() -> None:
    async def run() -> None:
        w = traversal_world()
        extra = Node(selector=".record", parent=w.region,
                     values=dict(w.row.values))
        w.region.children.append(extra)
        with pytest.raises(BrowserOperationError) as caught:
            await w.adapter.page_evidence(w.session, "inbox", w.context)
        assert caught.value.failure.code == "unsupported"

    asyncio.run(run())


def test_page_evidence_rejects_row_reuse_during_current_authority_check() -> None:
    async def run() -> None:
        w = traversal_world()
        before_collected = False
        changed = False
        collect = w.adapter._collection_facts

        async def mark_before(region: Any, read: Any, traversal: Any, max_rows: int) -> Any:
            nonlocal before_collected
            result = await collect(region, read, traversal, max_rows)
            before_collected = True
            return result

        def change_row() -> None:
            nonlocal changed
            if before_collected and not changed:
                w.row.values[".key"] = "different-record"
                changed = True

        w.adapter._collection_facts = mark_before
        w.authority_hook = change_row
        with pytest.raises(BrowserOperationError) as caught:
            await w.adapter.page_evidence(w.session, "inbox", w.context)
        assert caught.value.failure.code == "stale"
        assert changed

    asyncio.run(run())


@pytest.mark.parametrize("wrong_object,extra_popup", [
    (False, False), (True, False), (False, True),
])
def test_expected_popup_is_registered_only_after_business_identity_check(
    wrong_object: bool, extra_popup: bool,
) -> None:
    async def run() -> None:
        w = query_world("click")
        rule = ExpectedPopup(
            step_id=w.skill.steps[0].step_id,
            origin=w.plan.navigation_origins[0],
            path_prefix="/detail",
            object_type="item",
        )
        w.adapter._popup_sources[w.session.session_ref] = SimpleNamespace(
            popup_expectations=(rule,),
            download_expectations=(),
            region=SimpleNamespace(region_selector="#region"),
        )
        popup = SimpleNamespace(url=rule.origin + "/detail/7")
        popup.is_closed = lambda: False

        async def opener() -> Any:
            return w.page

        async def wait_for_url(predicate: Any, *, timeout: int) -> None:
            assert predicate(popup.url) and timeout == rule.wait_ms

        popup.opener = opener
        popup.wait_for_url = wait_for_url
        frame = SimpleNamespace(url=popup.url, parent_frame=None)

        async def query_selector_all(selector: str) -> list[Handle]:
            assert selector == "#region"
            return [Handle(w.region, w)]

        frame.query_selector_all = query_selector_all

        async def target_frame(page: Any, region: Any) -> Any:
            assert page is popup
            return frame

        adopted: list[Any] = []

        async def register_page(session: Any, page: Any) -> None:
            assert session == w.session
            adopted.append(page)

        w.observer._target_frame = target_frame
        w.observer.register_page = register_page
        received: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

        @asynccontextmanager
        async def expect_popup(*, timeout: int):
            assert timeout > 0
            yield SimpleNamespace(value=received)

        w.page.expect_popup = expect_popup
        original_sent = w.sent

        async def send_and_open(operation: str) -> None:
            await original_sent(operation)
            w.live.context.pages.append(popup)
            w.live.context.events["page"](popup)
            if extra_popup:
                w.live.context.events["page"](SimpleNamespace())
            received.set_result(popup)

        w.sent = send_and_open
        if wrong_object or extra_popup:
            w.row.values[".object_type"] = "other"
        receipt = await w.adapter.execute(w.session, await w.command(), w.context)
        assert w.dom_sends == ["click"]
        if wrong_object or extra_popup:
            assert receipt.state == "possibly_sent" and adopted == []
        else:
            assert receipt.state == "acknowledged" and adopted == [popup]

    asyncio.run(run())


@pytest.mark.parametrize("deny_sink", [False, True])
def test_expected_download_seals_only_registered_click_and_exact_owner(
    deny_sink: bool, tmp_path: Path,
) -> None:
    async def run() -> None:
        w = query_world("click")
        rule = ExpectedDownload(
            step_id=w.skill.steps[0].step_id, object_type="item",
            media_type="text/plain", max_bytes=64, ttl_seconds=30,
            content_rule_digest="a" * 64,
        )
        w.adapter._popup_sources[w.session.session_ref] = SimpleNamespace(
            popup_expectations=(), download_expectations=(rule,),
            manifest=SimpleNamespace(capability=SimpleNamespace(target_system="oa")),
        )
        source_path = tmp_path / "browser-download"
        owner = FileArtifactOwner(
            w.binding.owner.tenant_id, w.binding.owner.user_id,
            w.binding.owner.session_id, "oa", w.binding.binding_id,
            w.binding.binding_revision, "task", "run",
        )
        artifact = StoredFileArtifact(
            secrets.token_urlsafe(32), owner, "text/plain", 3,
            hashlib.sha256(b"abc").digest(),
            datetime.now(timezone.utc) + timedelta(seconds=30),
            storage_scope="a" * 64,
        )
        sealed: list[Path] = []

        async def sink(path: Path, expected: ExpectedDownload) -> StoredFileArtifact:
            assert expected == rule
            sealed.append(path)
            if deny_sink:
                raise failure("denied")
            return artifact

        registration = w.adapter._executions[w.session.session_ref]
        w.adapter._executions[w.session.session_ref] = replace(
            registration, download_sink=sink,
        )
        received: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

        @asynccontextmanager
        async def expect_download(*, timeout: int):
            assert timeout > 0
            yield SimpleNamespace(value=received)

        w.page.expect_download = expect_download

        async def no_failure() -> None:
            return None

        async def path() -> str:
            return str(source_path)

        download = SimpleNamespace(
            page=w.page, url=w.plan.source.origin + "/export",
            failure=no_failure, path=path,
        )
        original_sent = w.sent

        async def send_and_download(operation: str) -> None:
            await original_sent(operation)
            received.set_result(download)

        w.sent = send_and_download
        receipt = await w.adapter.execute(w.session, await w.command(), w.context)
        assert receipt.state == ("possibly_sent" if deny_sink else "acknowledged")
        assert sealed == [source_path] and w.dom_sends == ["click"]

    asyncio.run(run())


def test_expected_download_without_private_sink_denies_before_click() -> None:
    async def run() -> None:
        w = query_world("click")
        rule = ExpectedDownload(
            step_id=w.skill.steps[0].step_id, object_type="item",
            media_type="text/plain", max_bytes=64, ttl_seconds=30,
            content_rule_digest="a" * 64,
        )
        w.adapter._popup_sources[w.session.session_ref] = SimpleNamespace(
            popup_expectations=(), download_expectations=(rule,),
        )
        with pytest.raises(BrowserOperationError) as caught:
            await w.adapter.execute(w.session, await w.command(), w.context)
        assert caught.value.failure.code == "unsupported"
        assert w.dom_sends == []

    asyncio.run(run())


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


def test_query_private_values_require_matching_read_digest_with_composite_evidence() -> None:
    async def run() -> None:
        w = query_world()
        verifier = IndependentVerifier(w.adapter, w.site, w.read_spec)
        result = await verifier.verify(w.session, w.confirmed, w.context)
        assert result.status == "verified" and result.evidence_digest is not None
        unrelated = result.model_copy(update={
            "read_evidence_digest": "0" * 64,
        })
        with pytest.raises(BrowserOperationError) as caught:
            w.adapter._consume_verified_result(w.session, w.context, unrelated, dict)
        assert caught.value.failure.code == "denied"

        result = await verifier.verify(w.session, w.confirmed, w.context)
        composite = result.model_copy(update={
            "evidence_digest": "1" * 64,
            "read_evidence_digest": result.evidence_digest,
        })
        assert w.adapter._consume_verified_result(w.session, w.context, composite, dict) == {
            "state": w.row.values[".state"],
        }

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
def test_compare_rows_never_reads_later_fields_before_key_owner_and_type(
    boundary, selectors
) -> None:
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
def test_unmatched_unicode_keys_still_consume_python_json_fingerprint_budget(
    rows, over_budget
) -> None:
    async def scenario():
        w = query_world()
        private_key = "雨" * 1000
        assert len(private_key.encode()) == 3000
        encoded_bytes = len(json.dumps(private_key).encode()) * rows
        assert (encoded_bytes > 262144) is over_budget
        w.row.values[".key"] = private_key
        w.region.children = [w.row, *(Node(selector=".record", parent=w.region,
                                          values={".key": private_key}) for _ in range(rows - 1))]
        w.rules = replace(
            w.rules, read=replace(w.rules.read, maximum_rows=64, maximum_value_bytes=4096)
        )
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
