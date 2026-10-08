"""Synthetic Playwright-shaped objects test observer identity and fail-closed paths.

Actual Chromium cases are separate and await execution approval; these objects
exercise the Python projection contract without a provider or cloud browser claim.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from app.browser_skill.models import (
    BrowserOperationError,
    BrowserSessionRef,
    ObservationPolicy,
    ObservationRequest,
)
from app.infra.browser.browserless_wire import BrowserProviderError
from app.infra.browser.playwright_observer import PlaywrightObserver, RegisteredRegion
from app.infra.browser.playwright_dom_rules import _IDENTITY_BATCH
from tests.browser_skill.factories import DIGEST, binding


@dataclass
class Node:
    connected: bool = True
    visible: bool = True
    enabled: bool = True


class Handle:
    def __init__(self, node: Node) -> None:
        self.node = node
        self.disposed = False

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        if expression == _IDENTITY_BATCH:
            assert set(arg) == {"region", "previous", "current"}
            if not self.node.connected or any(not handle.node.connected for handle in arg["current"]):
                return {"status": "stale", "same_region": False, "indices": []}
            current = [handle.node for handle in arg["current"]]
            previous = [handle.node for handle in arg["previous"]]
            if len({id(node) for node in current}) != len(current):
                return {"status": "invalid_response", "same_region": False, "indices": []}
            same = arg["region"] is not None and arg["region"].node is self.node
            positions = {id(node): index for index, node in enumerate(previous) if node.connected}
            return {"status": "ok", "same_region": same,
                    "indices": [positions.get(id(node), -1) if same else -1 for node in current]}
        if "fresh" in expression:
            return self.node is arg.node and self.node.connected
        return self.node.connected

    async def is_visible(self) -> bool:
        return self.node.visible

    async def is_enabled(self) -> bool:
        return self.node.enabled

    async def dispose(self) -> None:
        self.disposed = True

    def as_element(self) -> Handle:
        return self


class Property:
    def __init__(self, value: Any) -> None:
        self.value = value

    async def json_value(self) -> Any:
        return self.value

    async def get_properties(self) -> dict[str, Handle]:
        return {str(index): Handle(node) for index, node in enumerate(self.value)}

    async def dispose(self) -> None:
        return None


class Packet:
    def __init__(self, region: Region) -> None:
        self.region = region

    async def get_property(self, name: str) -> Property:
        if name == "metadata":
            return Property(
                {
                    "coverage": self.region.coverage,
                    "candidates": [item[1] for item in self.region.items],
                    "overflow": False,
                }
            )
        return Property([item[0] for item in self.region.items])

    async def dispose(self) -> None:
        return None


@dataclass
class Region:
    node: Node = field(default_factory=Node)
    items: list[tuple[Node, dict[str, Any]]] = field(default_factory=list)
    coverage: dict[str, Any] = field(
        default_factory=lambda: {
            "state": "complete",
            "reason": "complete",
            "trusted_empty": False,
        }
    )


class RegionHandle(Handle):
    def __init__(self, region: Region) -> None:
        super().__init__(region.node)
        self.region = region

    async def evaluate_handle(self, script: str, config: dict[str, Any]) -> Packet:
        assert "rawName" in script and config["policy"]["roles"] == ["button"]
        return Packet(self.region)


class IFrameHandle(Handle):
    def __init__(self, frame: Frame) -> None:
        super().__init__(Node())
        self.frame = frame

    async def content_frame(self) -> Frame:
        return self.frame


class Frame:
    def __init__(
        self, *, parent: Frame | None = None, origin: str = "https://fixture.invalid"
    ) -> None:
        self.parent_frame = parent
        self.child_frames: list[Frame] = []
        self.url = origin
        self.observable = True
        self.evaluations = 0
        self.selectors: dict[str, Any] = {}
        if parent is not None:
            parent.child_frames.append(self)

    async def evaluate(self, expression: str) -> bool:
        self.evaluations += 1
        if not self.observable:
            raise RuntimeError("browser detail must not escape")
        return True

    async def query_selector_all(self, selector: str) -> list[Handle]:
        value = self.selectors.get(selector)
        if isinstance(value, Region):
            return [RegionHandle(value)]
        if isinstance(value, Frame):
            return [IFrameHandle(value)]
        if isinstance(value, list):
            return value
        return []


class Page:
    def __init__(self, root: Frame) -> None:
        self.main_frame = root
        self._events: dict[str, list[Any]] = {}
        self.closed = False

    def on(self, event: str, callback: Any) -> None:
        self._events.setdefault(event, []).append(callback)

    def emit(self, event: str, frame: Frame | None = None) -> None:
        for callback in self._events.get(event, []):
            callback(frame) if frame is not None else callback()

    def is_closed(self) -> bool:
        return self.closed


class Registry:
    def __init__(self, session: BrowserSessionRef, page: Page) -> None:
        self.live = SimpleNamespace(session=session, context=SimpleNamespace(pages=[page]))
        self.calls = 0

    async def resolve_live(self, session: BrowserSessionRef) -> Any:
        self.calls += 1
        if session != self.live.session:
            raise BrowserProviderError("stale", "observe", "state")
        return self.live


def label(name: str = "Open", *, enabled: bool = True) -> tuple[Node, dict[str, Any]]:
    return Node(enabled=enabled), {
        "role": "button",
        "name": name,
        "context": [],
        "row_label": None,
        "column_label": None,
        "value_state": "not_applicable",
        "visible": True,
        "enabled": enabled,
    }


def fixture(
    *, nested: bool = True, empty_marker: bool = True
) -> tuple[
    PlaywrightObserver, Registry, BrowserSessionRef, Frame, Frame, Region, ObservationPolicy
]:
    root = Frame()
    target = Frame(parent=root) if nested else root
    if nested:
        root.selectors["iframe[data-browser=work]"] = target
    region = Region(items=[label()])
    target.selectors["[data-browser-region=inbox]"] = region
    session = BrowserSessionRef(session_ref="resource", binding=binding())
    registry = Registry(session, Page(root))
    policy = ObservationPolicy(
        policy_id="public_labels",
        digest=DIGEST,
        allowed_names=("Open", "Close"),
        allowed_roles=("button",),
    )
    site = RegisteredRegion(
        region_id="inbox",
        policy_id=policy.policy_id,
        policy_digest=policy.digest,
        region_selector="[data-browser-region=inbox]",
        frame_selectors=("iframe[data-browser=work]",) if nested else (),
        complete_selector="[data-browser-complete]",
        empty_selector="[data-browser-empty]" if empty_marker else None,
    )
    return (
        PlaywrightObserver(registry, {"inbox": site}),
        registry,
        session,
        root,
        target,
        region,
        policy,
    )


def test_recursive_actual_frame_and_stable_local_freshness() -> None:
    async def run() -> None:
        observer, registry, session, root, target, region, policy = fixture()
        first = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert len(first.scope.frame_path) == 2
        assert first.frames.children[0].frame == first.scope.frame_path[-1]
        assert first.candidates[0].name == "Open"
        assert registry.calls >= 2
        root.selectors["unrelated-timer"] = object()
        again = await observer.observe(
            session, ObservationRequest(region_id="inbox", expected_scope=first.scope), policy
        )
        assert again.scope == first.scope
        assert again.candidates[0].ref == first.candidates[0].ref
        assert (
            await observer.resolve_exact(session, first.candidates[0].ref, policy)
        ).frame is target
        region.items = [label()]
        with pytest.raises(BrowserOperationError) as exc:
            await observer.observe(
                session, ObservationRequest(region_id="inbox", expected_scope=first.scope), policy
            )
        assert exc.value.failure.code == "stale"
        fresh = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert fresh.scope.region_digest != first.scope.region_digest
        assert fresh.candidates[0].ref.target_id != first.candidates[0].ref.target_id

    asyncio.run(run())


def test_frame_remount_and_navigation_invalidate_only_related_path() -> None:
    async def run() -> None:
        observer, registry, session, root, target, region, policy = fixture()
        first = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        registry.live.context.pages[0].emit("framenavigated", target)
        with pytest.raises(BrowserOperationError) as exc:
            await observer.observe(
                session, ObservationRequest(region_id="inbox", expected_scope=first.scope), policy
            )
        assert exc.value.failure.code == "stale"
        replacement = Frame(parent=root)
        root.child_frames.remove(target)
        root.selectors["iframe[data-browser=work]"] = replacement
        replacement.selectors["[data-browser-region=inbox]"] = region
        new = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert new.scope.frame_path[-1].frame_id != first.scope.frame_path[-1].frame_id
        assert new.scope.frame_path[0] == first.scope.frame_path[0]

    asyncio.run(run())


def test_new_binding_cannot_reuse_old_page_or_target_reference() -> None:
    async def run() -> None:
        observer, registry, session, _, _, _, policy = fixture()
        old = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        rebound = BrowserSessionRef(
            session_ref=session.session_ref,
            binding=session.binding.model_copy(
                update={"lease_epoch": session.binding.lease_epoch + 1}
            ),
        )
        registry.live.session = rebound
        with pytest.raises(BrowserOperationError) as exc:
            await observer.resolve_region(
                rebound,
                ObservationRequest(region_id="inbox", expected_scope=old.scope),
                policy,
            )
        assert exc.value.failure.code == "stale"
        with pytest.raises(BrowserOperationError) as exc:
            await observer.observe(
                rebound,
                ObservationRequest(region_id="inbox", expected_scope=old.scope),
                policy,
            )
        assert exc.value.failure.code == "stale"
        fresh = await observer.observe(rebound, ObservationRequest(region_id="inbox"), policy)
        assert fresh.scope.page_id != old.scope.page_id
        assert fresh.candidates[0].ref != old.candidates[0].ref
        exact_region = await observer.resolve_region(
            rebound,
            ObservationRequest(region_id="inbox", expected_scope=fresh.scope),
            policy,
        )
        assert exact_region.projection.scope == fresh.scope
        with pytest.raises(BrowserOperationError) as exc:
            await observer.resolve_exact(rebound, old.candidates[0].ref, policy)
        assert exc.value.failure.code == "stale"

    asyncio.run(run())


def test_resolve_region_returns_actual_frame_and_borrowed_region_handle() -> None:
    async def run() -> None:
        observer, registry, session, _, target, region, policy = fixture()
        resolved = await observer.resolve_region(
            session, ObservationRequest(region_id="inbox"), policy
        )
        assert resolved.page is registry.live.context.pages[0]
        assert resolved.frame is target
        assert resolved.element.node is region.node
        assert len(resolved.projection.scope.frame_path) == 2
        again = await observer.resolve_region(
            session,
            ObservationRequest(region_id="inbox", expected_scope=resolved.projection.scope),
            policy,
        )
        assert again.projection.scope == resolved.projection.scope
        assert resolved.element.disposed is True
        assert again.element.disposed is False

    asyncio.run(run())


def test_resolve_region_rechecks_exact_element_after_fresh_observation() -> None:
    async def run() -> None:
        observer, _, session, _, target, _, policy = fixture()
        original_observe = observer.observe

        async def retarget_after_observe(*args: Any) -> Any:
            projection = await original_observe(*args)
            target.selectors["[data-browser-region=inbox]"] = Region(items=[label()])
            return projection

        observer.observe = retarget_after_observe  # type: ignore[method-assign]
        with pytest.raises(BrowserOperationError) as exc:
            await observer.resolve_region(session, ObservationRequest(region_id="inbox"), policy)
        assert exc.value.failure.code == "stale"

    asyncio.run(run())


def test_resolve_region_rejects_unobservable_frame_and_ambiguous_region() -> None:
    async def run() -> None:
        observer, _, session, _, target, region, policy = fixture()
        target.url = "https://other.invalid/frame"
        target.observable = False
        with pytest.raises(BrowserOperationError) as exc:
            await observer.resolve_region(session, ObservationRequest(region_id="inbox"), policy)
        assert exc.value.failure.code == "unsupported"

        target.observable = True
        target.selectors["[data-browser-region=inbox]"] = [
            RegionHandle(region),
            RegionHandle(Region(items=[label()])),
        ]
        with pytest.raises(BrowserOperationError) as exc:
            await observer.resolve_region(session, ObservationRequest(region_id="inbox"), policy)
        assert exc.value.failure.code == "invalid_request"

    asyncio.run(run())


def test_inaccessible_cross_origin_is_explicit_and_never_empty() -> None:
    async def run() -> None:
        observer, _, session, _, target, _, policy = fixture()
        target.url = "https://other.invalid/frame"
        target.observable = False
        result = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert result.coverage.state == "unsupported"
        assert result.coverage.reason == "cross_origin"
        assert result.frames.coverage.state == "complete"
        assert result.frames.children[0].coverage.state == "unsupported"
        assert result.candidates == ()
        assert result.coverage.trusted_empty is False

    asyncio.run(run())


def test_unsupported_parent_retains_descendants_and_blocks_selected_region() -> None:
    async def run() -> None:
        root = Frame()
        blocked = Frame(parent=root, origin="https://other.invalid/parent")
        blocked.observable = False
        target = Frame(parent=blocked)
        root.selectors["iframe[data-browser=parent]"] = blocked
        blocked.selectors["iframe[data-browser=work]"] = target
        session = BrowserSessionRef(session_ref="resource", binding=binding())
        page = Page(root)
        registry = Registry(session, page)
        policy = ObservationPolicy(
            policy_id="public_labels",
            digest=DIGEST,
            allowed_names=("Open",),
            allowed_roles=("button",),
        )
        site = RegisteredRegion(
            region_id="inbox",
            policy_id=policy.policy_id,
            policy_digest=policy.digest,
            region_selector="[data-browser-region=inbox]",
            frame_selectors=(
                "iframe[data-browser=parent]",
                "iframe[data-browser=work]",
            ),
            complete_selector="[data-browser-complete]",
            empty_selector="[data-browser-empty]",
        )
        observer = PlaywrightObserver(registry, {"inbox": site})
        first = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert len(first.scope.frame_path) == 3
        assert first.frames.children[0].coverage.state == "unsupported"
        assert first.frames.children[0].coverage.reason == "cross_origin"
        assert first.frames.children[0].children[0].frame == first.scope.frame_path[-1]
        assert first.frames.children[0].children[0].coverage.state == "complete"
        assert first.coverage.state == "unsupported"
        assert first.coverage.reason == "cross_origin"
        assert first.candidates == ()
        assert first.coverage.trusted_empty is False
        with pytest.raises(BrowserOperationError) as exc:
            await observer.resolve_region(session, ObservationRequest(region_id="inbox"), policy)
        assert exc.value.failure.code == "unsupported"

        page.emit("framenavigated", target)
        changed = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert (
            changed.scope.frame_path[-1].frame_epoch == first.scope.frame_path[-1].frame_epoch + 1
        )
        with pytest.raises(BrowserOperationError) as exc:
            await observer.observe(
                session,
                ObservationRequest(region_id="inbox", expected_scope=first.scope),
                policy,
            )
        assert exc.value.failure.code == "stale"

    asyncio.run(run())


def test_unsupported_parent_still_obeys_child_budget() -> None:
    async def run() -> None:
        observer, _, session, root, _, _, policy = fixture()
        blocked = Frame(parent=root, origin="https://other.invalid/parent")
        blocked.observable = False
        for _ in range(129):
            Frame(parent=blocked)
        with pytest.raises(BrowserOperationError) as exc:
            await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert exc.value.failure.code == "overloaded"

    asyncio.run(run())


class CountedChildren(list[Frame]):
    def __init__(self, children: list[Frame], reads: list[int]) -> None:
        super().__init__(children)
        self.reads = reads

    def __len__(self) -> int:
        self.reads.append(1)
        return super().__len__()


def wide_tree(root: Frame, node_count: int) -> list[Frame]:
    branches = (Frame(parent=root), Frame(parent=root))
    frames = [root, *branches]
    for branch, leaf_count in zip(branches, (128, node_count - 131), strict=True):
        frames.extend(Frame(parent=branch) for _ in range(leaf_count))
    return frames


def test_signature_stops_at_total_frame_budget_before_reading_remaining_nodes() -> None:
    root = Frame()
    frames = wide_tree(root, 259)
    reads: list[int] = []
    for frame in frames:
        frame.child_frames = CountedChildren(frame.child_frames, reads)
    with pytest.raises(BrowserOperationError) as exc:
        PlaywrightObserver._signature(root)
    assert exc.value.failure.code == "overloaded"
    assert len(reads) == 256


def test_initial_signature_rejects_wide_tree_before_frame_dom_evaluation() -> None:
    async def run() -> None:
        observer, _, session, root, _, _, policy = fixture(nested=False)
        frames = wide_tree(root, 259)
        with pytest.raises(BrowserOperationError) as exc:
            await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert exc.value.failure.code == "overloaded"
        assert sum(frame.evaluations for frame in frames) == 0

    asyncio.run(run())


def test_final_signature_rejects_tree_growth_with_the_same_total_budget() -> None:
    async def run() -> None:
        observer, _, session, root, _, _, policy = fixture(nested=False)
        frames = wide_tree(root, 256)
        reads: list[int] = []
        for frame in frames:
            frame.child_frames = CountedChildren(frame.child_frames, reads)
        original_snapshot = observer._snapshot
        captured: list[Handle] = []

        async def snapshot_then_grow(
            element: Handle, current_policy: ObservationPolicy, site: RegisteredRegion
        ) -> tuple[dict[str, Any], list[Any]]:
            metadata, nodes = await original_snapshot(element, current_policy, site)
            captured.extend([element, *nodes])
            for _ in range(3):
                added = Frame(parent=root.child_frames[1])
                added.child_frames = CountedChildren([], reads)
            reads.clear()
            return metadata, nodes

        observer._snapshot = snapshot_then_grow  # type: ignore[method-assign]
        with pytest.raises(BrowserOperationError) as exc:
            await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert exc.value.failure.code == "overloaded"
        assert len(reads) == 256
        assert captured and all(handle.disposed for handle in captured)

    asyncio.run(run())


def test_256_frame_boundary_supports_initial_and_final_signatures() -> None:
    async def run() -> None:
        observer, _, session, root, _, _, policy = fixture(nested=False)
        wide_tree(root, 256)
        first = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert len(first.frames.children) == 2
        assert len(first.frames.children[0].children) == 128
        assert len(first.frames.children[1].children) == 125
        again = await observer.observe(
            session, ObservationRequest(region_id="inbox", expected_scope=first.scope), policy
        )
        assert again.scope == first.scope
        assert again.candidates[0].ref == first.candidates[0].ref

    asyncio.run(run())


@pytest.mark.parametrize("limit", ["depth", "children"])
def test_signature_retains_depth_and_child_limits(limit: str) -> None:
    root = Frame()
    if limit == "depth":
        last = root
        for _ in range(31):
            last = Frame(parent=last)
        assert PlaywrightObserver._signature(root)[0] == id(root)
        Frame(parent=last)
    else:
        for _ in range(128):
            Frame(parent=root)
        assert len(PlaywrightObserver._signature(root)[1]) == 128
        Frame(parent=root)
    with pytest.raises(BrowserOperationError) as exc:
        PlaywrightObserver._signature(root)
    assert exc.value.failure.code == "overloaded"


def test_trusted_empty_requires_site_marker_and_relevant_frame_coverage() -> None:
    async def run() -> None:
        observer, _, session, root, _, region, policy = fixture(empty_marker=False)
        region.items = []
        region.coverage = {"state": "complete", "reason": "complete", "trusted_empty": True}
        result = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert result.coverage.trusted_empty is False
        sibling = Frame(parent=root, origin="https://other.invalid")
        sibling.observable = False
        result = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert result.frames.coverage.state == "complete"
        assert result.frames.children[1].coverage.state == "unsupported"
        assert result.coverage.trusted_empty is False

        trusted, _, trusted_session, trusted_root, _, trusted_region, trusted_policy = fixture()
        trusted_region.items = []
        trusted_region.coverage = {
            "state": "complete",
            "reason": "complete",
            "trusted_empty": True,
        }
        irrelevant = Frame(parent=trusted_root, origin="https://other.invalid")
        irrelevant.observable = False
        Frame(parent=irrelevant)
        local = await trusted.observe(
            trusted_session, ObservationRequest(region_id="inbox"), trusted_policy
        )
        assert local.frames.children[1].coverage.state == "unsupported"
        assert local.frames.children[1].children[0].coverage.state == "complete"
        assert local.coverage.trusted_empty is True

    asyncio.run(run())


def test_duplicate_names_remain_distinct_and_disabled_target_cannot_resolve() -> None:
    async def run() -> None:
        observer, _, session, _, _, region, policy = fixture()
        region.items = [label(), label()]
        result = await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert [item.name for item in result.candidates] == ["Open", "Open"]
        assert len({item.ref.target_id for item in result.candidates}) == 2
        region.items[0][0].enabled = False
        with pytest.raises(BrowserOperationError) as exc:
            await observer.resolve_exact(session, result.candidates[0].ref, policy)
        assert exc.value.failure.code == "stale"

    asyncio.run(run())


def test_unapproved_projection_and_other_page_fail_closed_without_raw_output() -> None:
    async def run() -> None:
        observer, registry, session, _, _, region, policy = fixture()
        region.items = [label("private synthetic marker")]
        with pytest.raises(BrowserOperationError) as exc:
            await observer.observe(session, ObservationRequest(region_id="inbox"), policy)
        assert exc.value.failure.code == "invalid_response"
        assert "private synthetic marker" not in repr(exc.value)
        registry.live.context.pages.append(Page(Frame()))
        different = BrowserSessionRef(session_ref="other", binding=session.binding)
        with pytest.raises(BrowserOperationError) as exc:
            await observer.observe(different, ObservationRequest(region_id="inbox"), policy)
        assert exc.value.failure.code == "stale"

    asyncio.run(run())
