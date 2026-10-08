"""Bounded browser observation with infra-only Page, Frame and DOM references.

This module implements observation and exact-reference lookup, not the WebAdapter
action/read methods. The single Executor must perform its own current-authority
check and dispatch barrier before using an exact reference to send an action.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol
from urllib.parse import urlsplit

from app.browser_skill.models import (
    BrowserFailure,
    BrowserOperationError,
    BrowserSessionRef,
    Coverage,
    FrameHop,
    FrameObservation,
    ObservationPolicy,
    ObservationRequest,
    ScopeBinding,
    ScopeStamp,
    TargetRef,
    VisibleCandidate,
    VisibleProjection,
)
from app.browser_skill.scoping import scope_snapshot
from app.infra.browser.browserless_wire import BrowserProviderError
from app.infra.browser.playwright_dom_rules import DOMBatchError, match_actual_nodes


class LiveResourceRegistry(Protocol):
    """Provider-owned current-authority lookup. No domain Playwright references."""

    async def resolve_live(self, session: BrowserSessionRef) -> Any: ...


@dataclass(frozen=True, slots=True)
class RegisteredRegion:
    """Trusted Site configuration, never built from a model or caller selector."""

    region_id: str
    policy_id: str
    policy_digest: str
    region_selector: str
    frame_selectors: tuple[str, ...] = ()
    complete_selector: str | None = None
    empty_selector: str | None = None
    pagination_selector: str | None = None
    virtualized_selector: str | None = None

    def __post_init__(self) -> None:
        if not self.region_id or not self.policy_id or len(self.policy_digest) != 64:
            raise ValueError("browser_region_configuration_invalid")
        if self.region_selector.strip().lower() in {"", "*", "body", "html", ":root"}:
            raise ValueError("browser_region_must_be_explicit")
        if len(self.frame_selectors) > 31 or any(
            not selector.strip() for selector in self.frame_selectors
        ):
            raise ValueError("browser_frame_configuration_invalid")


@dataclass(slots=True)
class _FrameIdentity:
    frame: Any = field(repr=False)
    frame_id: str
    epoch: int = 0


@dataclass(slots=True)
class _PageIdentity:
    page: Any = field(repr=False)
    context: Any = field(repr=False)
    binding: ScopeBinding = field(repr=False)
    page_id: str
    epoch: int = 0
    frames: dict[int, _FrameIdentity] = field(default_factory=dict, repr=False)


@dataclass(slots=True)
class _CandidateIdentity:
    target_id: str
    epoch: int
    element: Any = field(repr=False)
    ref: TargetRef | None = None


@dataclass(slots=True)
class _RegionIdentity:
    element: Any = field(repr=False)
    token: str
    next_epoch: int
    scope: ScopeStamp
    candidates: list[_CandidateIdentity] = field(default_factory=list, repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class ExactNode:
    """Process-local locator result, never authority to dispatch an action."""

    page: Any
    frame: Any
    element: Any
    ref: TargetRef


@dataclass(frozen=True, slots=True, repr=False)
class ExactRegion:
    """Borrowed infra-only region handle; callers must own any longer-lived clone."""

    page: Any
    frame: Any
    element: Any
    projection: VisibleProjection


@dataclass(slots=True, repr=False)
class ResolutionRound:
    """Borrowed handles for one locked resolution, never an authorization cache."""

    observer: PlaywrightObserver
    session: BrowserSessionRef
    policy: ObservationPolicy
    region: ExactRegion
    nodes: tuple[tuple[VisibleCandidate, ExactNode], ...]
    active: bool = True

    def node(self, ref: TargetRef) -> ExactNode:
        if not self.active or ref.scope != self.region.projection.scope:
            raise _failure("stale")
        for candidate, node in self.nodes:
            if candidate.ref == ref:
                return node
        raise _failure("stale")

    async def check(self) -> None:
        if not self.active:
            raise _failure("stale")
        await self.observer._check_projection_locked(
            self.session, self.region.projection, self.policy,
        )


@lru_cache(maxsize=1)
def _snapshot_script() -> str:
    return Path(__file__).with_name("dom_snapshot.js").read_text(encoding="utf-8")


ObserveCode = Literal[
    "denied",
    "invalid_request",
    "stale",
    "resource_not_found",
    "unsupported",
    "invalid_response",
    "overloaded",
]


def _failure(code: ObserveCode) -> BrowserOperationError:
    return BrowserOperationError(
        BrowserFailure(
            code=code, phase="observe", dispatch_state="not_sent", cleanup_required=False
        )
    )


async def _dispose(handle: Any, *, suppress_cancel: bool = False) -> None:
    try:
        await handle.dispose()
    except asyncio.CancelledError:
        if not suppress_cancel:
            raise
    except Exception:
        # A failed disposal never changes a business result or starts an action.
        pass


async def _same_node(old: Any, current: Any) -> bool:
    try:
        return bool(await old.evaluate("(old, fresh) => old === fresh && old.isConnected", current))
    except Exception:
        return False


async def _dispose_many(handles: tuple[Any, ...], *, suppress_cancel: bool = False) -> None:
    """Release owned handles and preserve cancellation after the other exits."""
    cancellation: asyncio.CancelledError | None = None
    disposed: set[int] = set()
    for handle in handles:
        if handle is None or id(handle) in disposed:
            continue
        disposed.add(id(handle))
        try:
            await _dispose(handle, suppress_cancel=suppress_cancel or cancellation is not None)
        except asyncio.CancelledError as error:
            cancellation = error
    if cancellation is not None:
        raise cancellation


def _origin(raw_url: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(raw_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            return None
        return (parsed.scheme, parsed.hostname, parsed.port)
    except ValueError:
        return None


class PlaywrightObserver:
    """One projection per registered region in one actual frame."""

    def __init__(
        self, registry: LiveResourceRegistry, regions: Mapping[str, RegisteredRegion]
    ) -> None:
        if not regions or any(key != region.region_id for key, region in regions.items()):
            raise ValueError("browser_regions_invalid")
        self._registry = registry
        self._regions = dict(regions)
        self._pages: dict[str, _PageIdentity] = {}
        self._observed: dict[tuple[str, str], _RegionIdentity] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._observe_wait: dict[str, tuple[str, float]] = {}
        self._observe_calls: dict[str, int] = {}
        self._snapshot_calls: dict[str, int] = {}
        self._batch_counts: dict[str, dict[str, int]] = {}

    def _count(self, session: BrowserSessionRef, name: str) -> None:
        counters = self._batch_counts.setdefault(session.session_ref, {})
        counters[name] = min(10000, counters.get(name, 0) + 1)

    async def _identity_batch(
        self, session: BrowserSessionRef, root: Any, previous_region: Any | None,
        previous: tuple[Any, ...], current: tuple[Any, ...], maximum: int,
    ) -> tuple[bool, tuple[int, ...]]:
        self._count(session, "observer_identity_batch_attempts")
        try:
            result = await match_actual_nodes(
                root, previous_region, previous, current, maximum=maximum,
            )
        except DOMBatchError as error:
            raise _failure(error.code) from None
        self._count(session, "observer_identity_batch_successes")
        return result

    async def _live(self, session: BrowserSessionRef) -> Any:
        try:
            live = await self._registry.resolve_live(session)
        except BrowserProviderError as error:
            raise BrowserOperationError(error.failure) from None
        if live.session != session or live.context is None:
            raise _failure("denied")
        return live

    async def register_page(self, session: BrowserSessionRef, page: Any) -> None:
        """Trusted action/provider composition explicitly selects a popup or new tab."""
        live = await self._live(session)
        if not any(item is page for item in live.context.pages) or page.is_closed():
            raise _failure("denied")
        previous = self._pages.get(session.session_ref)
        if (
            previous is not None
            and previous.page is page
            and previous.context is live.context
            and previous.binding == session.binding
        ):
            return
        if previous is not None:
            for key in tuple(self._observed):
                if key[0] == session.session_ref:
                    state = self._observed.pop(key)
                    await _dispose(state.element)
                    for candidate in state.candidates:
                        await _dispose(candidate.element)
        identity = _PageIdentity(
            page=page,
            context=live.context,
            binding=session.binding,
            page_id=secrets.token_hex(12),
        )
        self._pages[session.session_ref] = identity
        page.on("framenavigated", lambda frame: self._navigated(session.session_ref, page, frame))
        page.on("framedetached", lambda frame: self._detached(session.session_ref, page, frame))
        page.on("close", lambda: self._closed(session.session_ref, page))

    def _navigated(self, session_ref: str, page: Any, frame: Any) -> None:
        record = self._pages.get(session_ref)
        if record is None or record.page is not page:
            return
        self._frame_identity(record, frame).epoch += 1
        if frame is page.main_frame:
            record.epoch += 1

    def _detached(self, session_ref: str, page: Any, frame: Any) -> None:
        record = self._pages.get(session_ref)
        if record is not None and record.page is page:
            record.frames.pop(id(frame), None)

    def _closed(self, session_ref: str, page: Any) -> None:
        record = self._pages.get(session_ref)
        if record is not None and record.page is page:
            record.epoch += 1

    def _frame_identity(self, page: _PageIdentity, frame: Any) -> _FrameIdentity:
        key = id(frame)
        known = page.frames.get(key)
        if known is None or known.frame is not frame:
            known = _FrameIdentity(frame=frame, frame_id=secrets.token_hex(12))
            page.frames[key] = known
        return known

    async def _page(self, session: BrowserSessionRef, live: Any) -> _PageIdentity:
        known = self._pages.get(session.session_ref)
        if known is None:
            pages = live.context.pages
            if len(pages) != 1:
                raise _failure("invalid_request")
            await self.register_page(session, pages[0])
            known = self._pages[session.session_ref]
        elif known.binding != session.binding:
            await self.register_page(session, known.page)
            known = self._pages[session.session_ref]
        if (
            known.context is not live.context
            or not any(item is known.page for item in live.context.pages)
            or known.page.is_closed()
        ):
            raise _failure("stale")
        return known

    async def _target_frame(self, page: Any, region: RegisteredRegion) -> Any:
        frame = page.main_frame
        for selector in region.frame_selectors:
            matches: list[Any] = []
            try:
                matches = await frame.query_selector_all(selector)
                if len(matches) != 1:
                    raise _failure("resource_not_found" if not matches else "invalid_request")
                child = await matches[0].content_frame()
                if child is None or not any(item is child for item in frame.child_frames):
                    raise _failure("unsupported")
                frame = child
            except BrowserOperationError:
                raise
            except Exception:
                raise _failure("unsupported") from None
            finally:
                for handle in matches:
                    await _dispose(handle)
        return frame

    def _path(self, page: _PageIdentity, frame: Any) -> tuple[FrameHop, ...]:
        found: list[FrameHop] = []
        current = frame
        while current is not None and len(found) < 32:
            identity = self._frame_identity(page, current)
            found.append(FrameHop(frame_id=identity.frame_id, frame_epoch=identity.epoch))
            current = current.parent_frame
        if current is not None or not found:
            raise _failure("invalid_response")
        found.reverse()
        root = self._frame_identity(page, page.page.main_frame)
        if found[0].frame_id != root.frame_id:
            raise _failure("invalid_response")
        return tuple(found)

    async def _frame_tree(
        self,
        page: _PageIdentity,
        frame: Any,
        root_origin: tuple[str, str, int | None] | None,
        budget: list[int],
        depth: int = 1,
    ) -> FrameObservation:
        budget[0] += 1
        if budget[0] > 256 or depth > 32 or len(frame.child_frames) > 128:
            raise _failure("overloaded")
        hop_state = self._frame_identity(page, frame)
        hop = FrameHop(frame_id=hop_state.frame_id, frame_epoch=hop_state.epoch)
        try:
            observable = await frame.evaluate("() => Boolean(document.documentElement)")
        except Exception:
            observable = False
        if not observable:
            reason: Literal["cross_origin", "unobservable"] = (
                "cross_origin"
                if (root_origin is not None and _origin(frame.url) not in (None, root_origin))
                else "unobservable"
            )
            coverage = Coverage(state="unsupported", reason=reason)
        else:
            coverage = Coverage(state="complete", reason="complete")
        children = tuple(
            [
                await self._frame_tree(page, child, root_origin, budget, depth + 1)
                for child in frame.child_frames
            ]
        )
        # Frame topology is independent of this frame's DOM observability.
        # A child's coverage cannot make its parent observable or erase an
        # unrelated complete local region.
        return FrameObservation(
            frame=hop,
            coverage=coverage,
            children=children,
        )

    @staticmethod
    def _signature(frame: Any, depth: int = 1, budget: list[int] | None = None) -> tuple[Any, ...]:
        if budget is None:
            budget = [0]
        budget[0] += 1
        if budget[0] > 256 or depth > 32 or len(frame.child_frames) > 128:
            raise _failure("overloaded")
        return (
            id(frame),
            tuple(
                PlaywrightObserver._signature(child, depth + 1, budget)
                for child in frame.child_frames
            ),
        )

    @staticmethod
    async def _snapshot(
        element: Any, policy: ObservationPolicy, site: RegisteredRegion
    ) -> tuple[dict[str, Any], list[Any]]:
        packet: Any = None
        metadata_handle: Any = None
        nodes_handle: Any = None
        nodes: list[Any] = []
        properties: dict[str, Any] = {}
        transferred = False
        containers_pending = True
        try:
            config = {
                "policy": {
                    "roles": list(policy.allowed_roles),
                    "names": list(policy.allowed_names),
                    "context": list(policy.allowed_context),
                    "rowLabels": list(policy.allowed_row_labels),
                    "columnLabels": list(policy.allowed_column_labels),
                    "maximumCandidates": policy.maximum_candidates,
                },
                "site": {
                    "completeSelector": site.complete_selector,
                    "emptySelector": site.empty_selector,
                    "paginationSelector": site.pagination_selector,
                    "virtualizedSelector": site.virtualized_selector,
                },
            }
            packet = await element.evaluate_handle(_snapshot_script(), config)
            metadata_handle = await packet.get_property("metadata")
            metadata = await metadata_handle.json_value()
            nodes_handle = await packet.get_property("nodes")
            properties = await nodes_handle.get_properties()
            if not isinstance(metadata, dict) or set(metadata) != {
                "coverage",
                "candidates",
                "overflow",
            }:
                raise _failure("invalid_response")
            raw_candidates = metadata["candidates"]
            if type(metadata["overflow"]) is not bool or not isinstance(raw_candidates, list):
                raise _failure("invalid_response")
            if metadata["overflow"]:
                raise _failure("overloaded")
            if len(raw_candidates) > policy.maximum_candidates or len(properties) < len(
                raw_candidates
            ):
                raise _failure("invalid_response")
            for index in range(len(raw_candidates)):
                item = properties.get(str(index))
                node = item.as_element() if item is not None else None
                if node is None:
                    raise _failure("invalid_response")
                nodes.append(node)
            for key, value in tuple(properties.items()):
                if key not in {str(index) for index in range(len(raw_candidates))}:
                    properties.pop(key)
                    await _dispose(value)
            containers_pending = False
            await _dispose_many((nodes_handle, metadata_handle, packet))
            transferred = True
            return metadata, nodes
        except BrowserOperationError:
            raise
        except Exception:
            raise _failure("invalid_response") from None
        finally:
            if not transferred:
                # get_properties owns every returned handle, including values
                # not yet appended to nodes. Cancelled allocation must release
                # those too; cleanup cannot replace the original failure.
                containers = ((nodes_handle, metadata_handle, packet) if containers_pending else ())
                await _dispose_many(
                    (*properties.values(), *nodes, *containers), suppress_cancel=True
                )

    async def _take_snapshot(
        self, session: BrowserSessionRef, element: Any,
        policy: ObservationPolicy, site: RegisteredRegion,
    ) -> tuple[dict[str, Any], list[Any]]:
        self._snapshot_calls[session.session_ref] = min(
            10000, self._snapshot_calls.get(session.session_ref, 0) + 1,
        )
        return await self._snapshot(element, policy, site)

    @staticmethod
    def _safe_candidate(raw: Any, policy: ObservationPolicy) -> dict[str, Any]:
        expected = {
            "role",
            "name",
            "context",
            "row_label",
            "column_label",
            "value_state",
            "visible",
            "enabled",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise _failure("invalid_response")
        role = raw["role"]
        name = raw["name"]
        context = raw["context"]
        row = raw["row_label"]
        column = raw["column_label"]
        if (
            type(role) is not str
            or role not in policy.allowed_roles
            or (name is not None and (type(name) is not str or name not in policy.allowed_names))
            or type(context) is not list
            or any(type(item) is not str or item not in policy.allowed_context for item in context)
            or (row is not None and (type(row) is not str or row not in policy.allowed_row_labels))
            or (
                column is not None
                and (type(column) is not str or column not in policy.allowed_column_labels)
            )
            or raw["value_state"] not in {"empty", "nonempty", "not_applicable"}
            or raw["visible"] is not True
            or type(raw["enabled"]) is not bool
        ):
            raise _failure("invalid_response")
        return {
            "role": role,
            "name": name,
            "context": tuple(context),
            "row_label": row,
            "column_label": column,
            "value_state": raw["value_state"],
            "visible": True,
            "enabled": raw["enabled"],
        }

    @staticmethod
    def _digest(
        token: str, policy: ObservationPolicy, coverage: Coverage, rows: list[dict[str, Any]]
    ) -> str:
        safe = {
            "region_token": token,
            "policy_digest": policy.digest,
            "coverage": coverage.model_dump(),
            "candidates": rows,
        }
        encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _frame_coverage(tree: FrameObservation, path: tuple[FrameHop, ...]) -> Coverage:
        current = tree
        if current.frame != path[0]:
            raise _failure("stale")
        unsupported = current.coverage if current.coverage.state == "unsupported" else None
        for hop in path[1:]:
            next_frame = next((child for child in current.children if child.frame == hop), None)
            if next_frame is None:
                raise _failure("stale")
            current = next_frame
            if unsupported is None and current.coverage.state == "unsupported":
                unsupported = current.coverage
        return unsupported if unsupported is not None else current.coverage

    @staticmethod
    def _path_complete(tree: FrameObservation, path: tuple[FrameHop, ...]) -> bool:
        current = tree
        if current.frame != path[0] or current.coverage.state != "complete":
            return False
        for hop in path[1:]:
            next_frame = next((child for child in current.children if child.frame == hop), None)
            if next_frame is None or next_frame.coverage.state != "complete":
                return False
            current = next_frame
        return True

    async def observe(
        self,
        session: BrowserSessionRef,
        request: ObservationRequest,
        policy: ObservationPolicy,
    ) -> VisibleProjection:
        self._observe_calls[session.session_ref] = min(
            10000, self._observe_calls.get(session.session_ref, 0) + 1,
        )
        self._observe_wait.pop(session.session_ref, None)
        site = self._regions.get(request.region_id)
        if site is None or (site.policy_id, site.policy_digest) != (
            policy.policy_id,
            policy.digest,
        ):
            raise _failure("denied")
        key = (session.session_ref, request.region_id)
        self._observe_wait[session.session_ref] = ("region_lock", time.monotonic())
        async with self._locks.setdefault(key, asyncio.Lock()):
            self._observe_wait[session.session_ref] = ("live_authority", time.monotonic())
            live = await self._live(session)
            page = await self._page(session, live)
            self._observe_wait[session.session_ref] = ("dom_transport", time.monotonic())
            actual_frame = await self._target_frame(page.page, site)
            frame_path = self._path(page, actual_frame)
            start_page_epoch = page.epoch
            start_tree = self._signature(page.page.main_frame)
            tree = await self._frame_tree(
                page, page.page.main_frame, _origin(page.page.main_frame.url), [0]
            )
            target_coverage = self._frame_coverage(tree, frame_path)
            region_handle: Any = None
            new_handles: list[Any] = []
            committed = False
            old = self._observed.get(key)
            try:
                if target_coverage.state == "unsupported":
                    coverage = target_coverage
                    safe_candidates: list[dict[str, Any]] = []
                else:
                    try:
                        matches = await actual_frame.query_selector_all(site.region_selector)
                    except Exception:
                        raise _failure("unsupported") from None
                    if len(matches) != 1:
                        for match in matches:
                            await _dispose(match)
                        raise _failure("resource_not_found" if not matches else "invalid_request")
                    region_handle = matches[0]
                    metadata, new_handles = await self._take_snapshot(
                        session, region_handle, policy, site,
                    )
                    try:
                        coverage = Coverage.model_validate(metadata["coverage"])
                    except Exception:
                        raise _failure("invalid_response") from None
                    safe_candidates = [
                        self._safe_candidate(raw, policy) for raw in metadata["candidates"]
                    ]
                    if len(new_handles) != len(safe_candidates):
                        raise _failure("invalid_response")
                    if coverage.trusted_empty and (
                        not self._path_complete(tree, frame_path)
                        or not site.empty_selector
                        or not site.complete_selector
                        or safe_candidates
                    ):
                        coverage = coverage.model_copy(update={"trusted_empty": False})
                if (page.epoch != start_page_epoch
                        or self._signature(page.page.main_frame) != start_tree
                        or self._path(page, actual_frame) != frame_path):
                    raise _failure("stale")
                same_context = (old is not None and old.scope.page_id == page.page_id
                                and old.scope.page_epoch == start_page_epoch
                                and old.scope.frame_path == frame_path)
                if old is not None and not same_context and request.expected_scope is not None:
                    raise _failure("stale")
                prior_same_region = False
                previous_indices: tuple[int, ...] = tuple(-1 for _ in new_handles)
                if region_handle is not None:
                    previous_handles = (tuple(item.element for item in old.candidates)
                                        if same_context and old is not None else ())
                    try:
                        prior_same_region, previous_indices = await self._identity_batch(
                            session, region_handle,
                            old.element if same_context and old is not None else None,
                            previous_handles, tuple(new_handles), 255,
                        )
                    except Exception:
                        if (page.epoch != start_page_epoch
                                or self._signature(page.page.main_frame) != start_tree
                                or self._path(page, actual_frame) != frame_path):
                            raise _failure("stale") from None
                        raise
                token = (
                    old.token if prior_same_region and old is not None else secrets.token_hex(12)
                )
                next_epoch = old.next_epoch if prior_same_region and old is not None else 0
                identities: list[_CandidateIdentity] = []
                safe_rows: list[dict[str, Any]] = []
                for safe, handle, previous_index in zip(
                    safe_candidates, new_handles, previous_indices, strict=True,
                ):
                    previous = (old.candidates[previous_index]
                                if prior_same_region and old is not None and previous_index >= 0
                                else None)
                    if previous is None:
                        next_epoch += 1
                        identity = _CandidateIdentity(secrets.token_hex(12), next_epoch, handle)
                    else:
                        identity = _CandidateIdentity(previous.target_id, previous.epoch, handle)
                    identities.append(identity)
                    safe_rows.append(
                        {**safe, "target_id": identity.target_id, "candidate_epoch": identity.epoch}
                    )
                region_digest = self._digest(token, policy, coverage, safe_rows)
                scope = ScopeStamp(
                    page_id=page.page_id,
                    page_epoch=start_page_epoch,
                    frame_path=frame_path,
                    region_id=request.region_id,
                    region_digest=region_digest,
                )
                if request.expected_scope is not None and request.expected_scope != scope:
                    raise _failure("stale")
                if (
                    page.epoch != start_page_epoch
                    or self._signature(page.page.main_frame) != start_tree
                    or self._path(page, actual_frame) != frame_path
                    or (
                        region_handle is not None
                        and not await region_handle.evaluate("el => el.isConnected")
                    )
                ):
                    raise _failure("stale")
                self._observe_wait[session.session_ref] = ("live_authority", time.monotonic())
                current_live = await self._live(session)
                if current_live.context is not live.context or page.page.is_closed():
                    raise _failure("stale")
                self._observe_wait[session.session_ref] = ("dom_transport", time.monotonic())
                candidates = tuple(
                    VisibleCandidate(
                        ref=TargetRef(
                            target_id=item.target_id, candidate_epoch=item.epoch, scope=scope
                        ),
                        **safe,
                    )
                    for item, safe in zip(identities, safe_candidates, strict=True)
                )
                projection = VisibleProjection(
                    policy_id=policy.policy_id,
                    policy_digest=policy.digest,
                    binding=current_live.session.binding,
                    scope=scope,
                    frames=tree,
                    coverage=coverage,
                    candidates=candidates,
                )
                scope_snapshot(projection, session.binding, policy)
                if region_handle is not None:
                    self._observed[key] = _RegionIdentity(
                        region_handle, token, next_epoch, scope, identities
                    )
                    committed = True
                    if old is not None:
                        await _dispose_many((old.element, *(c.element for c in old.candidates)))
                return projection
            except BrowserOperationError:
                raise
            except Exception:
                raise _failure("invalid_response") from None
            finally:
                if not committed:
                    await _dispose_many((region_handle, *new_handles), suppress_cancel=True)

    async def resolve_region(
        self,
        session: BrowserSessionRef,
        request: ObservationRequest,
        policy: ObservationPolicy,
    ) -> ExactRegion:
        """Resolve one registered region from a fresh projection.

        The returned element is borrowed from this observer. A later observation
        may dispose it, so a reader must clone and own its temporary handle.
        This lookup is not an action authorization or a dispatch barrier.
        """
        self._observe_wait.pop(session.session_ref, None)
        projection = await self.observe(session, request, policy)
        if projection.coverage.state == "unsupported":
            raise _failure("unsupported")
        key = (session.session_ref, request.region_id)
        self._observe_wait[session.session_ref] = ("region_lock", time.monotonic())
        async with self._locks[key]:
            state = self._observed.get(key)
            page = self._pages.get(session.session_ref)
            if (
                state is None
                or page is None
                or state.scope != projection.scope
                or page.page_id != projection.scope.page_id
                or page.binding != session.binding
            ):
                raise _failure("stale")
            self._observe_wait[session.session_ref] = ("live_authority", time.monotonic())
            live = await self._live(session)
            if (
                page.context is not live.context
                or page.page.is_closed()
                or not any(item is page.page for item in live.context.pages)
            ):
                raise _failure("stale")
            try:
                self._observe_wait[session.session_ref] = ("dom_transport", time.monotonic())
                frame = await self._target_frame(page.page, self._regions[request.region_id])
                if self._path(page, frame) != projection.scope.frame_path:
                    raise _failure("stale")
                matches = await frame.query_selector_all(
                    self._regions[request.region_id].region_selector
                )
                try:
                    if (
                        len(matches) != 1
                        or not await _same_node(state.element, matches[0])
                        or not await state.element.evaluate("el => el.isConnected")
                    ):
                        raise _failure("stale")
                    self._observe_wait[session.session_ref] = ("live_authority", time.monotonic())
                    current = await self._live(session)
                    if (
                        current.context is not live.context
                        or self._pages.get(session.session_ref) is not page
                        or page.page.is_closed()
                    ):
                        raise _failure("stale")
                    self._observe_wait[session.session_ref] = ("dom_transport", time.monotonic())
                    return ExactRegion(page.page, frame, state.element, projection)
                finally:
                    for match in matches:
                        await _dispose(match)
            except BrowserOperationError:
                raise
            except Exception:
                raise _failure("stale") from None

    async def _check_projection_locked(
        self, session: BrowserSessionRef, projection: VisibleProjection,
        policy: ObservationPolicy,
    ) -> tuple[ExactRegion, tuple[tuple[VisibleCandidate, ExactNode], ...]]:
        """Validate the full fresh DOM without replacing/discarding this round's handles."""
        self._count(session, "observer_projection_check_attempts")
        def mark(stage: str) -> None:
            self._observe_wait[session.session_ref] = (stage, time.monotonic())

        def mismatch(stage: str) -> BrowserOperationError:
            _, started = self._observe_wait[session.session_ref]
            self._observe_wait[session.session_ref] = (stage, started)
            return _failure("stale")

        mark("candidate_contract")
        site = self._regions.get(projection.scope.region_id)
        if site is None or (site.policy_id, site.policy_digest) != (
            policy.policy_id, policy.digest,
        ):
            raise _failure("denied")
        scope_snapshot(projection, session.binding, policy)
        key = (session.session_ref, projection.scope.region_id)
        mark("candidate_live_authority")
        live = await self._live(session)
        mark("candidate_page")
        page = await self._page(session, live)
        state = self._observed.get(key)
        if (
            state is None or state.scope != projection.scope
            or page.page_id != projection.scope.page_id
            or page.epoch != projection.scope.page_epoch
            or page.binding != session.binding
        ):
            raise mismatch("candidate_scope_mismatch")
        mark("candidate_frame")
        frame = await self._target_frame(page.page, site)
        signature = self._signature(page.page.main_frame)
        if self._path(page, frame) != projection.scope.frame_path:
            raise mismatch("candidate_frame_mismatch")
        mark("candidate_region_lookup")
        regions = await frame.query_selector_all(site.region_selector)
        try:
            mark("candidate_region_identity")
            if len(regions) != 1:
                raise mismatch("candidate_region_mismatch")
            same_region, _ = await self._identity_batch(
                session, regions[0], state.element, (), (), 255,
            )
            if not same_region:
                raise mismatch("candidate_region_mismatch")
            mark("candidate_region_disposal")
        finally:
            await _dispose_many(tuple(regions), suppress_cancel=sys.exc_info()[0] is not None)

        mark("candidate_snapshot")
        metadata, handles = await self._take_snapshot(session, state.element, policy, site)
        nodes: list[tuple[VisibleCandidate, ExactNode]] = []
        try:
            mark("candidate_metadata")
            safe = tuple(self._safe_candidate(raw, policy) for raw in metadata["candidates"])
            if (
                Coverage.model_validate(metadata["coverage"]) != projection.coverage
                or len(safe) != len(projection.candidates)
                or len(handles) != len(projection.candidates)
                or len(state.candidates) != len(projection.candidates)
            ):
                raise mismatch("candidate_metadata_mismatch")
            same_region, indices = await self._identity_batch(
                session, state.element, state.element,
                tuple(item.element for item in state.candidates), tuple(handles), 255,
            )
            if not same_region or indices != tuple(range(len(handles))):
                raise mismatch("candidate_identity_mismatch")
            for candidate, identity, raw in zip(
                projection.candidates, state.candidates, safe, strict=True,
            ):
                mark("candidate_identity")
                if (
                    identity.target_id != candidate.ref.target_id
                    or identity.epoch != candidate.ref.candidate_epoch
                    or candidate.ref.scope != projection.scope
                    or VisibleCandidate(ref=candidate.ref, **raw) != candidate
                ):
                    raise mismatch("candidate_identity_mismatch")
                if candidate.visible and candidate.enabled:
                    mark("candidate_availability")
                    if (
                        not await identity.element.is_visible()
                        or not await identity.element.is_enabled()
                    ):
                        raise mismatch("candidate_availability_mismatch")
                    nodes.append((
                        candidate,
                        ExactNode(page.page, frame, identity.element, candidate.ref),
                    ))
            mark("candidate_handle_disposal")
        finally:
            await _dispose_many(tuple(handles), suppress_cancel=sys.exc_info()[0] is not None)
        mark("candidate_topology")
        if (
            page.epoch != projection.scope.page_epoch
            or self._signature(page.page.main_frame) != signature
            or self._path(page, frame) != projection.scope.frame_path
            or page.page.is_closed()
        ):
            raise mismatch("candidate_topology_mismatch")
        # Keep frame observability and current authority, not just topology.
        tree = await self._frame_tree(
            page, page.page.main_frame, _origin(page.page.main_frame.url), [0],
        )
        if tree != projection.frames:
            raise mismatch("candidate_projection_mismatch")
        mark("candidate_live_authority")
        current = await self._live(session)
        if (
            current.context is not live.context
            or self._pages.get(session.session_ref) is not page
            or self._observed.get(key) is not state
            or page.epoch != projection.scope.page_epoch
            or self._signature(page.page.main_frame) != signature
            or self._path(page, frame) != projection.scope.frame_path
            or page.page.is_closed()
            or not await state.element.evaluate("el => el.isConnected")
        ):
            raise mismatch("candidate_topology_mismatch")
        self._count(session, "observer_projection_check_successes")
        return ExactRegion(page.page, frame, state.element, projection), tuple(nodes)

    @asynccontextmanager
    async def _candidate_batch(
        self, session: BrowserSessionRef, projection: VisibleProjection,
        policy: ObservationPolicy,
    ) -> AsyncIterator[tuple[ExactRegion, tuple[tuple[VisibleCandidate, ExactNode], ...]]]:
        """Check both sides of selection; borrowing never crosses this region lock."""
        key = (session.session_ref, projection.scope.region_id)
        self._observe_wait[session.session_ref] = ("candidate_region_lock", time.monotonic())
        async with self._locks.setdefault(key, asyncio.Lock()):
            resolved = await self._check_projection_locked(session, projection, policy)
            yield resolved
            self._observe_wait[session.session_ref] = ("candidate_reobserve", time.monotonic())
            await self._check_projection_locked(session, projection, policy)

    @asynccontextmanager
    async def resolution_round(
        self, session: BrowserSessionRef, request: ObservationRequest,
        policy: ObservationPolicy,
    ) -> AsyncIterator[ResolutionRound]:
        projection = await self.observe(session, request, policy)
        key = (session.session_ref, request.region_id)
        async with self._locks.setdefault(key, asyncio.Lock()):
            region, nodes = await self._check_projection_locked(session, projection, policy)
            resolution = ResolutionRound(self, session, policy, region, nodes)
            try:
                yield resolution
                await resolution.check()
            finally:
                resolution.active = False

    async def resolve_exact(
        self,
        session: BrowserSessionRef,
        ref: TargetRef,
        policy: ObservationPolicy,
    ) -> ExactNode:
        """Re-observe before lookup; Executor must repeat this inside its send barrier."""
        projection = await self.observe(
            session,
            ObservationRequest(region_id=ref.scope.region_id, expected_scope=ref.scope),
            policy,
        )
        if ref not in tuple(item.ref for item in projection.candidates):
            raise _failure("stale")
        state = self._observed.get((session.session_ref, ref.scope.region_id))
        page = self._pages.get(session.session_ref)
        if state is None or page is None:
            raise _failure("stale")
        candidate = next(
            (item for item in state.candidates if item.target_id == ref.target_id), None
        )
        if candidate is None or candidate.epoch != ref.candidate_epoch:
            raise _failure("stale")
        frame = await self._target_frame(page.page, self._regions[ref.scope.region_id])
        try:
            if (
                not await candidate.element.evaluate("el => el.isConnected")
                or not await candidate.element.is_visible()
                or not await candidate.element.is_enabled()
            ):
                raise _failure("stale")
        except BrowserOperationError:
            raise
        except Exception:
            raise _failure("stale") from None
        return ExactNode(page.page, frame, candidate.element, ref)
