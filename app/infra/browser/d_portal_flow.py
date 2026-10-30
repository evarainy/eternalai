"""Finite approved portal preparation, reusing D's local browser guards."""

import asyncio
import time
from dataclasses import replace
from typing import TypedDict, cast

from playwright.async_api import (
    BrowserContext,
    CDPSession,
    Download,
    ElementHandle,
    Frame,
    Page,
)
from playwright.async_api import Error as PWError

from .d_form_flow import (
    FIELDS,
    FIND,
    Flow,
    FlowEmit,
    FrameTree,
    Stop,
    need,
)


class TargetInfo(TypedDict):
    targetId: str
    browserContextId: str


MODAL = '[role="dialog"],[role="alertdialog"],.modal,.el-dialog,.ant-modal,.layui-layer,.k-window'
CLOSE = (
    ".el-dialog__headerbtn,.ant-modal-close,.layui-layer-close,.k-i-close,.k-svg-i-x,"
    '[aria-label="Close"],[aria-label="关闭"],[title="关闭"],[title="Close"]'
)
SAFE = r"""e=>{const a=e.closest('button,input');const t=(a?.type||'').toLowerCase();
const h=e.closest('a')?.getAttribute('href')||'';
return e.isConnected&&e.getClientRects().length>0&&!['password','file','reset','image'].includes(t)
&&!(t==='submit'&&a.form)&&(!h||['javascript:void(0)','javascript:void(0);','javascript:;'].includes(h));}"""
SAFE_TYPES = (
    r"""e=>[e,e.closest('button,input'),...e.querySelectorAll("""
    r"""'button,input')].filter(Boolean).every(x=>{
const t=(x.type||'').toLowerCase();return !['password','file','reset',"""
    r"""'image'].includes(t)&&!(t==='submit'&&x.form);})"""
)
IS_CLOSE = r"""e=>['','x','X','×','关闭','Close'].includes((e.innerText||'').trim())
&&!e.querySelector('a,button,input')&&(e.innerText||'').indexOf('送祝福')<0"""
KNOWN_INFORMATION = r"""e=>!e.querySelector('form,input,textarea,select,[contenteditable="true"]')
&&[...e.querySelectorAll('a,button,span,div')].some(x=>x.isConnected&&x.getClientRects().length>0
&&x.children.length===0&&(x.innerText||'').trim()==='送祝福')
&&[...e.querySelectorAll('button')].filter(x=>x.getClientRects().length>0)
.every(x=>['','x','X','×','关闭','Close','送祝福'].includes((x.innerText||'').trim()))"""
INFORMATION_FLAGS = (
    r"""e=>{
 const visible=x=>x.isConnected&&x.getClientRects().length>0;
 const links=[...e.querySelectorAll('a,button')].filter(visible);
 const unknown=links.filter(x=>!['','x','X','×','关闭','Close','送祝福'].includes("""
    r"""(x.innerText||'').trim()));
 return {
  input_free:!e.querySelector('form,input,textarea,select,[contenteditable="true"]'),
  greeting_link:links.some(x=>(x.innerText||'').trim()==='送祝福'),
  only_known_links:links.every(x=>['','x','X','×','关闭','Close','送祝福'].includes((x.innerText||'').trim())),
  greeting_leaf:[...e.querySelectorAll('a,button,span,div')].some(x=>visible(x)&&x.children.length===0&&(x.innerText||'').trim()==='送祝福'),
  unknown_links_count:unknown.length,
  unknown_close_only:unknown.length>0&&unknown.every(x=>x.matches('.el-dialog__headerbtn,.ant-modal-close,.layui-layer-close,.k-i-close,.k-svg-i-x,[aria-label="Close"],[aria-label="关闭"],[title="关闭"],[title="Close"]')||!!x.querySelector('.k-i-close,.k-svg-i-x')),
  unknown_private_glyph_only:unknown.length>0&&unknown.every(x=>/^[\uE000-\uF8FF\s]+$/u.test((x.innerText||'').trim())),
  unknown_greeting_container:unknown.length>0&&unknown.every(x=>[...x.querySelectorAll('span,div')].some(y=>visible(y)&&y.children.length===0&&(y.innerText||'').trim()==='送祝福')),
  unknown_approved_structure:unknown.length>0&&unknown.every(x=>x.matches('[aria-label="Close"],[aria-label="关闭"],[title="关闭"],[title="Close"]')||[...x.querySelectorAll('*')].some(y=>visible(y)&&y.children.length===0&&(y.innerText||'').trim()==='送祝福')),
  unknown_text_only:unknown.length>0&&unknown.every(x=>x.children.length===0),
  unknown_close_caption_known:[...e.querySelectorAll('[aria-label="Close"],[aria-label="关闭"],[title="关闭"],[title="Close"]')].filter(visible).every(x=>['','x','X','×','关闭','Close'].includes((x.innerText||'').trim())),
  close_candidates:e.querySelectorAll('.el-dialog__headerbtn,.ant-modal-close,.layui-layer-close,.k-i-close,.k-svg-i-x,[aria-label="Close"],[aria-label="关闭"],[title="关闭"],[title="Close"]').length
 };
}"""
)


class PortalFlow(Flow):
    def __init__(self, request: object, emit: FlowEmit, cancel: asyncio.Event) -> None:
        super().__init__(request, emit, cancel)
        self.menu_pending = False
        # One-shot fixed portal entry after verified login; independent of menu_pending.
        self.entry_attempted = False
        self.entry_settled = False
        cast(dict[str, object], self.summary).update(
            popup_closed=False, menu_opened=False, form_ready=False
        )
        self.summary["timings"].update(
            popup_locate=0.0, popup_click=0.0, popup_disappear=0.0, menu_ready=0.0
        )

    def page_changed(
        self, code: str, frame: Frame | None = None, page: Page | None = None
    ) -> None:
        # Only a dispatched, exact approved menu may change the page/frame set;
        # origins stay guarded by the context route and per-frame checks.
        expected = {"PAGE_MAIN_FRAME_NAV", "PAGE_CHILD_FRAME_NAV", "PAGE_CONTEXT_EVENT"}
        if self.menu_pending and code in expected:
            if page is not None:
                self.watch(page)
            return
        return super().page_changed(code, frame=frame, page=page)

    def egress_phase(self) -> str:
        # connect() and the one-shot entry both run in CONNECT; split them here.
        if self.phase == "CONNECT" and self.entry_attempted:
            return "PORTAL_ENTERED" if self.entry_settled else "PORTAL_ENTRY"
        return self.phase

    def watch(self, page: Page) -> None:
        self.event(
            page,
            "framenavigated",
            lambda frame, p=page: self.page_changed(
                "PAGE_MAIN_FRAME_NAV" if frame is p.main_frame else "PAGE_CHILD_FRAME_NAV",
                frame=frame,
            ),
        )
        self.event(page, "close", lambda _, p=page: self.page_changed("PAGE_TARGET_CLOSED", page=p))

        async def download(item: Download) -> None:
            await item.cancel()

        self.event(page, "download", download)

    async def sources(self, page: Page, cdp: CDPSession) -> tuple[TargetInfo, FrameTree]:
        need(self.violation is None, self.violation or "PAGE_CHANGED")
        need(not self.cancel.is_set(), "CANCELLED")
        need(not page.is_closed(), "PAGE_TARGET_CLOSED")
        info = cast(TargetInfo, (await cdp.send("Target.getTargetInfo"))["targetInfo"])
        self.binding = replace(self.binding, target_id=info["targetId"])
        tree = cast(FrameTree, (await cdp.send("Page.getFrameTree"))["frameTree"])
        self.tree_origins(tree, locked=True)
        for frame in page.frames:
            if not self.scan_frame(frame, page):
                continue
            self.allowed_origin(frame.url, "frame")
            need(await frame.locator('input[type="password"]').count() == 0, "LOGIN_REQUIRED")
        return info, tree

    async def close_information(self) -> None:
        locate_started = time.perf_counter()
        await self.sources(cast(Page, self.page), cast(CDPSession, self.cdp))
        visible: list[ElementHandle] = []
        for candidate in await cast(Page, self.page).locator(MODAL).element_handles():
            if await candidate.is_visible():
                visible.append(candidate)
        outer: list[ElementHandle] = []
        for candidate in visible:
            if not any(
                [
                    await other.evaluate("(e,c)=>e.contains(c)", candidate)
                    for other in visible
                    if other is not candidate
                ]
            ):
                outer.append(candidate)
        self.summary["popup_modal_count"] = len(outer)
        if not outer:
            self.summary["timings"]["popup_locate"] = time.perf_counter() - locate_started
            self.emit(9, "PASS", self.summary["timings"]["popup_locate"], "OK")
            return
        need(len(outer) == 1, "UNEXPECTED_DIALOG")
        modal = outer[0]
        self.summary["popup_shape_flags"] = cast(
            dict[str, bool | int], await modal.evaluate(INFORMATION_FLAGS)
        )
        need(await modal.evaluate(KNOWN_INFORMATION), "UNEXPECTED_DIALOG")
        close: list[ElementHandle] = []
        for candidate in await modal.query_selector_all(CLOSE):
            if not await candidate.is_visible():
                continue
            node = (await candidate.evaluate_handle('e=>e.closest("button,a")||e')).as_element()
            checked_node = cast(ElementHandle, node)
            if not any([await checked_node.evaluate("(e,p)=>e===p", prior) for prior in close]):
                close.append(checked_node)
        need(len(close) == 1, "CONTROL_MISSING" if not close else "CONTROL_AMBIGUOUS")
        target = close[0]
        need(
            await target.evaluate(SAFE)
            and await target.evaluate(SAFE_TYPES)
            and await target.evaluate(IS_CLOSE),
            "CONTROL_CHANGED",
        )
        self.summary["timings"]["popup_locate"] = time.perf_counter() - locate_started
        self.emit(9, "PASS", self.summary["timings"]["popup_locate"], "OK")
        self.summary["action_attempts"] += 1
        stamp = time.perf_counter()
        try:
            await target.click(timeout=10000)
            self.summary["timings"]["popup_click"] = time.perf_counter() - stamp
            self.emit(10, "PASS", self.summary["timings"]["popup_click"], "OK")
            hidden_started = time.perf_counter()
            await modal.wait_for_element_state("hidden", timeout=10000)
            self.summary["timings"]["popup_disappear"] = time.perf_counter() - hidden_started
            self.emit(11, "PASS", self.summary["timings"]["popup_disappear"], "OK")
        finally:
            self.summary["timings"]["action_wait"] += time.perf_counter() - stamp
        self.summary["actions_completed"] += 1
        self.summary["executed_actions"].append("POPUP_CLOSE")
        self.summary["popup_closed"] = True

    async def open_menu(self) -> None:
        await self.check()
        candidates: list[tuple[Frame, ElementHandle]] = []
        for frame in cast(Page, self.page).frames:
            if not self.scan_frame(frame, cast(Page, self.page)):
                continue
            for candidate in await frame.get_by_text("运维管理", exact=True).element_handles():
                if await candidate.is_visible():
                    candidates.append((frame, candidate))
        need(len(candidates) == 1, "CONTROL_MISSING" if not candidates else "CONTROL_AMBIGUOUS")
        target_frame, target = candidates[0]
        self.allowed_origin(target_frame.url, "frame")
        need(
            await target.evaluate("""e=>e.isConnected&&e.getClientRects().length>0
            &&(e.innerText||'').trim()==='运维管理'&&!e.closest('form')
            &&!e.querySelector('input,textarea,select,[contenteditable="true"]')"""),
            "CONTROL_CHANGED",
        )
        need(await target.evaluate(SAFE_TYPES), "CONTROL_CHANGED")
        href = await target.evaluate('e=>e.closest("a")?.href||""')
        if href and not href.startswith("javascript:"):
            self.allowed_origin(href, "href")
        # Probe belongs to a document that may unload after this authorized click;
        # completion is proven by the unique native-owned form, not old handles.
        self.summary["action_attempts"] += 1
        stamp = time.perf_counter()
        self.menu_pending = True
        try:
            await target.click(timeout=10000)
            chosen = None
            cdps: dict[Page, CDPSession] = {
                cast(Page, self.page): cast(CDPSession, self.cdp)
            }
            for _ in range(60):
                need(self.violation is None, self.violation or "PAGE_CHANGED")
                found: list[tuple[Page, CDPSession, TargetInfo, FrameTree]] = []
                for page in cast(BrowserContext, self.context).pages:
                    if page is not self.page and not self.source_in_scope(page.url):
                        continue
                    if page not in cdps:
                        cdps[page] = await cast(BrowserContext, self.context).new_cdp_session(page)
                        self.sessions.append(cdps[page])
                    cdp = cdps[page]
                    info, tree = await self.sources(page, cdp)
                    counts = {name: 0 for name in FIELDS}
                    for frame in page.frames:
                        if not self.scan_frame(frame, page):
                            continue
                        for name in FIELDS:
                            array = await frame.evaluate_handle(FIND, name)
                            try:
                                counts[name] += await array.evaluate("a=>a.length")
                            finally:
                                await array.dispose()
                    need(all(v <= 1 for v in counts.values()), "CONTROL_AMBIGUOUS")
                    if all(v == 1 for v in counts.values()):
                        found.append((page, cdp, info, tree))
                need(len(found) <= 1, "CONTROL_AMBIGUOUS")
                if found:
                    chosen = found[0]
                    break
                await asyncio.sleep(0.25)
            need(chosen is not None, "CONTROL_MISSING")
            self.page, self.cdp, info, _ = cast(
                tuple[Page, CDPSession, TargetInfo, FrameTree], chosen
            )
            self.binding = replace(self.binding, target_id=info["targetId"])
            self.pages = set(cast(BrowserContext, self.context).pages)
            self.frame_urls = {frame: frame.url for frame in self.page.frames}
        finally:
            self.menu_pending = False
            self.summary["timings"]["action_wait"] += time.perf_counter() - stamp
        self.summary["actions_completed"] += 1
        self.summary["executed_actions"].append("MENU_OPEN")
        self.summary["menu_opened"] = True
        self.summary["timings"]["menu_ready"] = time.perf_counter() - stamp
        self.emit(12, "PASS", self.summary["timings"]["menu_ready"], "OK")

    async def prepare_page(self) -> None:
        await self.prepare()

    async def wait_ready(self) -> None:
        """The portal renders its menu or the form after the document loads.

        Bounded wait only; a page that never shows either is judged by the steps below.
        """
        page = cast(Page, self.page)
        for _ in range(60):
            need(self.violation is None, self.violation or "PAGE_CHANGED")
            need(not self.cancel.is_set(), "CANCELLED")
            for frame in page.frames:
                if not self.scan_frame(frame, page):
                    continue
                try:
                    if await frame.get_by_text("运维管理", exact=True).count():
                        return
                    array = await frame.evaluate_handle(FIND, FIELDS[1])
                    try:
                        if await array.evaluate("a=>a.length"):
                            return
                    finally:
                        await array.dispose()
                except PWError:
                    continue  # A frame replaced while the page is still loading.
            await asyncio.sleep(0.25)

    async def prepare(self) -> None:
        self.phase = "PORTAL_PREP"
        for page in cast(BrowserContext, self.context).pages:
            self.watch(page)
        await self.maximize()
        await self.wait_ready()
        await self.close_information()
        try:
            await self.fields()
        except Stop as error:
            if error.code != "CONTROL_MISSING":
                raise
            await self.open_menu()
        await self.fields()
        self.summary["form_ready"] = True
