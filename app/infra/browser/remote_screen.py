"""Remote screen of a D fill-stop Run's own window, served from the API process.

A separate observer Playwright driver attaches over CDP to the Run's Chromium,
casts the bound page and, only while the owner holds control in a sealed
handoff, replays allowlisted mouse/keyboard/text input. It never routes,
evaluates scripts, navigates or reads page content. Takeover order follows
"stop accepting -> drain in-flight -> exclusive human" (idea only, OpenBot
take-the-wheel, MIT); the per-viewer newest-frame slot follows Steel's
page-screencast idea. No code is copied from either project.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from app.infra.browser.d_form_execution import DFormRuntime, DFormScreenTarget
from app.infra.browser.d_form_owned_browser import OwnedDFormBrowser, find_target_page
from app.ports.auth import Principal
from app.ports.browser_chat import BrowserChatError, BrowserRunAccessPort
from app.ports.browser_screen import (
    SCREEN_KEYS,
    SCREEN_PROMPT_MAX,
    SCREEN_TEXT_MAX,
    BrowserScreenError,
    ScreenFrame,
    ScreenInput,
    ScreenKey,
    ScreenMessage,
    ScreenMouse,
    ScreenText,
)

_logger = logging.getLogger("uvicorn.error.remote_screen")
_STAGES = frozenset({"open", "attach", "take", "release", "dialog", "close", "follow"})
_CODES = frozenset(
    {
        "OK",
        "denied",
        "busy",
        "not_ready",
        "unavailable",
        "timeout",
        "takeover_failed",
        "target_missing",
        "driver_failed",
        "driver_stop_failed",
        "input_stuck",
        "error",
    }
)
_MAX_VIEWERS = 4
_QUEUE_LIMIT = 64
_MESSAGE_LIMIT = 32
_POLL_SECONDS = 0.25
_INPUT_SECONDS = 5.0
_DRAIN_SECONDS = 5.0
# Covers one bounded fill action (observe 15 s + action 20 s), Flow cleanup with
# its 10 s driver stop, receipt and seal; never extends the Run's own limits.
_TAKEOVER_SECONDS = 90.0
_ATTACH_ATTEMPTS = 3
_FRAME_QUALITY = 60
_FRAME_SIZE: Any = {"width": 1280, "height": 800}
_WHEEL_LIMIT = 2000.0


def _log(stage: str, status: str, code: str, viewers: int, **counts: int) -> None:
    """Closed fields only; never coordinates, text, dialog content, URLs or cookies."""
    extra = "".join(f" {name}={int(value)}" for name, value in counts.items())
    _logger.info(
        "remote_screen stage=%s status=%s code=%s viewers=%d%s",
        stage if stage in _STAGES else "error",
        "ok" if status == "ok" else "failed",
        code if code in _CODES else "error",
        viewers,
        extra,
    )


class _Viewer:
    def __init__(
        self,
        screen: _RunScreen,
        principal: Principal,
        bound_session: str,
    ) -> None:
        self.id = uuid4().hex
        self.screen = screen
        self.principal = principal
        self.bound_session = bound_session
        self.messages: deque[ScreenMessage] = deque()
        self.wake = asyncio.Event()
        self.seen = 0
        self.closed = False

    def push(self, payload: Mapping[str, object]) -> None:
        if self.closed:
            return
        if len(self.messages) >= _MESSAGE_LIMIT:
            # A viewer that stops reading is dropped, never buffered without bound.
            self.closed = True
        else:
            self.messages.append(ScreenMessage(dict(payload)))
        self.wake.set()

    async def next_event(self) -> ScreenFrame | ScreenMessage:
        while True:
            if self.messages:
                return self.messages.popleft()
            if self.closed:
                raise BrowserScreenError("closed")
            frame = self.screen.frame
            if frame is not None and self.screen.frame_seq != self.seen:
                # Only the newest frame is ever sent; a slow viewer skips stale ones.
                self.seen = self.screen.frame_seq
                self.screen.frames_sent += 1
                return frame
            self.wake.clear()
            await self.wake.wait()

    def request_control(self) -> None:
        self.screen.service.spawn(self.screen.take(self))

    async def release_control(self) -> None:
        await self.screen.release(self)

    async def send(self, item: ScreenInput) -> None:
        self.screen.enqueue(self, item)

    async def answer_dialog(self, dialog_id: str, accept: bool, prompt: str | None) -> None:
        await self.screen.answer_dialog(self, dialog_id, accept, prompt)

    async def recheck(self) -> None:
        await self.screen.service.check_access(
            self.principal, self.bound_session, self.screen.task_id, self.screen.run_id
        )
        if self.closed or self.screen.closed:
            raise BrowserScreenError("closed")

    async def close(self) -> None:
        await self.screen.leave(self)


class _RunScreen:
    """Per-Run observer driver, frame slot and single controller."""

    def __init__(self, service: DFormRemoteScreen, task_id: str, run_id: str) -> None:
        self.service = service
        self.task_id, self.run_id = task_id, run_id
        self.lock = asyncio.Lock()
        self.op_lock = asyncio.Lock()
        self.closed = False
        self.viewers: dict[str, _Viewer] = {}
        self.controller: str | None = None
        self.pending: str | None = None
        self.epoch = 0
        self.inputs: asyncio.Queue[tuple[int, str, ScreenInput]] = asyncio.Queue(_QUEUE_LIMIT)
        self.mouse_down = False
        # Releases and detaches in progress: no input, answer or new control meanwhile.
        self.draining = 0
        self.frame: ScreenFrame | None = None
        self.frame_seq = 0
        self.browser_owned: OwnedDFormBrowser | None = None
        self.playwright: Any = None
        # Kept until it settles: a timed-out start may still bring up a driver.
        self.driver_start: asyncio.Task[Any] | None = None
        self.driver_stop: asyncio.Task[None] | None = None
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self.target_id: str | None = None
        self.dialog: Any = None
        self.dialog_id: str | None = None
        self.new_page_noticed = False
        self.phase: str | None = None
        self.attach_failures = 0
        self.watcher: asyncio.Task[None] | None = None
        self.input_task: asyncio.Task[None] | None = None
        self.frames_sent = self.inputs_applied = self.inputs_rejected = 0

    # ----- state broadcast -------------------------------------------------

    def _control_for(self, viewer: _Viewer) -> str:
        if self.controller == viewer.id:
            return "self"
        if self.controller is not None:
            return "other"
        return "pending" if self.pending is not None else "none"

    def _state_message(self, viewer: _Viewer) -> Mapping[str, object]:
        return {
            "type": "state",
            "phase": self.phase or "preparing",
            "control": self._control_for(viewer),
            "attached": self.page is not None,
        }

    def _dialog_message(self, dialog: Any) -> Mapping[str, object]:
        return {
            "type": "dialog",
            "id": self.dialog_id,
            "kind": dialog.type,
            "message": dialog.message[:SCREEN_PROMPT_MAX],
            "default": (dialog.default_value or "")[:SCREEN_PROMPT_MAX],
        }

    def sync(self, viewer: _Viewer) -> None:
        """Everything a new connection cannot have heard: current state and open dialog."""
        viewer.push(self._state_message(viewer))
        if self.dialog is not None:
            viewer.push(self._dialog_message(self.dialog))

    def broadcast_state(self) -> None:
        for viewer in list(self.viewers.values()):
            viewer.push(self._state_message(viewer))

    def broadcast(self, payload: Mapping[str, object]) -> None:
        for viewer in list(self.viewers.values()):
            viewer.push(payload)

    # ----- observer driver -------------------------------------------------

    def on_frame(self, frame: Mapping[str, Any]) -> None:
        data, width, height = frame["data"], frame["viewportWidth"], frame["viewportHeight"]
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < 1e5
            for value in (width, height)
        ):
            return
        self.frame = ScreenFrame(bytes(data), round(width), round(height))
        self.frame_seq += 1
        for viewer in self.viewers.values():
            viewer.wake.set()

    def on_dialog(self, dialog: Any) -> None:
        # Listening keeps the observer's own driver from auto-dismissing it.
        if dialog.page is not self.page:
            return
        self.dialog, self.dialog_id = dialog, uuid4().hex
        self.broadcast(self._dialog_message(dialog))

    def on_dialog_closed(self, dialog: Any) -> None:
        if dialog is self.dialog:
            self.dialog = self.dialog_id = None
            self.broadcast({"type": "dialog_closed"})

    def on_page(self, page: Any) -> None:
        # Portal preparation opens its own form page; that one is followed, not noticed.
        if page is not self.page and self.phase != "preparing" and not self.new_page_noticed:
            self.new_page_noticed = True
            self.broadcast({"type": "notice", "code": "new_window"})

    def _listen(self, context: Any, page: Any) -> None:
        # Registered before any frame: a dialog listener stops this driver from
        # auto-dismissing dialogs (Playwright closes them only without listeners).
        if context is not self.context:
            context.on("dialog", self.on_dialog)
            context.on("dialogclosed", self.on_dialog_closed)
            context.on("page", self.on_page)
        self.context, self.page = context, page

    async def attach(self, target: DFormScreenTarget) -> None:
        """Under self.lock. A failure leaves nothing attached that is not proven stopped."""
        from playwright.async_api import async_playwright

        owned = target.browser
        if (
            self.closed
            or self.page is not None
            or owned.created_dir is None
            or owned.port == 0
            or owned.target_id is None
            or owned.page is None
        ):
            return  # Window or real target not ready yet (startup page may still be open).
        if not owned.observer_exited or owned.observer_input_fault:
            # An earlier driver of this Run is not proven gone (a second one would
            # overwrite that missing proof when it stops), or a mouse button may still
            # be held in this window: none is started for the rest of this Run.
            self.attach_failures = _ATTACH_ATTEMPTS
            self.broadcast({"type": "notice", "code": "unavailable"})
            return
        self.browser_owned = owned
        try:
            lines = (
                (owned.created_dir / "profile" / "DevToolsActivePort")
                .read_text(encoding="ascii")
                .splitlines()
            )
            # Revoked before the start attempt: from here only a real stop of this
            # driver proves the observer gone, even if the start wait times out.
            owned.observer_exited = False
            self.driver_start = asyncio.create_task(async_playwright().start())
            self.playwright = await asyncio.wait_for(asyncio.shield(self.driver_start), 15)
            self.driver_start = None
            browser = await self.playwright.chromium.connect_over_cdp(
                f"ws://127.0.0.1:{owned.port}{lines[1]}",
                timeout=10000,
                no_defaults=True,
            )
            target_id = owned.target_id
            found = await find_target_page(browser, target_id)
            if found is None:
                raise LookupError("target_missing")
            self.browser, self.target_id = browser, target_id
            self._listen(*found)
            await found[1].screencast.start(
                on_frame=self.on_frame, quality=_FRAME_QUALITY, size=_FRAME_SIZE
            )
        except Exception as error:
            self.attach_failures += 1
            code = "target_missing" if isinstance(error, LookupError) else "driver_failed"
            _log("attach", "failed", code, len(self.viewers))
            await self._stop_driver()
            if self.attach_failures >= _ATTACH_ATTEMPTS:
                self.broadcast({"type": "notice", "code": "unavailable"})
            return
        self.attach_failures = 0
        self.input_task = asyncio.create_task(self._run_inputs())
        _log("attach", "ok", "OK", len(self.viewers))
        self.broadcast_state()

    async def follow(self, target: DFormScreenTarget) -> None:
        """Under self.lock: the portal confirmed a new form target; recast that page."""
        target_id = target.browser.target_id
        if self.page is None or target_id is None or target_id == self.target_id:
            return
        try:
            await asyncio.wait_for(self.page.screencast.stop(), 2)
            found = await find_target_page(self.browser, target_id)
            if found is None:
                raise LookupError("target_missing")
            self.dialog = self.dialog_id = None
            self.target_id = target_id
            self._listen(*found)
            await found[1].screencast.start(
                on_frame=self.on_frame, quality=_FRAME_QUALITY, size=_FRAME_SIZE
            )
            _log("follow", "ok", "OK", len(self.viewers))
        except Exception as error:
            _log(
                "follow",
                "failed",
                "target_missing" if isinstance(error, LookupError) else "driver_failed",
                len(self.viewers),
            )
            await self.detach()

    async def _stop_driver(self) -> bool:
        owned = self.browser_owned
        self.browser = self.context = self.page = self.target_id = None
        self.dialog = self.dialog_id = None
        if self.driver_start is not None:
            # A start that outlived its wait may still produce a running driver.
            try:
                self.playwright = await asyncio.wait_for(asyncio.shield(self.driver_start), 10)
            except TimeoutError:
                _log("close", "failed", "driver_stop_failed", len(self.viewers))
                return False
            except Exception:
                # The start itself failed: this attempt yields no driver, so the proof
                # it revoked is restored (attach only starts when it was proven).
                self.driver_start = None
                if owned is not None:
                    owned.observer_exited = True
                return True
            self.driver_start = None
        if self.playwright is None:
            return True
        # Started once and shielded: a timed-out wait would cancel Playwright's own
        # stop future and a second stop() returns at once, which proves nothing.
        if self.driver_stop is None:
            self.driver_stop = asyncio.create_task(self.playwright.stop())
        try:
            await asyncio.wait_for(asyncio.shield(self.driver_stop), 10)
        except Exception:
            # Proof stays False and the driver stays referenced: Run cleanup waits on
            # this same stop again, then quarantines instead of releasing.
            _log("close", "failed", "driver_stop_failed", len(self.viewers))
            return False
        self.playwright = self.driver_stop = None
        self.mouse_down = False
        if owned is not None:
            owned.observer_exited = True
        return True

    async def detach(self) -> bool:
        """Under self.lock: stop input, release held buttons, stop frames, stop the driver."""
        self.draining += 1
        try:
            self.epoch += 1
            self.controller = None
            self._drain()
            try:
                async with asyncio.timeout(_DRAIN_SECONDS):
                    async with self.op_lock:
                        clean = await self._release_buttons()
            except TimeoutError:
                clean = False
            if not clean:
                _log("close", "failed", "input_stuck", len(self.viewers))
                # Disconnecting CDP does not release a held button in the page: never
                # reattach to this window (survives this screen object being forgotten).
                if self.browser_owned is not None:
                    self.browser_owned.observer_input_fault = True
            if self.input_task is not None:
                self.input_task.cancel()
                await asyncio.gather(self.input_task, return_exceptions=True)
                self.input_task = None
            if self.page is not None:
                try:
                    await asyncio.wait_for(self.page.screencast.stop(), 2)
                except Exception:
                    pass  # The driver stop below ends the cast in any case.
            attached = self.playwright is not None
            stopped = await self._stop_driver()
            if attached:
                code = "driver_stop_failed" if not stopped else "OK" if clean else "input_stuck"
                _log(
                    "close",
                    "ok" if code == "OK" else "failed",
                    code,
                    len(self.viewers),
                    frames_sent=self.frames_sent,
                    inputs_applied=self.inputs_applied,
                    inputs_rejected=self.inputs_rejected,
                )
            self.frame = None
            self.broadcast_state()
            return stopped and clean
        finally:
            self.draining -= 1

    async def _release_buttons(self) -> bool:
        """True once no button is held (keys are press-only); a failed up stays recorded."""
        if not self.mouse_down or self.page is None:
            return True
        try:
            await asyncio.wait_for(self.page.mouse.up(), 2)
        except Exception:
            return False
        self.mouse_down = False
        return True

    # ----- watch loop ------------------------------------------------------

    async def watch(self) -> None:
        try:
            while not self.closed:
                if not self.viewers:
                    async with self.lock:
                        if not self.viewers and not self.closed:
                            await self.detach()
                            self.attach_failures = 0
                    # No await from this check to clearing self.watcher: a viewer
                    # that joined during detach keeps this loop, any later one
                    # finds no watcher and starts its own.
                    if not self.viewers:
                        return
                    continue
                target = self.service.runtime.screen_target(self.run_id)
                if target is None or target.task_id != self.task_id:
                    await self.service.close_run(self.run_id)
                    return
                if target.phase != self.phase:
                    self.phase = target.phase
                    self.broadcast_state()
                async with self.lock:
                    if self.closed:
                        return
                    if self.page is None and self.attach_failures < _ATTACH_ATTEMPTS:
                        await self.attach(target)
                    elif self.page is not None:
                        await self.follow(target)
                await asyncio.sleep(_POLL_SECONDS)
        except Exception:
            _log("close", "failed", "error", len(self.viewers))
            async with self.lock:
                await self.detach()
            self.broadcast({"type": "notice", "code": "unavailable"})
        finally:
            self.watcher = None
            if not self.viewers:
                self.service.forget(self)

    # ----- control ---------------------------------------------------------

    async def take(self, viewer: _Viewer) -> None:
        if self.controller is not None or self.pending is not None or self.draining:
            viewer.push({"type": "take_result", "ok": False, "reason": "busy"})
            _log("take", "failed", "busy", len(self.viewers))
            return
        # Single writer from here on: no second request passes until this one ends.
        self.pending = viewer.id
        reason = "unavailable"
        try:
            target = self.service.runtime.screen_target(self.run_id)
            phase = None if target is None else target.phase
            if phase == "filling":
                if not self.service.runtime.request_takeover(self.run_id):
                    reason = "takeover_failed"
                    return
                self.broadcast_state()
                deadline = time.monotonic() + _TAKEOVER_SECONDS
                # Filling, then receipt and seal ("preparing") until the worker holds
                # the sealed handoff; a failed fill or cancel ends the target instead.
                while phase in {"filling", "preparing"} and not viewer.closed:
                    if time.monotonic() >= deadline:
                        reason = "timeout"
                        return
                    await asyncio.sleep(_POLL_SECONDS)
                    target = self.service.runtime.screen_target(self.run_id)
                    phase = None if target is None else target.phase
            if phase == "preparing":
                reason = "not_ready"  # Login and portal preparation are never handed over.
                return
            if phase != "handoff" or viewer.closed or self.closed or self.page is None:
                return  # Failed fill, ended Run or a cancel that won first.
            try:
                # Fresh owner/session/Run check, including a pending cancel request.
                await viewer.recheck()
            except BrowserScreenError:
                return
            if viewer.closed or self.closed or self.page is None or self.draining:
                return
            self.epoch += 1
            self.controller = viewer.id
            self.phase = phase
            reason = "OK"
        finally:
            self.pending = None
            if reason == "OK":
                viewer.push({"type": "take_result", "ok": True})
                _log("take", "ok", "OK", len(self.viewers))
            else:
                viewer.push(
                    {
                        "type": "take_result",
                        "ok": False,
                        "reason": "unavailable" if reason == "takeover_failed" else reason,
                    }
                )
                _log("take", "failed", reason, len(self.viewers))
            self.broadcast_state()

    async def release(self, viewer: _Viewer) -> None:
        if self.controller != viewer.id or self.draining:
            return
        # Stop accepting first; queued but unexecuted input is discarded. The controller
        # stays set so nobody can take over until the action drained and the button is up.
        self.draining += 1
        try:
            self.epoch += 1
            self._drain()
            try:
                async with asyncio.timeout(_DRAIN_SECONDS):
                    async with self.op_lock:
                        released = await self._release_buttons()
            except TimeoutError:
                released = False
            if not released:
                _log("release", "failed", "input_stuck", len(self.viewers))
                async with self.lock:
                    await self.detach()
                return
            self.controller = None
        finally:
            self.draining -= 1
        _log("release", "ok", "OK", len(self.viewers))
        self.broadcast_state()

    async def _stop_stuck_input(self) -> None:
        """Spawned by the input loop after a failed button release; detach cancels that loop."""
        try:
            _log("release", "failed", "input_stuck", len(self.viewers))
            async with self.lock:
                await self.detach()
        finally:
            self.draining -= 1

    def _drain(self) -> None:
        while not self.inputs.empty():
            self.inputs.get_nowait()
            self.inputs_rejected += 1

    def enqueue(self, viewer: _Viewer, item: ScreenInput) -> None:
        if self.controller != viewer.id or self.page is None or self.closed or self.draining:
            self.inputs_rejected += 1
            return
        try:
            self.inputs.put_nowait((self.epoch, viewer.id, item))
        except asyncio.QueueFull:
            raise BrowserScreenError("overflow") from None

    async def _run_inputs(self) -> None:
        while True:
            epoch, viewer_id, item = await self.inputs.get()
            if epoch != self.epoch or viewer_id != self.controller or self.page is None:
                self.inputs_rejected += 1
                continue
            async with self.op_lock:
                try:
                    applied = await asyncio.wait_for(self._apply(item), _INPUT_SECONDS)
                except Exception:
                    applied = False
            if applied:
                self.inputs_applied += 1
                continue
            self.inputs_rejected += 1
            if isinstance(item, ScreenMouse) and item.action == "up" and self.mouse_down:
                # The button may still be down and later moves would drag: stop the channel.
                self.draining += 1
                self.service.spawn(self._stop_stuck_input())
                return

    async def _apply(self, item: ScreenInput) -> bool:
        page, frame = self.page, self.frame
        if page is None or frame is None:
            return False
        if isinstance(item, ScreenMouse):
            inside = 0 <= item.x <= frame.width and 0 <= item.y <= frame.height
            if item.action == "up":
                if not self.mouse_down:
                    return False
                # Dragged out of the picture: release at the edge, never keep it held.
                x = min(max(item.x, 0), frame.width)
                y = min(max(item.y, 0), frame.height)
                await page.mouse.move(x, y)
                await page.mouse.up()
                self.mouse_down = False
                return True
            if not inside:
                return False
            await page.mouse.move(item.x, item.y)
            if item.action == "down":
                self.mouse_down = True
                await page.mouse.down()
            elif item.action == "wheel":
                await page.mouse.wheel(
                    max(-_WHEEL_LIMIT, min(_WHEEL_LIMIT, item.delta_x)),
                    max(-_WHEEL_LIMIT, min(_WHEEL_LIMIT, item.delta_y)),
                )
            return True
        if isinstance(item, ScreenKey):
            if item.key not in SCREEN_KEYS:
                return False
            await page.keyboard.press(item.key)
            return True
        if isinstance(item, ScreenText):
            if not 0 < len(item.text) <= SCREEN_TEXT_MAX:
                return False
            await page.keyboard.insert_text(item.text)
            return True
        return False

    async def answer_dialog(
        self, viewer: _Viewer, dialog_id: str, accept: bool, prompt: str | None
    ) -> None:
        dialog = self.dialog
        # Only the controller of a sealed handoff answers; never while automation runs.
        if (
            dialog is None
            or self.controller != viewer.id
            or self.phase != "handoff"
            or self.draining
            or dialog_id != self.dialog_id
            or prompt is not None
            and len(prompt) > SCREEN_PROMPT_MAX
        ):
            self.inputs_rejected += 1
            return
        self.dialog = self.dialog_id = None
        try:
            async with asyncio.timeout(_INPUT_SECONDS):
                if accept:
                    await dialog.accept(prompt) if prompt is not None else await dialog.accept()
                else:
                    await dialog.dismiss()
            _log("dialog", "ok", "OK", len(self.viewers))
        except Exception:
            _log("dialog", "failed", "error", len(self.viewers))
        self.broadcast({"type": "dialog_closed"})

    # ----- viewers ---------------------------------------------------------

    async def leave(self, viewer: _Viewer) -> None:
        if self.controller == viewer.id:
            await self.release(viewer)
        viewer.closed = True
        viewer.wake.set()
        self.viewers.pop(viewer.id, None)
        _log("close", "ok", "OK", len(self.viewers))
        if not self.viewers and self.watcher is None:
            async with self.lock:
                if not self.viewers:
                    await self.detach()
            self.service.forget(self)


class DFormRemoteScreen:
    """BrowserScreenPort for D fill-stop Runs owned by this process's DFormRuntime."""

    def __init__(self, runtime: DFormRuntime, access: BrowserRunAccessPort) -> None:
        self.runtime, self.access = runtime, access
        self._screens: dict[str, _RunScreen] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        runtime.install_screen_closer(self.close_run)

    def spawn(self, work: Any) -> None:
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def forget(self, screen: _RunScreen) -> None:
        # A driver not proven stopped stays registered: the Run's close waits on it again.
        if (
            self._screens.get(screen.run_id) is screen
            and not screen.viewers
            and screen.playwright is None
        ):
            self._screens.pop(screen.run_id, None)

    async def check_access(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str
    ) -> None:
        """Owner, session binding and Run state through the existing access port."""
        try:
            response = await self.access.get(principal, bound_session, task_id, run_id)
        except BrowserChatError:
            raise BrowserScreenError("denied") from None
        run = response.run
        if run.result is not None or run.cancel.requested or run.run_id != run_id:
            raise BrowserScreenError("closed")
        target = self.runtime.screen_target(run_id)
        if target is None or target.task_id != task_id:
            raise BrowserScreenError("closed")

    async def open(
        self, principal: Principal, bound_session: str, task_id: str, run_id: str
    ) -> _Viewer:
        try:
            await self.check_access(principal, bound_session, task_id, run_id)
        except BrowserScreenError:
            _log("open", "failed", "denied", 0)
            raise BrowserScreenError("denied") from None
        screen = self._screens.get(run_id)
        if screen is None or screen.closed:
            screen = self._screens[run_id] = _RunScreen(self, task_id, run_id)
        if screen.task_id != task_id:
            raise BrowserScreenError("denied")
        if len(screen.viewers) >= _MAX_VIEWERS:
            _log("open", "failed", "busy", len(screen.viewers))
            raise BrowserScreenError("busy")
        viewer = _Viewer(screen, principal, bound_session)
        screen.viewers[viewer.id] = viewer
        _log("open", "ok", "OK", len(screen.viewers))
        screen.sync(viewer)
        if screen.watcher is None:
            screen.watcher = asyncio.create_task(screen.watch())
        return viewer

    async def close_run(self, run_id: str) -> None:
        """Runtime hook before stop proofs and cleanup; idempotent and never raises."""
        screen = self._screens.get(run_id)
        if screen is None:
            return
        try:
            async with screen.lock:
                screen.closed = True
                screen.broadcast({"type": "closing"})
                await screen.detach()
        except Exception:
            _log("close", "failed", "error", len(screen.viewers))
        finally:
            for viewer in list(screen.viewers.values()):
                viewer.closed = True
                viewer.wake.set()
            # Kept until proven stopped, so the runtime's next close can still prove it.
            if screen.playwright is None and self._screens.get(run_id) is screen:
                self._screens.pop(run_id, None)

    async def aclose(self) -> None:
        for run_id in list(self._screens):
            await self.close_run(run_id)
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
