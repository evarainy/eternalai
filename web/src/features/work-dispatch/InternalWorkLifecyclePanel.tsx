import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Input, Radio, Select, Space, Tag } from 'antd';
import { ApiError } from '../../api/mutator';
import { useAIDockStore } from '../../stores/aiDockStore';
import { useDraftSession, isCurrentDraftSession } from '../../stores/sessionDraftStore';
import type { DraftSessionToken } from '../../stores/sessionDraftStore';
import { accessLost, logDispatchFailure } from './dispatchErrors';
import { readOptions, optionKey, targetOf } from './dispatchApi';
import type { DispatchOption, DispatchOptionsResponse, OptionScope } from './dispatchApi';
import { browserZone, localTime, timeChoices } from './dispatchTime';
import AttachmentsPanel from './AttachmentsPanel';
import type { AttachmentView } from './AttachmentsPanel';
import { editRemark, lifecycleErrorText, readAttachments, readLifecycle, readLifecycleEvents, readRemark, readRemarkHistory, sendLifecycleCommand } from './workObjectLifecycleApi';
import type { LifecycleCommand, LifecycleEventView, LifecycleOperation, LifecycleStatus, PendingLifecycleCommand, PendingRemark, RemarkHistoryPage, RelationStatus } from './workObjectLifecycleApi';
import styles from './InternalWorkLifecyclePanel.module.css';

export const lifecycleStatusLabels: Record<LifecycleStatus | RelationStatus, string> = {
  assigned: '待本人接单', department_pending: '待部门分配', in_progress: '办理中', awaiting_department_review: '待部门审核',
  awaiting_acceptance: '待派发人验收', submitted: '已对外提交', completed: '已办结', withdrawn: '已撤回',
};
const eventLabels: Record<LifecycleOperation, string> = { assign: '分配承办人', accept: '接单', feedback: '反馈进展', submit: '提交结果',
  complete: '提交办结', department_review: '部门审核', submit_summary: '主办汇总提交', confirm: '确认验收', return: '退回补充',
  revise: '修改要求', withdraw: '撤回任务', extend: '延期', reassign: '改派' };
type Action = { operation: LifecycleOperation; relationId?: string };

export default function InternalWorkLifecyclePanel({ workObjectId, onAccessLost, onChanged }: {
  workObjectId: string; onAccessLost?: () => void; onChanged?: () => void;
}) {
  const token = useDraftSession();
  return token ? <LifecyclePanel key={`${token.generation}:${token.revision}:${workObjectId}`} id={workObjectId} token={token}
    onAccessLost={onAccessLost} onChanged={onChanged} /> : null;
}

function LifecyclePanel({ id, token, onAccessLost, onChanged }: { id: string; token: DraftSessionToken; onAccessLost?: () => void; onChanged?: () => void }) {
  const client = useQueryClient();
  const alive = useRef(true);
  const epoch = useRef(0);
  const requestSequence = useRef(0);
  const sending = useRef(false);
  const remarkSending = useRef(false);
  const [snapshot, setSnapshot] = useState<Awaited<ReturnType<typeof readLifecycle>> | null>(null);
  const [events, setEvents] = useState<LifecycleEventView[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [attachments, setAttachments] = useState<AttachmentView[]>([]);
  const [remark, setRemark] = useState<Awaited<ReturnType<typeof readRemark>> | null>(null);
  const [remarkText, setRemarkText] = useState('');
  const [remarkHistory, setRemarkHistory] = useState<RemarkHistoryPage | null>(null);
  const [pendingRemark, setPendingRemark] = useState<PendingRemark | null>(null);
  const [remarkBusy, setRemarkBusy] = useState(false);
  const [action, setAction] = useState<Action | null>(null);
  const [text, setText] = useState('');
  const [title, setTitle] = useState('');
  const [requirement, setRequirement] = useState('');
  const [receipt, setReceipt] = useState('');
  const [completionMode, setCompletionMode] = useState<'submit_completes' | 'confirm_required'>('submit_completes');
  const [approved, setApproved] = useState(true);
  const [targets, setTargets] = useState<DispatchOption[]>([]);
  const [leadKey, setLeadKey] = useState('');
  const [resultAttachments, setResultAttachments] = useState<AttachmentView[]>([]);
  const [due, setDue] = useState('');
  const [chosenInstant, setChosenInstant] = useState('');
  const [zone] = useState(browserZone);
  const [pending, setPending] = useState<PendingLifecycleCommand | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const choices = useMemo(() => timeChoices(due, zone), [due, zone]);
  const current = (requestEpoch = epoch.current) => alive.current && requestEpoch === epoch.current && isCurrentDraftSession(token);
  const clearPrivate = () => {
    epoch.current += 1; requestSequence.current += 1;
    setSnapshot(null); setEvents([]); setCursor(null); setAttachments([]); setRemark(null); setRemarkText(''); setRemarkHistory(null);
    setPending(null); setPendingRemark(null); setAction(null); setText(''); setTitle(''); setRequirement(''); setReceipt('');
    setTargets([]); setLeadKey(''); setResultAttachments([]); setDue('');
    useAIDockStore.getState().clearPageContext('work-objects');
    useAIDockStore.getState().clearPageContext('work-object-series-history');
    onAccessLost?.();
  };
  const failed = (operation: string, error: unknown) => {
    logDispatchFailure(operation, error);
    if (accessLost(error)) clearPrivate();
    setNotice(lifecycleErrorText(error));
  };
  const refreshBusiness = async () => {
    const requestEpoch = epoch.current; const sequence = ++requestSequence.current;
    const [next, page, files] = await Promise.all([readLifecycle(id), readLifecycleEvents(id), readAttachments(id)]);
    if (!current(requestEpoch) || sequence !== requestSequence.current) return;
    setSnapshot(next); setEvents(page.items); setCursor(page.next_after_version); setAttachments(files);
  };
  const refreshRemark = async () => {
    const requestEpoch = epoch.current;
    const next = await readRemark(id);
    if (current(requestEpoch)) { setRemark(next); setRemarkText(next.view.text); }
  };
  const loadRemarkHistory = async (nextCursor?: string) => {
    const requestEpoch = epoch.current;
    const page = await readRemarkHistory(id, nextCursor);
    if (current(requestEpoch)) setRemarkHistory((old) => nextCursor && old ? { ...page, items: [...old.items, ...page.items.filter((entry) => !old.items.some((existing) => existing.remark_version === entry.remark_version))] } : page);
  };
  const refresh = async () => {
    setNotice('');
    try { await Promise.all([refreshBusiness(), refreshRemark(), loadRemarkHistory()]); }
    catch (error) { if (current()) failed('lifecycle_read', error); }
  };
  useEffect(() => {
    alive.current = true; void refresh();
    return () => { alive.current = false; epoch.current += 1; };
    // Authentication/task key gives this panel one private request lifecycle.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const afterBusiness = () => {
    void client.invalidateQueries({ queryKey: ['work-objects', token.generation] });
    onChanged?.();
  };
  const submit = async (command: PendingLifecycleCommand) => {
    if (!current() || sending.current) return;
    const requestEpoch = epoch.current;
    sending.current = true; setPending(command); setBusy(true); setNotice('');
    let committed = false;
    try {
      const result = await sendLifecycleCommand(id, command); committed = true;
      if (!current(requestEpoch)) return;
      setPending(null); setAction(null); setText(''); setResultAttachments([]);
      setNotice(result.replayed ? '已确认原操作。' : '操作已提交。');
      console.info('[work-dispatch]', { stage: 'request_confirmed', operation: command.body.operation });
      await refreshBusiness();
      if (current(requestEpoch)) afterBusiness();
    } catch (error) {
      if (!current(requestEpoch)) return;
      if (accessLost(error)) failed('lifecycle_command', error);
      else if (committed) { setNotice('操作已提交，最新状态暂未读取，请重新读取核对。'); afterBusiness(); }
      else if (!(error instanceof ApiError) || error.status >= 500) { logDispatchFailure('lifecycle_command', error); setNotice('结果待确认，请重试原请求。'); }
      else {
        setPending(null); failed('lifecycle_command', error);
        if (error.status === 412) {
          setAction(null); setSnapshot(null);
          try { await refreshBusiness(); } catch (readError) { if (current(requestEpoch)) failed('lifecycle_read', readError); }
        }
      }
    } finally { sending.current = false; if (current()) setBusy(false); }
  };
  const start = (next: Action) => {
    if (!snapshot) return;
    setAction(next); setText(''); setTargets([]); setResultAttachments([]); setApproved(true); setLeadKey('');
    setTitle(snapshot.view.title); setRequirement(snapshot.view.requirement); setReceipt(snapshot.view.receipt_requirement);
    setCompletionMode(snapshot.view.completion_mode ?? 'submit_completes');
    setDue(snapshot.view.due_at ? localTime(snapshot.view.due_at, zone) : ''); setChosenInstant(''); setNotice('');
  };
  const submitAction = () => {
    if (!snapshot || !action || pending || busy) return;
    const relation = action.relationId ?? '';
    const content = text.trim();
    let body: LifecycleCommand;
    switch (action.operation) {
      case 'assign':
        if (targets[0]?.kind !== 'user') { setNotice('请从目录明确选择本部门承办人。'); return; }
        body = { operation: 'assign', handling_relation_id: relation, directory_user_id: targets[0].directory_user_id }; break;
      case 'accept': body = { operation: 'accept', handling_relation_id: relation }; break;
      case 'feedback':
      case 'submit':
        if (!content) { setNotice('请填写进展或提交结果；通知、提醒可填写“已阅”。'); return; }
        body = action.operation === 'feedback' ? { operation: 'feedback', handling_relation_id: relation, text: content }
          : { operation: 'submit', handling_relation_id: relation, text: content, attachment_ids: resultAttachments.map((item) => item.attachment_id) }; break;
      case 'complete':
      case 'submit_summary':
        if (!content) { setNotice('请填写本次正式结果。'); return; }
        body = action.operation === 'complete' ? { operation: 'complete', text: content }
          : { operation: 'submit_summary', text: content, attachment_ids: resultAttachments.map((item) => item.attachment_id) }; break;
      case 'department_review':
        if (!approved && !content) { setNotice('退回补充必须填写原因。'); return; }
        body = { operation: 'department_review', handling_relation_id: relation, approved, reason: content }; break;
      case 'confirm': body = { operation: 'confirm' }; break;
      case 'return':
      case 'withdraw':
        if (!content) { setNotice('请填写原因。'); return; }
        body = { operation: action.operation, reason: content }; break;
      case 'revise':
        if (!title.trim()) { setNotice('标题不能为空。'); return; }
        body = { operation: 'revise', title: title.trim(), requirement, receipt_requirement: receipt, completion_mode: completionMode }; break;
      case 'extend': {
        const instant = chosenInstant || (choices.length === 1 ? choices[0]!.instant : '');
        if (!instant) { setNotice('请核对延期日期、时区及重复时刻偏移。'); return; }
        body = { operation: 'extend', due_at: instant }; break;
      }
      case 'reassign': {
        const lead = targets.find((target) => optionKey(target) === leadKey);
        if (!targets.length || snapshot.view.dispatch_mode === 'collaborative' && !lead) { setNotice('请明确新的对象及协作唯一主办。'); return; }
        body = { operation: 'reassign', targets: targets.map(targetOf), lead_target: lead ? targetOf(lead) : null }; break;
      }
    }
    void submit({ body, key: crypto.randomUUID(), etag: snapshot.etag });
  };
  const saveRemark = async (request: PendingRemark) => {
    if (remarkSending.current || !current()) return;
    const requestEpoch = epoch.current;
    remarkSending.current = true; setRemarkBusy(true); setPendingRemark(request); setNotice('');
    let committed = false;
    try {
      const response = await editRemark(id, request); committed = true;
      if (!current(requestEpoch)) return;
      setRemark({ view: response.remark, etag: response.etag }); setRemarkText(response.remark.text); setPendingRemark(null);
      setNotice(response.replayed ? '已确认原备注保存。' : '备注已保存，业务状态与办结时间未改变。');
      console.info('[work-dispatch]', { stage: 'request_confirmed', operation: 'remark_edit' });
      await loadRemarkHistory();
    } catch (error) {
      if (!current(requestEpoch)) return;
      if (accessLost(error)) failed('remark_edit', error);
      else if (committed) setNotice('备注已保存，备注历史暂未读取。');
      else if (!(error instanceof ApiError) || error.status >= 500) { logDispatchFailure('remark_edit', error); setNotice('备注保存结果待确认，请重试原请求。'); }
      else { setPendingRemark(null); failed('remark_edit', error); if (error.status === 412) { try { await refreshRemark(); } catch (readError) { if (current(requestEpoch)) failed('remark_read', readError); } } }
    } finally { remarkSending.current = false; if (current()) setRemarkBusy(false); }
  };
  const view = snapshot?.view;
  const activeRelation = view?.relations.find((relation) => relation.relation_id === action?.relationId);
  const changing = busy || pending !== null;
  return <section className={styles.panel} aria-label="事项办理">
    <div className={styles.toolbar}><h3>{view ? lifecycleStatusLabels[view.status] : '读取办理状态'}</h3><Button disabled={busy || remarkBusy} onClick={() => void refresh()}>重新读取办理</Button></div>
    {notice ? <Alert title={notice} type="info" /> : null}
    {view?.frozen ? <Alert type="info" title={view.status === 'completed' ? '事项已正式办结，业务内容与附件冻结；有权用户仍可编辑备注。' : '事项已撤回，停止业务流转；有权用户仍可编辑备注。'} /> : null}
    {view?.completed_at ? <p>办结时间：{view.completed_at}</p> : null}
    {view?.unavailable_reason ? <Alert type="info" title={lifecycleErrorText(new ApiError(403, view.unavailable_reason, ''))} /> : null}
    {view ? <><p>{view.dispatch_mode === 'collaborative' ? '主协办协作' : '独立办理'} · {view.completion_mode === 'confirm_required' ? '派发人确认后完成' : '正式提交即完成'}</p>
      <div className={styles.relations}>{view.relations.map((relation) => <article key={relation.relation_id} className={styles.relation}>
        <div className={styles.toolbar}><strong>{relation.role === 'lead' ? '主办' : relation.role === 'collaborator' ? '协办' : '承办'} · {relation.target.kind === 'department' ? `部门 ${relation.target.department_id}` : `个人 ${relation.target.directory_user_id}`}</strong><Tag>{lifecycleStatusLabels[relation.status]}</Tag></div>
        {relation.assignee_directory_user_id ? <p>当前承办目录编号：{relation.assignee_directory_user_id}</p> : null}
        {relation.submitted_result !== null ? <p className={styles.text}>已提交结果：{relation.submitted_result}</p> : null}
        {relation.review_result !== null ? <p className={styles.text}>部门审核：{relation.review_result ? '通过' : '退回'} {relation.review_reason}</p> : null}
        {!view.frozen ? <Space wrap>{relation.available_commands.map((operation) => <Button key={operation} disabled={changing}
          onClick={() => start({ operation: operation as LifecycleOperation, relationId: relation.relation_id })}>{eventLabels[operation as LifecycleOperation] ?? operation}</Button>)}</Space> : null}
      </article>)}</div>
      {!view.frozen ? <Space wrap>{view.available_commands.map((operation) => <Button key={operation} disabled={changing}
        onClick={() => start({ operation: operation as LifecycleOperation })}>{eventLabels[operation as LifecycleOperation] ?? operation}</Button>)}</Space> : null}
      {pending ? <Button loading={busy} disabled={busy} onClick={() => void submit(pending)}>重试原办理请求</Button> : null}
      {action && !view.frozen ? <div className={styles.action}>
        <h4>{eventLabels[action.operation]}</h4>
        {action.operation === 'assign' || action.operation === 'reassign' ? <DirectoryTargets token={token} value={targets} onChange={setTargets}
          departmentId={action.operation === 'assign' ? activeRelation?.target.department_id : undefined} onlyUsers={action.operation === 'assign'}
          max={action.operation === 'assign' || view.dispatch_mode !== 'collaborative' ? 1 : 100} disabled={changing} onAccessLost={clearPrivate} /> : null}
        {action.operation === 'reassign' && view.dispatch_mode === 'collaborative' ? <Select aria-label="改派唯一主办" placeholder="选择唯一主办" disabled={changing}
          value={leadKey || undefined} onChange={setLeadKey} options={targets.map((target) => ({ value: optionKey(target), label: target.kind === 'user' ? target.display_name : target.department_display_name }))} /> : null}
        {action.operation === 'revise' ? <>
          <label>标题<Input value={title} maxLength={200} disabled={changing} onChange={(event) => setTitle(event.target.value)} /></label>
          <label>办理要求<Input.TextArea value={requirement} maxLength={10000} rows={4} disabled={changing} onChange={(event) => setRequirement(event.target.value)} /></label>
          <label>回执材料要求<Input.TextArea value={receipt} maxLength={2000} rows={3} disabled={changing} onChange={(event) => setReceipt(event.target.value)} /></label>
          <Radio.Group disabled={changing} value={completionMode} onChange={(event) => setCompletionMode(event.target.value)}><Radio value="submit_completes">正式提交即完成</Radio><Radio value="confirm_required">派发人确认后完成</Radio></Radio.Group>
          <p>实质修改将使原待审结果失效，须重新办理提交。</p>
        </> : null}
        {action.operation === 'extend' ? <><label>新截止时间（{zone}）<input type="datetime-local" value={due} disabled={changing}
          onChange={(event) => { setDue(event.target.value); setChosenInstant(''); }} /></label>
          {choices.length > 1 ? <Select aria-label="截止偏移" value={chosenInstant || undefined} options={choices.map((choice) => ({ label: choice.offset, value: choice.instant }))} onChange={setChosenInstant} /> : null}</> : null}
        {action.operation === 'department_review' ? <Radio.Group disabled={changing} value={approved} onChange={(event) => setApproved(event.target.value)}><Radio value={true}>审核通过并对外提交</Radio><Radio value={false}>退回补充</Radio></Radio.Group> : null}
        {['feedback', 'submit', 'complete', 'department_review', 'submit_summary', 'return', 'withdraw'].includes(action.operation) ? <label>{['return', 'withdraw', 'department_review'].includes(action.operation) ? '原因 / 审核意见' : '办理进展 / 正式结果'}
          <Input.TextArea rows={4} value={text} maxLength={2000} disabled={changing} onChange={(event) => setText(event.target.value)} /></label> : null}
        {action.operation === 'submit' || action.operation === 'submit_summary' ? <AttachmentsPanel purpose="result" visibility={activeRelation?.target.kind === 'department' ? 'department_internal' : 'shared'} value={resultAttachments}
          onChange={setResultAttachments} readOnly={changing} onAccessLost={clearPrivate} /> : null}
        {action.operation === 'confirm' ? <p>请核对正式结果，确认后整体办结冻结。</p> : null}
        {action.operation === 'withdraw' ? <p>撤回停止业务流转，不计为办结，不提供重开入口。</p> : null}
        <Space><Button type="primary" disabled={changing} onClick={submitAction}>确认{eventLabels[action.operation]}</Button><Button disabled={changing} onClick={() => setAction(null)}>取消</Button></Space>
      </div> : null}
      <h4>任务资料与已关联结果附件</h4>
      <AttachmentsPanel workObjectId={id} purpose="task_material" visibility="shared" value={attachments.filter((item) => item.purpose === 'task_material')}
        onChange={(items) => setAttachments((old) => [...old.filter((item) => item.purpose !== 'task_material'), ...items])}
        readOnly={view.frozen || !view.available_commands.includes('revise') || changing} etag={snapshot!.etag}
        onEtagChange={() => { void refreshBusiness().then(afterBusiness).catch((error: unknown) => { if (current()) failed('attachment_refresh', error); }); }} onAccessLost={clearPrivate} />
      {attachments.filter((item) => item.purpose === 'result').map((attachment) => <AttachmentsPanel key={attachment.attachment_id} workObjectId={id}
        handlingRelationId={attachment.handling_relation_id ?? undefined} purpose="result" visibility={attachment.visibility} value={[attachment]} onChange={() => undefined} readOnly onAccessLost={clearPrivate} />)}
    </> : null}
    {remark ? <div className={styles.remark}><h4>共享备注</h4><p>备注对当前有权查看任务的参与者可见。部门内部意见请使用对应办理关系，不写在共享备注中。</p>
      <Input.TextArea aria-label="共享备注" rows={3} maxLength={2000} value={remarkText} readOnly={!remark.view.editable} disabled={remarkBusy || pendingRemark !== null}
        onChange={(event) => setRemarkText(event.target.value)} />
      <p>备注版本 {remark.view.version} · {remark.view.updated_at ?? '尚未编辑'}{remark.view.updated_by ? ` · 编辑账号编号 ${remark.view.updated_by}` : ''}</p>
      {pendingRemark ? <Button loading={remarkBusy} onClick={() => void saveRemark(pendingRemark)}>重试原备注请求</Button>
        : remark.view.editable ? <Button disabled={remarkBusy} onClick={() => void saveRemark({ text: remarkText, etag: remark.etag, key: crypto.randomUUID() })}>保存备注</Button> : null}
      <details><summary>备注历史</summary>{remarkHistory?.items.map((entry) => <article className={styles.event} key={entry.remark_version}>
        <strong>备注版本 {entry.remark_version}</strong> <time>{entry.occurred_at}</time><span> · 编辑账号编号 {entry.actor_ai_user_id}</span><p className={styles.text}>{entry.text}</p></article>)}
        {remarkHistory?.next_cursor ? <Button onClick={() => void loadRemarkHistory(remarkHistory.next_cursor!).catch((error: unknown) => { if (current()) failed('remark_history', error); })}>更多备注历史</Button> : null}</details>
    </div> : null}
    <div aria-label="办理活动"><h4>办理历史</h4>{events.map((event) => <article className={styles.event} key={event.event_id}>
      <strong>{eventLabels[event.operation]}</strong> <time>{event.occurred_at}</time><span> · 操作账号编号 {event.actor_ai_user_id}</span><p>{lifecycleStatusLabels[event.from_status]} → {lifecycleStatusLabels[event.to_status]}</p>
      <p className={styles.text}>{event.text}</p>{event.payload && Object.keys(event.payload).length ? <details><summary>变更前后记录</summary><pre className={styles.text}>{JSON.stringify(event.payload, null, 2)}</pre></details> : null}
    </article>)}</div>
    {cursor !== null ? <Button disabled={busy} onClick={() => {
      const requestEpoch = epoch.current;
      void readLifecycleEvents(id, cursor).then((page) => { if (!current(requestEpoch)) return; setEvents((old) => [...old, ...page.items.filter((item) => !old.some((event) => event.event_id === item.event_id))]); setCursor(page.next_after_version); })
        .catch((error: unknown) => { if (current(requestEpoch)) failed('lifecycle_events', error); });
    }}>加载更多办理记录</Button> : null}
  </section>;
}

function DirectoryTargets({ token, departmentId, onlyUsers = false, value, onChange, max, disabled, onAccessLost }: {
  token: DraftSessionToken; departmentId?: string; onlyUsers?: boolean; value: DispatchOption[]; onChange: (items: DispatchOption[]) => void; max: number; disabled: boolean; onAccessLost: () => void;
}) {
  const [page, setPage] = useState<DispatchOptionsResponse | null>(null);
  const [scope, setScope] = useState<OptionScope>({ kind: 'user', ...(departmentId ? { department_id: departmentId } : {}) });
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  const alive = useRef(true); const sequence = useRef(0);
  const load = async (next: OptionScope, cursor?: string) => {
    const request = ++sequence.current; setScope(next); setError('');
    if (!cursor) setPage(null);
    try {
      const response = await readOptions(next, cursor, query);
      if (!alive.current || !isCurrentDraftSession(token) || request !== sequence.current) return;
      setPage((old) => cursor && old ? { ...response, items: [...old.items, ...response.items] as DispatchOptionsResponse['items'] } : response);
    } catch (readError) {
      if (!alive.current || !isCurrentDraftSession(token) || request !== sequence.current) return;
      logDispatchFailure('handling_directory', readError); setPage(null); setError(lifecycleErrorText(readError)); if (accessLost(readError)) onAccessLost();
    }
  };
  useEffect(() => { alive.current = true; void load(scope); return () => { alive.current = false; sequence.current += 1; }; }, [departmentId, onlyUsers]); // eslint-disable-line react-hooks/exhaustive-deps
  return <div className={styles.targets}>
    <Space wrap><Button disabled={disabled} onClick={() => void load({ kind: 'user', ...(departmentId ? { department_id: departmentId } : {}) })}>选择人员</Button>
      {!onlyUsers ? <Button disabled={disabled} onClick={() => void load({ kind: 'department' })}>选择部门</Button> : null}
      <Input.Search aria-label="搜索承办目录" value={query} onChange={(event) => setQuery(event.target.value)} onSearch={() => void load(scope)} disabled={disabled} /></Space>
    {error ? <p role="alert">{error}</p> : null}
    <Select mode={max === 1 ? undefined : 'multiple'} placeholder="从服务端候选中明确选择" disabled={disabled}
      value={max === 1 ? value[0] ? optionKey(value[0]) : undefined : value.map(optionKey)}
      options={[...new Map([...value, ...(page?.items ?? [])].map((option) => [optionKey(option), option])).values()].map((option) => ({ value: optionKey(option), label: option.kind === 'user' ? `${option.display_name}（${option.department_display_name}）` : option.department_display_name }))}
      onChange={(keys: string | string[]) => {
        const selectedKeys = Array.isArray(keys) ? keys : [keys];
        const options = [...value, ...(page?.items ?? [])];
        onChange(selectedKeys.slice(0, max).map((key) => options.find((item) => optionKey(item) === key)!));
      }} />
    {page?.has_more ? <Button disabled={disabled} onClick={() => void load(scope, page.next_cursor!)}>加载更多目录</Button> : null}
    {value.length ? <p>已明确选择 {value.length} 个对象。</p> : null}
  </div>;
}
