"""Finite approved portal preparation, reusing D's local browser guards."""
import asyncio
import time
from dataclasses import replace

from .d_form_flow import FIELDS, FIND, Flow, Stop, need, origin

MODAL='[role="dialog"],[role="alertdialog"],.modal,.el-dialog,.ant-modal,.layui-layer,.k-window'
CLOSE='.el-dialog__headerbtn,.ant-modal-close,.layui-layer-close,.k-i-close,.k-svg-i-x,[aria-label="Close"],[aria-label="关闭"],[title="关闭"],[title="Close"]'
SAFE=r'''e=>{const a=e.closest('button,input');const t=(a?.type||'').toLowerCase();
const h=e.closest('a')?.getAttribute('href')||'';
return e.isConnected&&e.getClientRects().length>0&&!['password','file','reset','image'].includes(t)
&&!(t==='submit'&&a.form)&&(!h||['javascript:void(0)','javascript:void(0);','javascript:;'].includes(h));}'''
SAFE_TYPES=r'''e=>[e,e.closest('button,input'),...e.querySelectorAll('button,input')].filter(Boolean).every(x=>{
const t=(x.type||'').toLowerCase();return !['password','file','reset','image'].includes(t)&&!(t==='submit'&&x.form);})'''
IS_CLOSE=r'''e=>['','x','X','×','关闭','Close'].includes((e.innerText||'').trim())
&&!e.querySelector('a,button,input')&&(e.innerText||'').indexOf('送祝福')<0'''
KNOWN_INFORMATION=r'''e=>!e.querySelector('form,input,textarea,select,[contenteditable="true"]')
&&[...e.querySelectorAll('a,button,span,div')].some(x=>x.isConnected&&x.getClientRects().length>0
&&x.children.length===0&&(x.innerText||'').trim()==='送祝福')
&&[...e.querySelectorAll('button')].filter(x=>x.getClientRects().length>0)
.every(x=>['','x','X','×','关闭','Close','送祝福'].includes((x.innerText||'').trim()))'''
INFORMATION_FLAGS=r'''e=>{
 const visible=x=>x.isConnected&&x.getClientRects().length>0;
 const links=[...e.querySelectorAll('a,button')].filter(visible);
 const unknown=links.filter(x=>!['','x','X','×','关闭','Close','送祝福'].includes((x.innerText||'').trim()));
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
}'''


class PortalFlow(Flow):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.menu_pending=False
        self.summary.update(popup_closed=False,menu_opened=False,form_ready=False)
        self.summary['timings'].update(popup_locate=0.0,popup_click=0.0,popup_disappear=0.0,menu_ready=0.0)

    def page_changed(self,code,frame=None,page=None):
        # Only a dispatched, exact approved menu may change the page/frame set.
        # Origins, context ownership, PID, unknown dialogs and downloads remain
        # guarded during this bounded transition. No permissive retry exists.
        expected={'PAGE_MAIN_FRAME_NAV','PAGE_CHILD_FRAME_NAV','PAGE_CONTEXT_EVENT'}
        if self.menu_pending and code in expected:
            if page is not None:
                if page.context is not self.context or len(self.context.pages)>2:
                    return super().page_changed('PAGE_CONTEXT_SET_CHANGED',page=page)
                self.watch(page)
            return
        return super().page_changed(code,frame=frame,page=page)

    def watch(self,page):
        self.event(page,'framenavigated',lambda frame,p=page:self.page_changed(
            'PAGE_MAIN_FRAME_NAV' if frame is p.main_frame else 'PAGE_CHILD_FRAME_NAV',frame=frame))
        self.event(page,'close',lambda _,p=page:self.page_changed('PAGE_TARGET_CLOSED',page=p))
        self.event(page,'dialog',lambda _:self.flag('UNEXPECTED_DIALOG'))
        async def download(item):
            self.flag('DOWNLOAD_BLOCKED')
            try:await item.cancel()
            except BaseException:self.flag('DOWNLOAD_BLOCKED')
        self.event(page,'download',download)

    async def sources(self,page,cdp,stable=True):
        need(self.violation is None,self.violation or 'PAGE_CHANGED')
        need(not self.cancel.is_set(),'CANCELLED')
        need(not page.is_closed(),'PAGE_TARGET_CLOSED')
        need(1<=len(self.context.pages)<=2,'PAGE_CONTEXT_SET_CHANGED')
        if stable:need(set(self.context.pages)==self.pages,'PAGE_CONTEXT_SET_CHANGED')
        info=(await cdp.send('Target.getTargetInfo'))['targetInfo']
        need(info.get('browserContextId')==self.binding.context_id,'CONFIG_FAILED')
        self.binding=replace(self.binding,target_id=info['targetId'])
        await self.maximize()
        tree=(await cdp.send('Page.getFrameTree'))['frameTree']
        self.tree_origins(tree,locked=True)
        if stable:
            need({f['frame']['id'] for f in self.walk(tree)}==self.frame_ids,'PAGE_FRAME_SET_CHANGED')
        for frame in page.frames:
            if frame.url=='about:blank':continue
            need(origin(frame.url) in self.allowed,'EGRESS_BLOCKED')
            need(await frame.locator('input[type="password"]').count()==0,'LOGIN_REQUIRED')
        return info,tree

    async def close_information(self):
        locate_started=time.perf_counter()
        await self.sources(self.page,self.cdp)
        visible=[]
        for candidate in await self.page.locator(MODAL).element_handles():
            if await candidate.is_visible():visible.append(candidate)
        outer=[]
        for candidate in visible:
            if not any([await other.evaluate('(e,c)=>e.contains(c)',candidate)
                        for other in visible if other is not candidate]):outer.append(candidate)
        self.summary['popup_modal_count']=len(outer)
        if not outer:
            self.summary['timings']['popup_locate']=time.perf_counter()-locate_started
            self.emit(9,'PASS',self.summary['timings']['popup_locate'],'OK');return
        need(len(outer)==1,'UNEXPECTED_DIALOG');modal=outer[0]
        self.summary['popup_shape_flags']=await modal.evaluate(INFORMATION_FLAGS)
        need(await modal.evaluate(KNOWN_INFORMATION),'UNEXPECTED_DIALOG')
        close=[]
        for candidate in await modal.query_selector_all(CLOSE):
            if not await candidate.is_visible():continue
            node=(await candidate.evaluate_handle('e=>e.closest("button,a")||e')).as_element()
            need(node is not None,'CONTROL_CHANGED')
            if not any([await node.evaluate('(e,p)=>e===p',prior) for prior in close]):close.append(node)
        need(len(close)==1,'CONTROL_MISSING' if not close else 'CONTROL_AMBIGUOUS')
        target=close[0]
        await self.sources(self.page,self.cdp)
        need(await modal.evaluate(KNOWN_INFORMATION) and await target.evaluate(SAFE)
             and await target.evaluate(SAFE_TYPES) and await target.evaluate(IS_CLOSE),'CONTROL_CHANGED')
        probe=await self.probe(target,'click')
        self.summary['timings']['popup_locate']=time.perf_counter()-locate_started
        self.emit(9,'PASS',self.summary['timings']['popup_locate'],'OK')
        self.summary['action_attempts']+=1;stamp=time.perf_counter()
        try:
            await target.click(timeout=10000)
            need(await target.evaluate('(e,k)=>e[k]?.count||0',probe)>0,'RESULT_MISMATCH')
            self.summary['timings']['popup_click']=time.perf_counter()-stamp
            self.emit(10,'PASS',self.summary['timings']['popup_click'],'OK')
            hidden_started=time.perf_counter()
            await modal.wait_for_element_state('hidden',timeout=10000)
            self.summary['timings']['popup_disappear']=time.perf_counter()-hidden_started
            self.emit(11,'PASS',self.summary['timings']['popup_disappear'],'OK')
        finally:self.summary['timings']['action_wait']+=time.perf_counter()-stamp
        await self.check()
        self.summary['actions_completed']+=1;self.summary['executed_actions'].append('POPUP_CLOSE')
        self.summary['popup_closed']=True

    async def open_menu(self):
        await self.check();candidates=[]
        for frame in self.page.frames:
            if frame.url=='about:blank':continue
            await self.sources(self.page,self.cdp)
            for candidate in await frame.get_by_text('运维管理',exact=True).element_handles():
                if await candidate.is_visible():candidates.append(candidate)
        need(len(candidates)==1,'CONTROL_MISSING' if not candidates else 'CONTROL_AMBIGUOUS')
        target=candidates[0]
        await self.check()
        need(await target.evaluate('''e=>e.isConnected&&e.getClientRects().length>0
            &&(e.innerText||'').trim()==='运维管理'&&!e.closest('form')
            &&!e.querySelector('input,textarea,select,[contenteditable="true"]')'''),'CONTROL_CHANGED')
        need(await target.evaluate(SAFE_TYPES),'CONTROL_CHANGED')
        href=await target.evaluate('e=>e.closest("a")?.href||""')
        if href and not href.startswith('javascript:'):need(origin(href) in self.allowed,'EGRESS_BLOCKED')
        # Probe belongs to a document that may unload after this authorized click;
        # completion is proven by the unique native-owned form, not old handles.
        self.summary['action_attempts']+=1;stamp=time.perf_counter();self.menu_pending=True
        try:
            await target.click(timeout=10000)
            chosen=None;cdps={self.page:self.cdp}
            for _ in range(60):
                need(self.violation is None,self.violation or 'PAGE_CHANGED')
                found=[]
                for page in self.context.pages:
                    if page not in cdps:
                        cdps[page]=await self.context.new_cdp_session(page);self.sessions.append(cdps[page])
                    cdp=cdps[page]
                    info,tree=await self.sources(page,cdp,stable=False)
                    counts={name:0 for name in FIELDS}
                    for frame in page.frames:
                        if frame.url=='about:blank':continue
                        for name in FIELDS:
                            await self.sources(page,cdp,stable=False)
                            array=await frame.evaluate_handle(FIND,name)
                            try:counts[name]+=await array.evaluate('a=>a.length')
                            finally:await array.dispose()
                    need(all(v<=1 for v in counts.values()),'CONTROL_AMBIGUOUS')
                    if all(v==1 for v in counts.values()):found.append((page,cdp,info,tree))
                need(len(found)<=1,'CONTROL_AMBIGUOUS')
                if found:chosen=found[0];break
                await asyncio.sleep(0.25)
            need(chosen is not None,'CONTROL_MISSING')
            self.page,self.cdp,info,_=chosen
            # Control discovery awaited several times after its earlier tree.
            # Validate the chosen native page again, then freeze the latest tree.
            info,_=await self.sources(self.page,self.cdp,stable=False)
            tree=(await self.cdp.send('Page.getFrameTree'))['frameTree']
            self.tree_origins(tree,locked=True)
            self.binding=replace(self.binding,target_id=info['targetId'])
            self.pages=set(self.context.pages)
            self.frame_ids={f['frame']['id'] for f in self.walk(tree)}
            self.frame_urls={frame:frame.url for frame in self.page.frames}
        finally:
            self.menu_pending=False
            self.summary['timings']['action_wait']+=time.perf_counter()-stamp
        await self.check();await self.fields()
        self.summary['actions_completed']+=1;self.summary['executed_actions'].append('MENU_OPEN')
        self.summary['menu_opened']=True
        self.summary['timings']['menu_ready']=time.perf_counter()-stamp
        self.emit(12,'PASS',self.summary['timings']['menu_ready'],'OK')

    async def prepare_page(self):
        await self.prepare()

    async def prepare(self):
        self.phase='PORTAL_PREP'
        for page in self.context.pages:self.watch(page)
        await self.maximize()
        await self.close_information()
        try:await self.fields()
        except Stop as error:
            if error.code!='CONTROL_MISSING':raise
            await self.open_menu()
        await self.check();await self.fields()
        self.summary['form_ready']=True
