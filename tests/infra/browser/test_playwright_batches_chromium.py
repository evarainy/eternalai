"""Real Chromium evidence for private batch JS; no provider, DB or model claim.

Requires the already installed Playwright Chromium. Missing binaries fail openly;
this entry is prepared for the separately approved validation run.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.browser_skill.models import (
    BrowserOperationError,
    BrowserSessionRef,
    ObservationPolicy,
    ObservationRequest,
)
from app.infra.browser.playwright_dom_rules import (
    DOMBatchError,
    DOMValue,
    filter_private_candidates,
    match_actual_nodes,
    private_row_identity,
    read_private_many,
)
from app.infra.browser.playwright_observer import (
    PlaywrightObserver,
    RegisteredRegion,
    _dispose_many,
)
from app.infra.browser.playwright_web_adapter import _FINAL_IDENTITY
from tests.browser_skill.factories import DIGEST, binding

HTML = """<section id="region" data-browser-region="inbox">
<i data-browser-complete></i>
<article class="row"><span class="key">key_a</span><span class="tenant">tenant_a</span>
<span class="user">user_a</span><span class="type">item</span><span class="field">雨</span>
<button id="a" class="control">Open</button><button id="b" class="control">Open</button></article>
</section><button id="outside" class="control">Open</button>"""


async def chromium_case(case):
    from playwright.async_api import async_playwright

    async with async_playwright() as driver:
        browser = await driver.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(HTML)
            await case(page)
        finally:
            await browser.close()


def test_chromium_identity_uses_actual_nodes_and_filter_fatal_is_atomic() -> None:
    async def case(page):
        root, a, b, outside = [await page.query_selector(selector)
                               for selector in ("#region", "#a", "#b", "#outside")]
        fresh = await page.query_selector_all(".row .control")
        try:
            assert await match_actual_nodes(root, root, (a, b), (fresh[1], fresh[0])) == (
                True,
                (1, 0),
            )
            selected = await filter_private_candidates(
                root, (a, b), ".control", ".row", DOMValue(".key"), 16
            )
            assert selected == ((True, "key_a"), (True, "key_a"))
            assert await filter_private_candidates(root, (a,), ".absent", None, None, 16) == (
                (False, None),
            )
            with pytest.raises(DOMBatchError) as denied:
                await filter_private_candidates(
                    root, (a, outside), ".control", ".row", DOMValue(".key"), 16
                )
            assert denied.value.code == "denied"
            with pytest.raises(DOMBatchError) as duplicate:
                await match_actual_nodes(root, root, (a, b), (a, a))
            assert duplicate.value.code == "invalid_response"
            await a.evaluate("node => node.replaceWith(node.cloneNode(true))")
            replacement = await page.query_selector("#a")
            try:
                assert await match_actual_nodes(root, root, (a, b), (replacement, b)) == (
                    True,
                    (-1, 1),
                )
                with pytest.raises(DOMBatchError) as detached:
                    await filter_private_candidates(
                        root, (a,), ".control", ".row", DOMValue(".key"), 16
                    )
                assert detached.value.code == "invalid_response"
            finally:
                await replacement.dispose()
        finally:
            await _dispose_many((root, a, b, outside, *fresh))

    asyncio.run(chromium_case(case))


@pytest.mark.parametrize("boundary,selectors", [
    ("key", [".key"]), ("owner", [".key", ".tenant", ".user"]),
    ("type", [".key", ".tenant", ".user", ".type"]),
])
def test_chromium_private_identity_short_circuits_before_unapproved_fields(
    boundary, selectors
) -> None:
    async def case(page):
        from playwright.async_api import Error as PlaywrightError

        root, node, row = [
            await page.query_selector(selector) for selector in ("#region", "#a", ".row")
        ]
        config = {
            "selector": ".control",
            "row_selector": ".row",
            "fields": [
                {
                    "selector": selector,
                    "kind": "text",
                    "attribute": None,
                    "expected": expected,
                    "limit": 32,
                }
                for selector, expected in (
                    (".key", "key_a"),
                    (".tenant", "tenant_a"),
                    (".user", "user_a"),
                    (".type", "item"),
                )
            ],
        }
        wrong = {"key": ".key", "owner": ".tenant", "type": ".type"}[boundary]
        await row.evaluate(
            """(row, selector) => {
          row.querySelector(selector).textContent = 'other';
          window.identityReads = [];
          const original = row.querySelectorAll.bind(row);
          row.querySelectorAll = selector => { window.identityReads.push(selector);"""
            """ return original(selector); };
        }""",
            wrong,
        )
        try:
            assert await private_row_identity(root, node, row, config) is False
            assert await page.evaluate("() => window.identityReads") == selectors
            assert await read_private_many(row, (DOMValue(".field"),), 3) == ("雨",)
            with pytest.raises(PlaywrightError, match="bound"):
                await read_private_many(row, (DOMValue(".field"),), 2)
        finally:
            await _dispose_many((root, node, row))

    asyncio.run(chromium_case(case))


def test_chromium_observer_invalidates_navigation_and_preserves_round_checks() -> None:
    async def case(page):
        async def serve(route):
            await route.fulfill(content_type="text/html", body=HTML)

        await page.route("https://fixture.invalid/**", serve)
        await page.goto("https://fixture.invalid/first")
        session = BrowserSessionRef(session_ref="real_batch_dom", binding=binding())
        live = SimpleNamespace(session=session, context=page.context)

        class Registry:
            async def resolve_live(self, actual):
                assert actual == session
                return live

        policy = ObservationPolicy(policy_id="public_labels", digest=DIGEST,
                                   allowed_roles=("button",), allowed_names=("Open",))
        observer = PlaywrightObserver(
            Registry(),
            {
                "inbox": RegisteredRegion(
                    region_id="inbox",
                    policy_id=policy.policy_id,
                    policy_digest=policy.digest,
                    region_selector="[data-browser-region=inbox]",
                    complete_selector="[data-browser-complete]",
                )
            },
        )
        request = ObservationRequest(region_id="inbox")
        try:
            async with observer.resolution_round(session, request, policy) as resolution:
                first = resolution.region.projection
                assert len(first.candidates) == 2 and resolution.active
            assert not resolution.active
            counts = observer._batch_counts[session.session_ref]
            assert counts["observer_projection_check_successes"] == 2
            async with observer._candidate_batch(session, first, policy) as (_, nodes):
                assert len(nodes) == 2
            assert counts["observer_projection_check_successes"] == 4
            await page.goto("https://fixture.invalid/second")
            with pytest.raises(BrowserOperationError) as stale:
                await observer.observe(
                    session,
                    ObservationRequest(region_id="inbox", expected_scope=first.scope),
                    policy,
                )
            assert stale.value.failure.code == "stale"
            fresh = await observer.observe(session, request, policy)
            assert fresh.scope.page_epoch != first.scope.page_epoch
            assert {item.ref.target_id for item in fresh.candidates}.isdisjoint(
                item.ref.target_id for item in first.candidates)
        finally:
            await _dispose_many(
                tuple(
                    handle
                    for state in observer._observed.values()
                    for handle in (state.element, *(item.element for item in state.candidates))
                )
            )

    asyncio.run(chromium_case(case))


def test_chromium_frame_remount_invalidates_scope_before_reusing_old_handles() -> None:
    async def case(page):
        async def serve(route):
            body = (
                '<iframe id="work" src="/frame-a"></iframe>'
                if route.request.url.endswith("/main")
                else HTML
            )
            await route.fulfill(content_type="text/html", body=body)

        await page.route("https://fixture.invalid/**", serve)
        await page.goto("https://fixture.invalid/main")
        session = BrowserSessionRef(session_ref="real_frame_batch", binding=binding())
        live = SimpleNamespace(session=session, context=page.context)

        class Registry:
            async def resolve_live(self, actual):
                assert actual == session
                return live

        policy = ObservationPolicy(policy_id="frame_labels", digest=DIGEST,
                                   allowed_roles=("button",), allowed_names=("Open",))
        observer = PlaywrightObserver(Registry(), {"inbox": RegisteredRegion(
            region_id="inbox", policy_id=policy.policy_id, policy_digest=policy.digest,
            region_selector="#region", frame_selectors=("#work",),
            complete_selector="[data-browser-complete]",
        )})
        try:
            first = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
            old_nodes = tuple(
                item.element
                for item in observer._observed[(session.session_ref, "inbox")].candidates
            )
            assert len(first.scope.frame_path) == 2 and len(old_nodes) == 2
            await page.evaluate("""() => {
              document.querySelector('#work').remove();
              const frame = document.createElement('iframe');
              frame.id = 'work'; frame.src = '/frame-b'; document.body.appendChild(frame);
            }""")
            frame_element = await page.query_selector("#work")
            try:
                frame = await frame_element.content_frame()
                assert frame is not None
                await frame.wait_for_url("https://fixture.invalid/frame-b")
            finally:
                await frame_element.dispose()
            with pytest.raises(BrowserOperationError) as stale:
                await observer.observe(
                    session,
                    ObservationRequest(region_id="inbox", expected_scope=first.scope),
                    policy,
                )
            assert stale.value.failure.code == "stale"
            fresh = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
            assert fresh.scope.frame_path != first.scope.frame_path
            assert {item.ref.target_id for item in fresh.candidates}.isdisjoint(
                item.ref.target_id for item in first.candidates)
        finally:
            await _dispose_many(
                tuple(
                    handle
                    for state in observer._observed.values()
                    for handle in (state.element, *(item.element for item in state.candidates))
                )
            )

    asyncio.run(chromium_case(case))


def test_chromium_final_state_matches_playwright_during_state_changes() -> None:
    async def case(page):
        await page.set_content(
            '<section id="region"><div id="parent">'
            '<button id="target">Open</button></div></section>'
        )
        target = await page.query_selector("#target")
        region = await page.query_selector("#region")
        identity = await target.evaluate_handle(
            "(node, region) => ({node, region, row: null})", region,
        )
        config = {
            "element": target, "region_selector": "#region", "selector": "#target",
            "row_selector": None, "target": True, "operation": "click", "option": None,
        }
        cases = (
            ("baseline", "", True),
            ("display_none", "n.style.display = 'none'", False),
            ("visibility_hidden", "n.style.visibility = 'hidden'", False),
            ("native_disabled", "n.disabled = true", False),
            ("ancestor_aria_true", "p.setAttribute('aria-disabled', 'true')", False),
            ("child_aria_false_override",
             "p.setAttribute('aria-disabled', 'true'); n.setAttribute('aria-disabled', 'false')",
             True),
            ("aria_uppercase_true", "n.setAttribute('aria-disabled', 'TRUE')", False),
            ("restored_enabled", "", True),
        )
        try:
            for name, mutation, expected in cases:
                await target.evaluate("""(n, code) => {
                  const p = n.parentElement;
                  n.removeAttribute('style'); n.disabled = false;
                  n.removeAttribute('aria-disabled'); p.removeAttribute('aria-disabled');
                  eval(code);
                }""", mutation)
                native = await target.is_visible() and await target.is_enabled()
                assert native is expected, name
                assert await identity.evaluate(_FINAL_IDENTITY, config) is expected, name
        finally:
            await _dispose_many((identity, target, region))

    asyncio.run(chromium_case(case))
