"""Finite local form-ready flow. No Agent, LLM, credentials or business output."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import psutil
from playwright.async_api import async_playwright, TimeoutError as PWTimeout, Error as PWError

# Temporary approved D demo policy, fixed by the publication source digest.
# Keep origin/target ownership, cancellation and actual field verification.
DEMO_EXTRA_GUARDS_DISABLED = True

FIELDS = ('报修类别', '故障位置', '故障描述')
MODES = {'READ_REPORTER', 'FILL_STOP'}
CODES = {
    'OK', 'CONFIG_FAILED', 'SESSION_BUSY', 'LOGIN_REQUIRED', 'PAGE_CHANGED',
    'UNEXPECTED_DIALOG', 'DOWNLOAD_BLOCKED', 'EGRESS_BLOCKED',
    'SOURCE_URL_PARSE', 'SOURCE_URL_CREDENTIALS', 'SOURCE_URL_SCHEME',
    'SOURCE_URL_HOST', 'SOURCE_URL_PORT', 'SOURCE_ORIGIN_MISMATCH',
    'SOURCE_SET_CHANGED', 'CONTROL_MISSING', 'CONTROL_AMBIGUOUS',
    'CONTROL_CHANGED', 'PAGE_STATE_MISMATCH', 'ACTION_TIMEOUT',
    'OBSERVE_TIMEOUT', 'CONNECT_TIMEOUT', 'RESULT_MISMATCH',
    'REPORTER_MISSING', 'REPORTER_AMBIGUOUS', 'REPORTER_INVALID',
    'CANCELLED', 'CLEANUP_FAILED', 'REQUEST_GUARD_FAILED', 'PREP_FAILED',
    'PAGE_CONTEXT_EVENT', 'PAGE_MAIN_FRAME_NAV', 'PAGE_CHILD_FRAME_NAV',
    'PAGE_TARGET_CLOSED', 'PAGE_CONTEXT_SET_CHANGED', 'PAGE_FRAME_SET_CHANGED',
    'WINDOW_PREP_FAILED',
    'WINDOW_LOOKUP_FAILED', 'WINDOW_SET_FAILED', 'WINDOW_VERIFY_FAILED',
}

# One metadata line for the first actual EGRESS decision of a Flow. Only fixed
# enums and booleans; URL, host, path and request content are never logged.
EGRESS_FORMAT=('d_form_egress_first source=%s scheme=%s default_port=%s mismatch=%s'
    ' method=%s navigation=%s prior_violation=%s phase=%s')
EGRESS_PHASES={'CONNECT','PREFLIGHT','LOCAL_WAIT','EXECUTE','VERIFY','REPORTER_WAIT','CLEANUP',
    'PORTAL_PREP','PORTAL_ENTRY','PORTAL_ENTERED'}
_egress_log=logging.getLogger('uvicorn.error.d_form')

# Reused from the proven local DOM helpers. Labels are workflow structure;
# desired business values are supplied through the local request, never here.
DOM = r'''
const names=['报修类别','故障位置','故障描述','报修人','报修人姓名'];
const norm=s=>(s||'').replace(/[\s:：*＊]/g,'');
const visible=e=>e.isConnected && e.getClientRects().length>0;
function label(e){
  const direct=[...(e.labels||[])].map(x=>x.textContent);
  direct.push(e.getAttribute('aria-label'),e.getAttribute('placeholder'));
  for(const text of direct){const n=norm(text);if(names.includes(n))return n;}
  for(let x=e.parentElement,i=0;x&&i<4;x=x.parentElement,i++){
    const labels=[...x.querySelectorAll('label,.el-form-item__label,.ant-form-item-label,.layui-form-label')]
      .filter(visible).map(y=>norm(y.textContent)).filter(y=>names.includes(y));
    if(new Set(labels).size===1)return labels[0];
    const found=names.filter(n=>norm(x.textContent).includes(n));
    const canonical=[...new Set(found.map(n=>n==='报修人姓名'?'报修人':n))];
    if(canonical.length===1)return canonical[0];
  }
  const box=e.getBoundingClientRect();
  const groups='form,tr,.form-group,.form-row,.el-form-item,.ant-form-item,.layui-form-item';
  const group=e.closest(groups);if(!group)return '';
  const captions=[...document.querySelectorAll('label,td,th,span,div')]
    .filter(x=>visible(x)&&x.children.length===0&&names.includes(norm(x.textContent)))
    .map(x=>({node:x,name:norm(x.textContent),box:x.getBoundingClientRect()}))
    .filter(x=>x.node.closest(groups)===group&&x.box.right<=box.left+2&&box.left-x.box.right<=240
      &&Math.abs((x.box.top+x.box.bottom-box.top-box.bottom)/2)
        <=Math.max(8,Math.min(x.box.height,box.height)/2));
  return captions.length===1?captions[0].name:'';
}
function categoryWidgets(){
  const all=[...document.querySelectorAll('[role="combobox"],[role="listbox"]')]
    .filter(e=>visible(e)&&label(e)==='报修类别');
  return all.filter(e=>!all.some(parent=>parent!==e&&parent.contains(e)));
}
'''

FIND = '(field)=>{'+DOM+r'''
const native=[...document.querySelectorAll('input,textarea,select')]
  .filter(e=>visible(e)&&!['password','file'].includes((e.type||'').toLowerCase())&&label(e)===field);
if(field!=='报修类别')return native;
const selects=native.filter(e=>e.tagName==='SELECT');const custom=categoryWidgets();
if(selects.length>1)return selects;
if(selects.length===1){
  if(!custom.length||(custom.length===1&&custom[0].contains(selects[0])))return selects;
  return [...selects,...custom];
}
return custom.length?custom:native;
}'''

IDENTITY = '(e)=>{'+DOM+r'''
const forbidden=x=>['password','file','submit','image','reset'].includes((x.type||'').toLowerCase());
let blocked=false,navigation=false;
for(let p=e;p;p=p.parentElement){
  if(p.matches('button,input')&&forbidden(p))blocked=true;
  if(p.tagName==='A'&&p.getAttribute('href'))navigation=true;
}
if([...e.querySelectorAll('button,input')].some(forbidden))blocked=true;
if([...e.querySelectorAll('a[href]')].some(a=>!!a.getAttribute('href')))navigation=true;
return {connected:visible(e),tag:e.tagName,type:(e.type||'').toLowerCase(),
  role:e.getAttribute('role')||'',label:label(e),readonly:!!e.readOnly,
  disabled:!!e.disabled,blocked_activation:blocked,navigation,
  href:e.closest('a')?.getAttribute('href')||'',url:e.ownerDocument.location.href};
}'''

ASSOCIATED_OPTIONS = '(e)=>{'+DOM+r'''
const related=new Set();
for(const node of [e,...e.querySelectorAll('[aria-controls],[aria-owns]')])
  for(const attribute of ['aria-controls','aria-owns'])
    for(const id of (node.getAttribute(attribute)||'').split(/\s+/).filter(Boolean))related.add(id);
const containers=[];
for(const id of related){
  const matches=[...document.querySelectorAll('[id]')].filter(node=>node.id===id);
  if(matches.length>1)return matches;
  if(matches.length===1&&visible(matches[0]))containers.push(matches[0]);
}
const ids=new Set([e,...e.querySelectorAll('[id]')].map(node=>node.id).filter(Boolean));
for(const list of document.querySelectorAll('[role="listbox"]'))
  if(visible(list)&&(list.getAttribute('aria-labelledby')||'').split(/\s+/).some(id=>ids.has(id)))containers.push(list);
if(e.querySelector('[role="option"],li,dd'))containers.push(e);
const all=[...new Set(containers)].filter(node=>visible(node)
  &&[...node.querySelectorAll('[role="option"],li,dd')].some(visible));
return all.filter(node=>!all.some(parent=>parent!==node&&parent.contains(node)));
}'''

VISIBLE_LISTS = r'''()=>[...document.querySelectorAll('[role="listbox"],ul,ol')]
 .filter(e=>e.isConnected&&e.getClientRects().length>0
  &&[...e.querySelectorAll('[role="option"],li,dd')].some(x=>x.getClientRects().length>0))'''
HAS_OPTION_REFERENCES = r'''e=>{
 for(const x of [e,...e.querySelectorAll('[aria-controls],[aria-owns]')])
  for(const a of ['aria-controls','aria-owns'])
   if((x.getAttribute(a)||'').trim())return true;
 const ids=new Set([e,...e.querySelectorAll('[id]')].map(x=>x.id).filter(Boolean));
 return [...e.ownerDocument.querySelectorAll('[role="listbox"]')].some(x=>
  (x.getAttribute('aria-labelledby')||'').split(/\s+/).some(id=>ids.has(id)));
}'''
CAUSAL_OPTIONS = r'''(before,e)=>{
 const visible=x=>x.isConnected&&x.getClientRects().length>0;
 if(!visible(e)||e.getAttribute('aria-expanded')!=='true')return [];
 const a=e.getBoundingClientRect();
 const lists=[...e.ownerDocument.querySelectorAll('[role="listbox"],ul,ol')].filter(x=>{
  if(before.includes(x)||!visible(x)||x===e||x.contains(e)
   ||x.matches('form,input,textarea,select,button,a[href]')
   ||x.querySelector('form,input,textarea,select,button,a[href]'))return false;
  if(![...x.querySelectorAll('[role="option"],li,dd')].some(visible))return false;
  const b=x.getBoundingClientRect();const overlap=Math.min(a.right,b.right)-Math.max(a.left,b.left);
  return b.width>=a.width*.5&&Math.abs(b.left-a.left)<=32
   &&overlap>=Math.min(a.width,b.width)*.8
   &&Math.min(Math.abs(b.top-a.bottom),Math.abs(a.top-b.bottom))<=24;
 });
 return lists.filter(x=>!lists.some(p=>p!==x&&p.contains(x)));
}'''

OPTION_ENABLED = r'''e=>{
 for(let p=e;p;p=p.parentElement)
  if(p.disabled||p.getAttribute('aria-disabled')==='true')return false;
 return true;
}'''

VALUE = '''e=>e.tagName==='SELECT'?[...e.selectedOptions].map(o=>o.textContent.trim()).join('')
  :['INPUT','TEXTAREA'].includes(e.tagName)?e.value:(e.innerText||'').trim()'''

REPORTER = '()=>{'+DOM+r'''
const native=[...document.querySelectorAll('input,textarea')]
  .filter(e=>visible(e)&&!['password','file'].includes((e.type||'').toLowerCase())
    &&['报修人','报修人姓名'].includes(label(e)));
if(native.length)return {ambiguous:native.length>1,values:native.map(e=>e.value)};
const captions=[...document.querySelectorAll('label,td,th,dt,span,div,p')]
  .filter(e=>visible(e)&&e.children.length===0)
  .filter(e=>['报修人','报修人姓名'].includes(norm(e.textContent))
    || /^(报修人姓名|报修人)\s*[:：]\s*\S/.test((e.textContent||'').trim()));
const result=new Map();let ambiguous=false;
const valid=t=>!!t&&t.length<=100&&!/[\r\n\u0085\u2028\u2029]/.test(t)
  &&!names.some(n=>norm(t).includes(n));
function value(e){
  if(!e||!visible(e))return null;
  const all=e.matches('input,textarea,select')?[e]:[...e.querySelectorAll('input,textarea,select')];
  if(all.some(x=>['password','file'].includes((x.type||'').toLowerCase())))return null;
  const controls=all.filter(visible);let t;
  if(controls.length){if(controls.length!==1||controls[0].tagName==='SELECT')return null;t=controls[0].value;}
  else{if(e.querySelector('button,[contenteditable="true"]'))return null;t=(e.innerText||'').trim();}
  return valid(t)?t:null;
}
for(const c of captions){
  const inline=(c.textContent||'').trim().match(/^(报修人姓名|报修人)\s*[:：]\s*(\S.*)$/);
  if(inline){if(valid(inline[2]))result.set(c,inline[2]);continue;}
  const nodes=[];if(c.control)nodes.push(c.control);
  if(c.tagName==='DT')nodes.push(c.nextElementSibling?.matches('dd')?c.nextElementSibling:null);
  const cell=c.closest('td,th');if(cell&&['TD','TH'].includes(cell.nextElementSibling?.tagName))nodes.push(cell.nextElementSibling);
  let w=c;for(let i=0;i<3&&w;i++,w=w.parentElement){if(norm(w.textContent)!==norm(c.textContent))break;nodes.push(w.nextElementSibling);}
  const a=[...new Set(nodes.filter(Boolean))].map(node=>({node,text:value(node)})).filter(x=>x.text!==null);
  const outer=a.filter(x=>!a.some(y=>y.node!==x.node&&y.node.contains(x.node)));
  if(outer.length>1){ambiguous=true;continue;}
  if(outer.length===1){result.set(outer[0].node,outer[0].text);continue;}
  const group=c.closest('tr,.form-group,.form-row,.el-form-item,.ant-form-item,.layui-form-item')||c.parentElement?.parentElement;
  if(!group)continue;const box=c.getBoundingClientRect();
  const row=e=>{const b=e.getBoundingClientRect();return Math.abs((b.top+b.bottom-box.top-box.bottom)/2)<=Math.max(8,Math.min(b.height,box.height)/2);};
  const leaves=[...group.querySelectorAll('label,td,th,span,div,p,dd,input,textarea')]
    .filter(e=>visible(e)&&e!==c&&e.children.length===0&&row(e));
  const end=Math.min(Infinity,...leaves.filter(e=>names.includes(norm(e.textContent)))
    .map(e=>e.getBoundingClientRect().left).filter(x=>x>=box.right-2));
  const near=leaves.filter(e=>{const b=e.getBoundingClientRect();return b.left>=box.right-2&&b.left-box.right<=240&&b.right<=end+2;})
    .map(node=>({node,text:value(node)})).filter(x=>x.text!==null);
  if(near.length>1)ambiguous=true;if(near.length===1)result.set(near[0].node,near[0].text);
}
return {ambiguous,values:[...result.values()]};
}'''


class Stop(Exception):
    """Only a fixed code is retained. Never use provider/DOM exception strings."""

    def __init__(self, code):
        super().__init__(code if code in CODES else 'PREP_FAILED')
        self.code = self.args[0]


def need(value, code):
    if not value:
        raise Stop(code)


@dataclass(frozen=True)
class Binding:
    pid: int
    created: float
    executable: str
    profile: str
    profile_root: str
    port: int
    context_id: str
    target_id: str
    host: str
    scheme: str = 'http'
    source_port: int | None = None

    @classmethod
    def parse(cls, value):
        need(type(value) is dict and set(value) in (set(cls.__dataclass_fields__),
             set(cls.__dataclass_fields__)-{'source_port'}), 'CONFIG_FAILED')
        binding = cls(**value)
        need(type(binding.pid) is int and binding.pid>0 and type(binding.port) is int
             and 0<binding.port<=65535 and type(binding.created) in (int,float), 'CONFIG_FAILED')
        for name in ('executable','profile','profile_root','context_id','target_id','host','scheme'):
            need(isinstance(getattr(binding,name),str) and 0<len(getattr(binding,name))<=4096,'CONFIG_FAILED')
        try:
            ipaddress.ip_address(binding.host)
        except ValueError:
            raise Stop('CONFIG_FAILED') from None
        need(binding.scheme in ('http','https'),'CONFIG_FAILED')
        need(binding.source_port is None or type(binding.source_port) is int
             and 0<binding.source_port<=65535,'CONFIG_FAILED')
        return binding


def origin(value):
    try:
        parsed=urlsplit(value)
        need(parsed.username is None and parsed.password is None,'SOURCE_URL_CREDENTIALS')
        default=443 if parsed.scheme in ('https','wss') else 80
        port=parsed.port if parsed.port is not None else default
        return parsed.scheme,parsed.hostname,port
    except (ValueError,TypeError):
        raise Stop('SOURCE_URL_PARSE') from None


class Flow:
    def __init__(self, request, emit, cancel):
        need(type(request) is dict and set(request)=={'mode','binding','inputs','local_gate'},'CONFIG_FAILED')
        need(request['mode'] in MODES and type(request['local_gate']) is bool,'CONFIG_FAILED')
        self.binding=Binding.parse(request['binding'])
        self.mode=request['mode'];self.inputs=request['inputs'];self.local_gate=request['local_gate']
        if self.mode=='FILL_STOP':
            need(type(self.inputs) is dict and set(self.inputs)=={'category','position','description'},'CONFIG_FAILED')
            need(all(isinstance(v,str) and 0<len(v.strip())<=500 and '\x00' not in v for v in self.inputs.values()),'CONFIG_FAILED')
            need(len(self.inputs['category'])<=100 and len(self.inputs['category'].splitlines())==1,'CONFIG_FAILED')
        else:need(self.inputs is None,'CONFIG_FAILED')
        self.emit=emit;self.cancel=cancel;self.violation=None;self.pw=None;self.browser=None
        self.context=None;self.page=None;self.cdp=None;self.sessions=[];self.probes=[]
        self.allowed=set();self.pages=set();self.frame_ids=set();self.route_installed=False;self.ws_installed=False
        self.http_route=None;self.ws_route=None;self.handlers=[];self.mutex=None;self.observe_depth=0
        self.helper_cleanup_failed=False;self.open_category=None
        self.egress_recorded=False
        self.phase='CONNECT';self.began=None;self.frame_urls={}
        self.summary={'mode':self.mode,'passed':False,'error_code':'PREP_FAILED',
            'model_requests':0,'submit_actions':0,'recording_enabled':False,
            'system_egress_isolation':False,'browser_left_open':True,
            'action_attempts':0,'actions_completed':0,'executed_actions':[],
            'window_attempted':False,'window_maximized':False,'window_fullscreen':False,
            'window_before':'UNKNOWN','window_error':None,'window_commands':0,
            'event_counts':{},'checks':{},'reporter_displayed':False,'frame_failure':None,'page_failure':None,
            'timings':{k:0.0 for k in ('connect','window_prep','observe','action_wait','verify','human_wait','cleanup','total')}}

    async def bounded(self, awaitable, timeout, code):
        if self.cancel.is_set():
            if hasattr(awaitable,'close'):awaitable.close()
            raise Stop('CANCELLED')
        task=asyncio.ensure_future(awaitable);stop=asyncio.create_task(self.cancel.wait())
        try:
            done,_=await asyncio.wait((task,stop),timeout=timeout,return_when=asyncio.FIRST_COMPLETED)
            if stop in done:raise Stop('CANCELLED')
            if task not in done:raise Stop(code)
            return await task
        finally:
            for pending in (task,stop):
                if not pending.done():pending.cancel()
            await asyncio.gather(task,stop,return_exceptions=True)

    def event(self, emitter, name, callback):
        emitter.on(name,callback);self.handlers.append((emitter,name,callback))

    def flag(self, code):
        if DEMO_EXTRA_GUARDS_DISABLED and code=='UNEXPECTED_DIALOG':
            return
        if self.violation is None:self.violation=code

    def egress_phase(self):
        return self.phase

    def egress_denied(self, source, value, address, allowed, request=None):
        # Called only where EGRESS_BLOCKED is decided, never on a sticky reread.
        # First decision only; diagnostics never change the rejection itself.
        if self.egress_recorded:
            return
        self.egress_recorded=True
        try:
            differs=min((tuple(a!=b for a,b in zip(address,item)) for item in allowed),
                        key=lambda d:(sum(d),d),default=())
            mismatch='_'.join(n for n,d in zip(('scheme','host','port'),differs) if d) or 'none'
            method='NONE'
            navigation=False
            if request is not None:
                known=('GET','HEAD','POST','OPTIONS')
                method=request.method if request.method in known else 'OTHER'
                navigation=request.is_navigation_request() is True
            phase=self.egress_phase()
            scheme=address[0] if address[0] in ('http','https','ws','wss') else 'other'
            _egress_log.info(EGRESS_FORMAT,source,scheme,
                urlsplit(value).port is None,mismatch,method,navigation,self.violation is not None,
                phase if phase in EGRESS_PHASES else 'OTHER')
        except Exception:
            pass

    def allowed_origin(self, value, source):
        address=origin(value)
        if address not in self.allowed:
            self.egress_denied(source,value,address,self.allowed)
            raise Stop('EGRESS_BLOCKED')

    def source_in_scope(self, value, locked=True):
        try:
            address=origin(value)
        except Stop:
            return False
        return (address[0]==self.binding.scheme and address[1]==self.binding.host
                and 0<address[2]<=65535
                and (self.binding.source_port is None or address[2]==self.binding.source_port)
                and (not locked or address in self.allowed))

    def scan_frame(self, frame, page):
        return (frame.url!='about:blank'
                and (not DEMO_EXTRA_GUARDS_DISABLED or frame is page.main_frame
                     or self.source_in_scope(frame.url)))

    def demo_background_request(self, request):
        if not DEMO_EXTRA_GUARDS_DISABLED:
            return False
        if not request.is_navigation_request():
            return True
        try:
            frame=request.frame
        except PWError:
            return False
        return frame.page is not self.page or frame is not self.page.main_frame

    def demo_resource_allowed(self, request, address):
        # Resource services on the configured host may use another port.
        # Documents, other hosts/protocols and write methods keep their scope.
        return (DEMO_EXTRA_GUARDS_DISABLED and not request.is_navigation_request()
                and request.method in {'GET','HEAD'}
                and address[0]==self.binding.scheme and address[1]==self.binding.host
                and 0<address[2]<=65535)

    def page_changed(self, code, frame=None, page=None):
        if DEMO_EXTRA_GUARDS_DISABLED and self.violation is None:
            if code in {'PAGE_MAIN_FRAME_NAV','PAGE_CHILD_FRAME_NAV'} and frame is not None:
                if code=='PAGE_CHILD_FRAME_NAV' or frame.page is not self.page:
                    return
                try:
                    if frame.url!='about:blank':
                        self.allowed_origin(frame.url,'frame')
                except Stop as error:
                    self.flag(error.code)
                return
            if code=='PAGE_CONTEXT_EVENT' and page is not None and page.context is self.context:
                return
            if code=='PAGE_TARGET_CLOSED' and page is not self.page:
                return
        # Only closed codes, ordinal/depth, booleans and a timer leave the
        # local worker. URLs/target IDs remain private and are never recorded.
        self.flag(code)
        if self.summary['page_failure'] is None:
            ordinal=0;depth=0;known=False;same_url=False
            if frame is not None:
                known=frame in self.frame_urls
                same_url=known and frame.url==self.frame_urls[frame]
                pending=[(self.page.main_frame,0)];index=0
                while pending:
                    current,level=pending.pop();index+=1
                    if current is frame:ordinal=index;depth=level;break
                    pending.extend((child,level+1) for child in reversed(current.child_frames))
            elif page is not None:known=page in self.pages
            self.summary['page_failure']={'condition':code,'phase':self.phase,
                'ordinal':ordinal,'depth':depth,'known_object':known,'same_url':same_url,
                'seconds':round(time.perf_counter()-self.began,3) if self.began is not None else 0.0}

    def frame_navigated(self, frame):
        self.page_changed('PAGE_MAIN_FRAME_NAV' if frame is self.page.main_frame else 'PAGE_CHILD_FRAME_NAV',frame=frame)

    async def observation(self, awaitable):
        outer=self.observe_depth==0;stamp=time.perf_counter();self.observe_depth+=1
        try:return await awaitable
        finally:
            self.observe_depth-=1
            if outer:self.summary['timings']['observe']+=time.perf_counter()-stamp

    async def connect(self):
        b=self.binding;p=psutil.Process(b.pid)
        need(p.create_time()==b.created and Path(p.exe()).resolve()==Path(b.executable).resolve(),'CONFIG_FAILED')
        need(Path(b.profile).resolve().parent==Path(b.profile_root).resolve()
             and '--user-data-dir='+b.profile in p.cmdline(),'CONFIG_FAILED')
        connections=p.net_connections(kind='tcp')
        need(any(c.status==psutil.CONN_LISTEN and c.laddr.ip=='127.0.0.1' and c.laddr.port==b.port for c in connections),'CONFIG_FAILED')
        need(not any(c.status==psutil.CONN_ESTABLISHED and c.laddr.port==b.port for c in connections),'SESSION_BUSY')
        # A process-local named lease prevents two new workers racing attachment.
        if sys.platform=='win32':
            import ctypes,hashlib
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CreateMutexW.restype=ctypes.c_void_p
            name='Local\\EAI_D_FORM_'+hashlib.sha256(f'{b.pid}:{b.created}:{b.port}'.encode()).hexdigest()
            self.mutex=(kernel,kernel.CreateMutexW(None,False,name))
            need(self.mutex[1] and ctypes.get_last_error()!=183,'SESSION_BUSY')
        self.pw=await async_playwright().start()
        self.browser=await self.pw.chromium.connect_over_cdp(f'http://127.0.0.1:{b.port}')
        root=await self.browser.new_browser_cdp_session();self.sessions.append(root)
        need(b.context_id in (await root.send('Target.getBrowserContexts'))['browserContextIds'],'CONFIG_FAILED')
        targets=[]
        for context in self.browser.contexts:
            for page in context.pages:
                cdp=await context.new_cdp_session(page);self.sessions.append(cdp)
                info=(await cdp.send('Target.getTargetInfo'))['targetInfo']
                if info.get('browserContextId')==b.context_id:targets.append((context,page,cdp,info['targetId']))
        matched=[item for item in targets if item[3]==b.target_id]
        need(len(matched)==1 and len(targets)>=1
             and (DEMO_EXTRA_GUARDS_DISABLED or len(targets)<=2),'CONFIG_FAILED')
        self.context,self.page,self.cdp,_=matched[0]
        need(self.page in self.context.pages,'CONFIG_FAILED')
        if not DEMO_EXTRA_GUARDS_DISABLED:
            need(set(self.context.pages)=={item[1] for item in targets},'CONFIG_FAILED')
        self.pages=set(self.context.pages)
        for _,page,cdp,_ in targets:
            tree=(await cdp.send('Page.getFrameTree'))['frameTree']
            self.allowed.update(self.tree_origins(tree,locked=False,
                target_role='BOUND_TARGET' if page is self.page else 'OWNED_AUXILIARY'))
        need(1<=len(self.allowed)<=2,'SOURCE_SET_CHANGED')
        self.frame_ids={f['frame']['id'] for f in self.walk((await self.cdp.send('Page.getFrameTree'))['frameTree'])}
        self.frame_urls={frame:frame.url for frame in self.page.frames}
        self.event(self.context,'page',lambda page:self.page_changed('PAGE_CONTEXT_EVENT',page=page))
        self.event(self.page,'framenavigated',self.frame_navigated)
        self.event(self.page,'close',lambda _:self.page_changed('PAGE_TARGET_CLOSED',page=self.page))
        self.event(self.page,'dialog',lambda _:self.flag('UNEXPECTED_DIALOG'))
        async def download(item):
            self.flag('DOWNLOAD_BLOCKED')
            try:await item.cancel()
            except BaseException:self.flag('DOWNLOAD_BLOCKED')
        self.event(self.page,'download',download)
        async def route(request_route):
            try:
                request=request_route.request;address=origin(request.url)
                if address in self.allowed or self.demo_resource_allowed(request,address):
                    await request_route.continue_()
                else:
                    if not self.demo_background_request(request):
                        self.egress_denied('http',request.url,address,self.allowed,request)
                        self.flag('EGRESS_BLOCKED')
                    await request_route.abort()
            except BaseException:
                self.flag('REQUEST_GUARD_FAILED')
                try:await request_route.abort()
                except BaseException:pass
        self.http_route=route
        await self.context.route('**/*',route);self.route_installed=True
        async def websocket(item):
            try:
                allowed={('wss' if scheme=='https' else 'ws',host,port) for scheme,host,port in self.allowed}
                address=origin(item.url)
                if address in allowed:item.connect_to_server()
                else:
                    if not DEMO_EXTRA_GUARDS_DISABLED:
                        self.egress_denied('ws',item.url,address,allowed)
                        self.flag('EGRESS_BLOCKED')
                    await item.close()
            except BaseException:self.flag('REQUEST_GUARD_FAILED')
        self.ws_route=websocket
        await self.context.route_web_socket('**/*',websocket);self.ws_installed=True

    @staticmethod
    def walk(tree):
        yield tree
        for child in tree.get('childFrames',[]):yield from Flow.walk(child)

    def tree_origins(self, tree, locked, target_role='BOUND_TARGET'):
        result=set();ordinal=0
        def visit(item,depth):
            nonlocal ordinal
            ordinal+=1;index=ordinal;frame=item['frame']
            if (DEMO_EXTRA_GUARDS_DISABLED
                    and (depth>0 or target_role=='OWNED_AUXILIARY')
                    and frame['url']!='about:blank'
                    and (not self.source_in_scope(frame['url'],locked)
                         or (depth>0 and origin(frame['url']) not in (self.allowed|result)))):
                return
            try:
                if frame['url']!='about:blank':
                    address=origin(frame['url']);native=origin(frame['securityOrigin'])
                    need(address[0]==self.binding.scheme,'SOURCE_URL_SCHEME')
                    need(address[1]==self.binding.host,'SOURCE_URL_HOST')
                    need(0<address[2]<=65535,'SOURCE_URL_PORT')
                    if self.binding.source_port is not None:
                        need(address[2]==self.binding.source_port,'SOURCE_URL_PORT')
                    need(native==address,'SOURCE_ORIGIN_MISMATCH')
                    if locked:need(address in self.allowed,'SOURCE_SET_CHANGED')
                    result.add(address)
            except Stop as error:
                self.summary['frame_failure']={'target_role':target_role,'ordinal':index,'depth':depth,'check':error.code}
                raise
            for child in item.get('childFrames',[]):visit(child,depth+1)
        visit(tree,0);return result

    async def check(self):
        need(not self.cancel.is_set(),'CANCELLED')
        if self.violation:raise Stop(self.violation)
        need(self.page is not None,'PAGE_CHANGED')
        if self.page.is_closed():self.page_changed('PAGE_TARGET_CLOSED',page=self.page);raise Stop(self.violation)
        need(self.page in self.context.pages,'PAGE_TARGET_CLOSED')
        if not DEMO_EXTRA_GUARDS_DISABLED and set(self.context.pages)!=self.pages:
            self.page_changed('PAGE_CONTEXT_SET_CHANGED')
            raise Stop(self.violation)
        need(psutil.Process(self.binding.pid).create_time()==self.binding.created,'CONFIG_FAILED')
        await self.bounded(self.maximize(),5,'WINDOW_PREP_FAILED')
        tree=(await self.cdp.send('Page.getFrameTree'))['frameTree']
        self.tree_origins(tree,locked=True)
        if (not DEMO_EXTRA_GUARDS_DISABLED
                and {item['frame']['id'] for item in self.walk(tree)}!=self.frame_ids):
            self.page_changed('PAGE_FRAME_SET_CHANGED')
            raise Stop(self.violation)
        for frame in self.page.frames:
            if not self.scan_frame(frame,self.page):
                continue
            self.allowed_origin(frame.url,'frame')
            need(await frame.locator('input[type="password"]').count()==0,'LOGIN_REQUIRED')
            modal=await frame.locator('[role="dialog"],[role="alertdialog"],.modal,.el-dialog,.ant-modal,.layui-layer,.k-window').evaluate_all(
                'nodes=>nodes.some(e=>e.isConnected&&e.getClientRects().length>0)')
            need(not modal,'UNEXPECTED_DIALOG')

    async def fields(self):
        await self.check();out={}
        for field in FIELDS:
            candidates=[]
            for frame in self.page.frames:
                if not self.scan_frame(frame,self.page):
                    continue
                array=await frame.evaluate_handle(FIND,field)
                try:
                    count=await array.evaluate('a=>a.length')
                    need(count<=1,'CONTROL_AMBIGUOUS')
                    if count:
                        node=(await array.get_property('0')).as_element()
                        need(node is not None,'CONTROL_CHANGED');candidates.append((frame,node))
                finally:await array.dispose()
            need(candidates,'CONTROL_MISSING');need(len(candidates)==1,'CONTROL_AMBIGUOUS')
            out[field]=candidates[0]
        return out

    async def state(self, fields):
        values={}
        for field,(_,node) in fields.items():
            info=await node.evaluate(IDENTITY)
            need(info['connected'] and info['label']==field,'CONTROL_CHANGED')
            if self.mode=='FILL_STOP':need(not info['disabled'],'CONTROL_CHANGED')
            self.allowed_origin(info['url'],'frame')
            need(info['type'] not in ('password','file','submit','image','reset'),'CONTROL_CHANGED')
            if field!=FIELDS[0]:
                need(info['tag'] in ('INPUT','TEXTAREA'),'CONTROL_CHANGED')
                if self.mode=='FILL_STOP':need(not info['readonly'],'CONTROL_CHANGED')
            else:
                need(info['tag']=='SELECT' or info['role'] in ('combobox','listbox')
                     or info['tag']=='INPUT' and info['readonly'],'CONTROL_CHANGED')
                if info['tag']!='SELECT':need(await node.get_attribute('aria-expanded')=='false','PAGE_STATE_MISMATCH')
            values[field]=await node.evaluate(VALUE)
        return values

    async def local_box(self, text, title, confirm=False):
        need(sys.platform=='win32','CONFIG_FAILED')
        flags=(0x4|0x20|0x100 if confirm else 0x40)|0x10000|0x40000
        code='import ctypes,sys;raise SystemExit(ctypes.windll.user32.MessageBoxW(None,sys.stdin.buffer.read().decode("utf-8"),'+repr(title)+','+str(flags)+'))'
        helper=await asyncio.create_subprocess_exec(sys.executable,'-B','-c',code,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        start=time.perf_counter()
        try:
            await self.bounded(helper.communicate(text.encode('utf-8')),600,'CANCELLED')
            return helper.returncode==(6 if confirm else 1)
        finally:
            self.summary['timings']['human_wait']+=time.perf_counter()-start
            if helper.returncode is None:
                try:
                    helper.kill();await asyncio.wait_for(helper.wait(),5)
                except BaseException:
                    self.helper_cleanup_failed=True
                    raise Stop('CLEANUP_FAILED') from None

    async def probe(self, node, event):
        key='__eai_d_'+uuid.uuid4().hex
        await node.evaluate('''(e,a)=>{const state={count:0};state.callback=()=>{state.count++};
          Object.defineProperty(e,a.key,{value:state,configurable:true});e.addEventListener(a.event,state.callback,true);}''',{'key':key,'event':event})
        self.probes.append((node,key,event));return key

    async def action(self, code, node, operation, event):
        await self.bounded(self.observation(self.check()),15,'OBSERVE_TIMEOUT')
        stamp=time.perf_counter()
        before=await node.evaluate(IDENTITY)
        need(before['connected'] and not before['disabled'],'CONTROL_CHANGED')
        need(not before['blocked_activation'] and not before['navigation'],'CONTROL_CHANGED')
        self.allowed_origin(before['url'],'frame')
        need(before['type'] not in ('password','file','submit','image','reset'),'CONTROL_CHANGED')
        if code in ('POSITION_INPUT','DESCRIPTION_INPUT'):
            expected=FIELDS[1] if code=='POSITION_INPUT' else FIELDS[2]
            need(before['label']==expected and before['tag'] in ('INPUT','TEXTAREA')
                 and not before['readonly'],'CONTROL_CHANGED')
            key='position' if code=='POSITION_INPUT' else 'description'
            need(await node.evaluate(VALUE) in ('',self.inputs[key]),'PAGE_STATE_MISMATCH')
        elif code=='CATEGORY_OPEN':
            need(before['label']==FIELDS[0] and (before['role'] in ('combobox','listbox')
                 or before['tag']=='INPUT' and before['readonly']),'CONTROL_CHANGED')
            need(await node.get_attribute('aria-expanded')=='false','PAGE_STATE_MISMATCH')
        elif code=='CATEGORY_SELECT':
            if before['tag']=='SELECT':
                need(before['label']==FIELDS[0],'CONTROL_CHANGED')
                selected=await node.evaluate_handle('''(e,t)=>[...e.options].filter(o=>o.textContent.trim()===t)''',self.inputs['category'])
                try:
                    need(await selected.evaluate('a=>a.length')==1,'CONTROL_CHANGED')
                    item=(await selected.get_property('0')).as_element()
                    need(item is not None and await item.evaluate(OPTION_ENABLED),'CONTROL_CHANGED')
                finally:await selected.dispose()
            else:
                valid=await node.evaluate('''(e,t)=>!!e.closest('[role="option"],li,dd')
                  &&(e.innerText||'').trim()===t&&!e.querySelector('input,textarea,select,button')''',self.inputs['category'])
                need(valid,'CONTROL_CHANGED')
                need(await node.evaluate(OPTION_ENABLED),'CONTROL_CHANGED')
                need(self.open_category is not None,'CONTROL_CHANGED')
                widget,container=self.open_category
                need(await widget.get_attribute('aria-expanded')=='true','CONTROL_CHANGED')
                need(await container.evaluate('(c,e)=>c.isConnected&&c.getClientRects().length>0&&c.contains(e)',node),'CONTROL_CHANGED')
        else:raise Stop('CONFIG_FAILED')
        if before['href'] and urlsplit(before['href']).netloc:self.allowed_origin(before['href'],'href')
        key=await self.probe(node,event)
        self.summary['timings']['observe']+=time.perf_counter()-stamp
        self.summary['action_attempts']+=1;start=time.perf_counter()
        try:
            await self.bounded(operation(),20,'ACTION_TIMEOUT')
            count=await node.evaluate('(e,k)=>e[k]?.count||0',key)
            self.summary['event_counts'][code]=count
            need(count>0,'RESULT_MISMATCH')
            self.summary['actions_completed']+=1;self.summary['executed_actions'].append(code)
        except PWTimeout:
            raise Stop('ACTION_TIMEOUT') from None
        finally:self.summary['timings']['action_wait']+=time.perf_counter()-start
        await self.bounded(self.observation(self.check()),15,'OBSERVE_TIMEOUT')

    async def select_category(self, field):
        frame,node=field;wanted=self.inputs['category']
        if await node.evaluate('e=>e.tagName')=='SELECT':
            options=await node.evaluate('(e,t)=>[...e.options].filter(o=>o.textContent.trim()===t).length',wanted)
            need(options==1,'CONTROL_MISSING' if options==0 else 'CONTROL_AMBIGUOUS')
            await self.action('CATEGORY_SELECT',node,lambda:node.select_option(label=wanted,timeout=20000),'change')
            return
        need(await node.get_attribute('aria-expanded')=='false','PAGE_STATE_MISMATCH')
        before=None;referenced_before=False
        async def open_widget():
            nonlocal before,referenced_before
            # action() has completed identity/source/fullscreen checks. Snapshot
            # here, immediately before the one already-authorized widget click.
            referenced_before=await node.evaluate(HAS_OPTION_REFERENCES)
            before=await frame.evaluate_handle(VISIBLE_LISTS)
            await node.click(timeout=20000)
        containers=None
        container=None
        try:
            await self.action('CATEGORY_OPEN',node,open_widget,'click')
            need(await node.get_attribute('aria-expanded')=='true','PAGE_STATE_MISMATCH')
            containers=await node.evaluate_handle(ASSOCIATED_OPTIONS)
            count=await containers.evaluate('a=>a.length')
            if count==0:
                need(not referenced_before and not await node.evaluate(HAS_OPTION_REFERENCES),'CONTROL_MISSING')
                await containers.dispose();containers=None
                # The click and native source checks were already proven by
                # action(). Bind only one newly visible adjacent safe list in
                # this exact frame; never choose from an arbitrary global list.
                containers=await before.evaluate_handle(CAUSAL_OPTIONS,node)
                count=await containers.evaluate('a=>a.length')
            need(count==1,'CONTROL_MISSING' if count==0 else 'CONTROL_AMBIGUOUS')
            container=(await containers.get_property('0')).as_element()
            need(container is not None,'CONTROL_CHANGED')
        finally:
            if containers is not None:await containers.dispose()
            if before is not None:await before.dispose()
        self.open_category=(node,container)
        array=await container.evaluate_handle('''(root,text)=>{const all=[...root.querySelectorAll('[role="option"],li,dd')]
          .filter(e=>e.isConnected&&e.getClientRects().length>0&&(e.innerText||'').trim()===text);
          return all.filter(e=>!all.some(p=>p!==e&&p.contains(e)));}''',wanted)
        try:
            count=await array.evaluate('a=>a.length')
            need(count==1,'CONTROL_MISSING' if count==0 else 'CONTROL_AMBIGUOUS')
            option=(await array.get_property('0')).as_element();need(option is not None,'CONTROL_CHANGED')
            await self.action('CATEGORY_SELECT',option,lambda:option.click(timeout=20000),'click')
        finally:
            self.open_category=None;await array.dispose()

    async def maximize(self):
        # Required maximized preparation; bind the native target first, before
        # any page DOM observation. Source/page guards remain active afterwards.
        stamp=time.perf_counter()
        try:
            root=self.sessions[0]
            target=(await root.send('Target.getTargetInfo',{'targetId':self.binding.target_id}))['targetInfo']
            need(target.get('browserContextId')==self.binding.context_id,'CONFIG_FAILED')
            need(psutil.Process(self.binding.pid).create_time()==self.binding.created,'CONFIG_FAILED')
            failure='WINDOW_LOOKUP_FAILED'
            try:
                info=await root.send('Browser.getWindowForTarget',{'targetId':self.binding.target_id})
                state=info['bounds']['windowState']
                if state in ('normal','minimized','maximized','fullscreen'):self.summary['window_before']=state
                failure='WINDOW_SET_FAILED'
                if state!='maximized':
                    if state!='normal':
                        self.summary['window_attempted']=True;self.summary['window_commands']+=1
                        await root.send('Browser.setWindowBounds',{'windowId':info['windowId'],'bounds':{'windowState':'normal'}})
                    self.summary['window_attempted']=True;self.summary['window_commands']+=1
                    await root.send('Browser.setWindowBounds',{'windowId':info['windowId'],
                        'bounds':{'windowState':'maximized'}})
                    await asyncio.sleep(0.25)
                failure='WINDOW_VERIFY_FAILED'
                current=await root.send('Browser.getWindowBounds',{'windowId':info['windowId']})
                self.summary['window_maximized']=current['bounds']['windowState']=='maximized'
                self.summary['window_fullscreen']=current['bounds']['windowState']=='fullscreen'
                need(self.summary['window_maximized'],'WINDOW_VERIFY_FAILED')
            except PWError:
                self.summary['window_error']=failure
                raise Stop(failure)
        finally:self.summary['timings']['window_prep']+=time.perf_counter()-stamp

    async def verify(self):
        start=time.perf_counter()
        try:
            await self.observation(self.check())
            fields=await self.observation(self.fields());values=await self.observation(self.state(fields))
            expected=dict(zip(FIELDS,(self.inputs['category'],self.inputs['position'],self.inputs['description'])))
            self.summary['checks']={code:values[name]==expected[name] for code,name in zip(('CATEGORY','POSITION','DESCRIPTION'),FIELDS)}
            need(all(self.summary['checks'].values()),'RESULT_MISMATCH')
            need({'CATEGORY_SELECT','POSITION_INPUT','DESCRIPTION_INPUT'}<=set(self.summary['executed_actions']),'RESULT_MISMATCH')
        finally:self.summary['timings']['verify']+=time.perf_counter()-start

    async def reporter_value(self):
        await self.check();values=[]
        for frame in self.page.frames:
            if not self.scan_frame(frame,self.page):
                continue
            found=await frame.evaluate(REPORTER)
            need(not found['ambiguous'],'REPORTER_AMBIGUOUS');values.extend(found['values'])
        need(values,'REPORTER_MISSING');need(len(values)==1,'REPORTER_AMBIGUOUS')
        value=values[0]
        need(isinstance(value,str) and 0<len(value.strip())<=100 and len(value.splitlines())==1
             and all(ord(c)>=32 for c in value),'REPORTER_INVALID')
        await self.check();return value.strip()

    async def reporter(self):
        stamp=time.perf_counter()
        value=await self.bounded(self.observation(self.reporter_value()),15,'OBSERVE_TIMEOUT')
        self.summary['timings']['verify']+=time.perf_counter()-stamp
        self.phase='REPORTER_WAIT'
        displayed=await self.local_box('报修人：'+value+'\n\n来自当前专属浏览器申请单，仅在本地显示。\n点击“确定”关闭显示，不会提交。',
                                     'D local result - reporter only')
        self.phase='VERIFY'
        need(displayed,'CANCELLED');self.summary['reporter_displayed']=True
        current=await self.bounded(self.observation(self.reporter_value()),15,'OBSERVE_TIMEOUT')
        need(current==value,'RESULT_MISMATCH')

    async def cleanup(self):
        clean=not self.helper_cleanup_failed
        for node,key,event in self.probes:
            try:
                await asyncio.wait_for(node.evaluate('''(e,a)=>{const s=e[a.key];if(s){e.removeEventListener(a.event,s.callback,true);delete e[a.key];}}''',{'key':key,'event':event}),2)
            except BaseException:clean=False
        for emitter,name,callback in self.handlers:
            try:emitter.remove_listener(name,callback)
            except BaseException:clean=False
        if self.context and self.route_installed:
            try:await asyncio.wait_for(self.context.unroute('**/*',self.http_route),3)
            except BaseException:clean=False
        # Pinned Playwright has no public WebSocket unroute method. Its own
        # BrowserContextDispatcher._onDispose uninstalls this connection's
        # WebSocket binding/init script when pw.stop releases the driver.
        for session in self.sessions:
            try:await asyncio.wait_for(session.detach(),2)
            except BaseException:clean=False
        if self.pw:
            try:await asyncio.wait_for(self.pw.stop(),10)
            except BaseException:clean=False
        if self.mutex:
            try:
                kernel,handle=self.mutex;kernel.CloseHandle.argtypes=[__import__('ctypes').c_void_p]
                if handle:need(kernel.CloseHandle(handle),'CLEANUP_FAILED')
            except BaseException:clean=False
        return clean

    async def run(self):
        began=time.perf_counter();stage=3;stamp=began
        self.began=began
        try:
            await self.bounded(self.connect(),20,'CONNECT_TIMEOUT')
            self.summary['timings']['connect']=time.perf_counter()-stamp;self.emit(3,'PASS',time.perf_counter()-stamp,'OK')
            stage=4;stamp=time.perf_counter();self.phase='PREFLIGHT'
            await self.bounded(self.maximize(),5,'WINDOW_PREP_FAILED')
            fields=await self.bounded(self.observation(self.fields()),15,'OBSERVE_TIMEOUT')
            values=await self.bounded(self.observation(self.state(fields)),15,'OBSERVE_TIMEOUT')
            if self.mode=='FILL_STOP':
                need(all(values[name] in ('',self.inputs[key]) for name,key in zip(FIELDS[1:],('position','description'))),'PAGE_STATE_MISMATCH')
            self.emit(4,'PASS',time.perf_counter()-stamp,'OK')
            if self.mode=='FILL_STOP':
                if self.local_gate:
                    self.phase='LOCAL_WAIT'
                    text=('确定性验证，零 LLM。仅本轮一次，不提交。\n\n类别：'+self.inputs['category']
                          +'\n故障位置：'+self.inputs['position']+'\n故障描述：'+self.inputs['description']
                          +'\n\n确认现在无人手动编辑此专属浏览器，然后选择“是”开始。选择“否”停止。')
                    if self.summary['window_error']:
                        text+='\n\n自动窗口管理未成功；请先在本地调整窗口，确认当前申请单布局完整。'
                    need(await self.local_box(text,'D deterministic run - local coordination',True),'CANCELLED')
                stage=5;stamp=time.perf_counter();self.phase='EXECUTE'
                await self.bounded(self.observation(self.check()),15,'OBSERVE_TIMEOUT')
                # Reobserve after human wait, with the same overwrite boundary.
                fields=await self.bounded(self.observation(self.fields()),15,'OBSERVE_TIMEOUT')
                values=await self.bounded(self.observation(self.state(fields)),15,'OBSERVE_TIMEOUT')
                need(all(values[name] in ('',self.inputs[key]) for name,key in zip(FIELDS[1:],('position','description'))),'PAGE_STATE_MISMATCH')
                await self.bounded(self.select_category(fields[FIELDS[0]]),100,'ACTION_TIMEOUT')
                for field,key,code in zip(FIELDS[1:],('position','description'),('POSITION_INPUT','DESCRIPTION_INPUT')):
                    node=fields[field][1]
                    await self.bounded(self.action(code,node,lambda n=node,k=key:n.fill(self.inputs[k],timeout=20000),'input'),70,'ACTION_TIMEOUT')
                self.emit(5,'PASS',time.perf_counter()-stamp,'OK')
                stage=6;stamp=time.perf_counter();self.phase='VERIFY';await self.bounded(self.verify(),15,'RESULT_MISMATCH')
                self.emit(6,'PASS',time.perf_counter()-stamp,'OK')
            # A's finite fill mode stops after the three-field verification.
            # Reporter display remains a separate standalone read operation.
            if self.mode=='READ_REPORTER':
                stage=6;stamp=time.perf_counter();self.phase='VERIFY';await self.bounded(self.reporter(),660,'CANCELLED')
                self.emit(6,'PASS',max(0,time.perf_counter()-stamp-self.summary['timings']['human_wait']),'OK')
            if self.mode=='FILL_STOP' and self.violation is not None:
                self.summary['passed']=False
                self.summary['error_code']=self.violation
                self.emit(stage,'FAIL',time.perf_counter()-stamp,self.violation)
                return self.summary
            self.summary['passed']=True;self.summary['error_code']='OK'
        except Stop as error:
            self.summary['error_code']=error.code;self.emit(stage,'FAIL',time.perf_counter()-stamp,error.code)
        except PWTimeout:
            code='CONNECT_TIMEOUT' if stage==3 else 'ACTION_TIMEOUT' if stage==5 else 'OBSERVE_TIMEOUT'
            self.summary['error_code']=code;self.emit(stage,'FAIL',time.perf_counter()-stamp,code)
        except PWError:
            code='CONFIG_FAILED' if stage==3 else 'CONTROL_CHANGED'
            self.summary['error_code']=code;self.emit(stage,'FAIL',time.perf_counter()-stamp,code)
        except psutil.Error:
            self.summary['error_code']='CONFIG_FAILED';self.emit(stage,'FAIL',time.perf_counter()-stamp,'CONFIG_FAILED')
        except BaseException:
            self.summary['error_code']='PREP_FAILED';self.emit(stage,'FAIL',time.perf_counter()-stamp,'PREP_FAILED')
        finally:
            self.phase='CLEANUP'
            stamp=time.perf_counter();clean=await self.cleanup()
            self.summary['timings']['cleanup']=time.perf_counter()-stamp
            if self.mode=='FILL_STOP' and self.summary['passed'] and self.violation is not None:
                self.summary['passed']=False
                self.summary['error_code']=self.violation
            self.emit(7,'PASS' if clean else 'FAIL',time.perf_counter()-stamp,'OK' if clean else 'CLEANUP_FAILED')
            if not clean:self.summary['passed']=False;self.summary['error_code']='CLEANUP_FAILED'
            self.summary['timings']['total']=time.perf_counter()-began
            self.summary['timings']['active_total']=max(0,self.summary['timings']['total']-self.summary['timings']['human_wait'])
            self.summary['timing_nested']={'verify_includes_observe':True,'active_excludes_human_wait':True}
            self.summary['timings']={k:round(v,3) for k,v in self.summary['timings'].items()}
            self.emit(8,'PASS' if self.summary['passed'] else 'FAIL',time.perf_counter()-began,self.summary['error_code'])
        return self.summary
