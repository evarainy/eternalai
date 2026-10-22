import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Button, Checkbox, Input, Radio, Select } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError } from '../../api/mutator';
import { isCurrentDraftSession, useDraftSession } from '../../stores/sessionDraftStore';
import type { DraftSessionToken } from '../../stores/sessionDraftStore';
import { readOptions, postDispatch, validReceipt, optionKey, targetOf, trimDispatchText } from './dispatchApi';
import type { DispatchOption, DispatchOptionsResponse, DispatchRequest, OptionScope } from './dispatchApi';
import { accessLost, dispatchFailure, logDispatchFailure } from './dispatchErrors';
import { browserZone, localTime, timeChoices } from './dispatchTime';
import { DISPATCH_KINDS, EMPTY_DRAFT, REMINDER_CHOICES, loadDraft, saveDraft, clearDraft } from './dispatchDraft';
import type { DispatchDraft, DispatchKind } from './dispatchDraft';
import AttachmentsPanel from './AttachmentsPanel';
import type { AttachmentView } from './AttachmentsPanel';
import styles from './WorkDispatchPage.module.css';
import { Icon } from '../../shared/ui/Icon';

type Selected = { option: DispatchOption; confirmed: boolean };
type Submission = { body: DispatchRequest; key: string; uncertain: boolean };
const labelOf = (option: DispatchOption) => option.kind === 'user'
  ? `${option.display_name}（${option.department_display_name}，目录编号 ${option.directory_user_id}）` : option.department_display_name;

export default function WorkDispatchPage() {
  const token = useDraftSession();
  const queryClient = useQueryClient();
  return token === null ? null : <DispatchComposer key={`${token.generation}:${token.revision}`} token={token}
    onSubmit={async (request, key) => {
      const result = await postDispatch(request, key);
      const count = request.dispatch_mode === 'collaborative' ? 1 : request.targets.length;
      if (!validReceipt(result, count)) throw new Error('invalid_receipt');
      if (isCurrentDraftSession(token)) {
        clearDraft(token);
        try { await queryClient.invalidateQueries({ queryKey: ['work-objects', token.generation] }, { throwOnError: true }); }
        catch { return `${result.replayed ? '已确认原提交' : '已发布'}，共 ${result.created_count} 条工作事项。列表刷新失败，请到工作事项重新读取。`; }
      }
      return `${result.replayed ? '已确认原提交' : '已发布'}，共 ${result.created_count} 条工作事项。`;
    }} />;
}

export interface DispatchComposerProps {
  token: DraftSessionToken;
  initialRequest?: DispatchRequest;
  initialAttachments?: AttachmentView[];
  series?: boolean;
  children?: ReactNode | ((locked: boolean) => ReactNode);
  submitLabel?: string;
  onSubmit: (request: DispatchRequest, idempotencyKey: string) => Promise<string>;
  onAccessLost?: () => void;
}

/** Ordinary dispatch and recurring templates share the same target and mode fields. */
export function DispatchComposer({ token, initialRequest, initialAttachments = [], series = false,
  children, submitLabel = '发布任务', onSubmit, onAccessLost }: DispatchComposerProps) {
  const [zone] = useState(browserZone);
  const [draft, setDraft] = useState<DispatchDraft>(() => {
    if (initialRequest) return { ...EMPTY_DRAFT, kind: initialRequest.kind, title: initialRequest.title,
      requirement: initialRequest.requirement, receipt: initialRequest.receipt_requirement,
      dispatchMode: initialRequest.dispatch_mode, completionMode: initialRequest.completion_mode,
      dueAt: initialRequest.due_at ? localTime(initialRequest.due_at, zone) : '',
      dueInstant: initialRequest.due_at ?? undefined, reminders: initialRequest.reminder_choices ?? [] };
    const saved = series ? { ...EMPTY_DRAFT } : loadDraft(token);
    return saved.dueInstant ? { ...saved, dueAt: localTime(saved.dueInstant, zone) } : saved;
  });
  const [selected, setSelected] = useState<Selected[]>(() => initialRequest?.targets.map((target) => ({
    option: target.kind === 'user' ? { ...target, display_name: target.directory_user_id,
      department_display_name: target.department_id } : { ...target, department_display_name: target.department_id },
    confirmed: true,
  })) ?? []);
  const [leadKey, setLeadKey] = useState(initialRequest?.lead_target ? optionKey(initialRequest.lead_target) : '');
  const [attachments, setAttachments] = useState<AttachmentView[]>(initialAttachments);
  const [scope, setScope] = useState<OptionScope>({ kind: 'user' });
  const [query, setQuery] = useState('');
  const [page, setPage] = useState<DispatchOptionsResponse | null>(null);
  const [directoryState, setDirectoryState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [directoryError, setDirectoryError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeError, setNoticeError] = useState(false);
  const [phase, setPhase] = useState<'editing' | 'pending' | 'uncertain' | 'success' | 'stopped'>('editing');
  const [chosenInstant, setChosenInstant] = useState(draft.dueInstant ?? '');
  const [legacyConfirmed, setLegacyConfirmed] = useState(false);
  const [timeConfirmed, setTimeConfirmed] = useState(!draft.dueAt || !!draft.dueInstant);
  const frozen = useRef<Submission | null>(null);
  const sending = useRef(false);
  const alive = useRef(true);
  const requestSequence = useRef(0);
  const snapshotVersion = useRef<number | null>(null);
  const choices = useMemo(() => timeChoices(draft.dueAt, zone), [draft.dueAt, zone]);
  const instant = draft.dueAt ? chosenInstant || (choices.length === 1 ? choices[0]!.instant : null) : null;
  const timeValid = series || !draft.dueAt || (timeConfirmed && choices.some((choice) => choice.instant === instant));
  const legacy = !!(draft.assignee || draft.visibility || draft.targets.length);
  const locked = phase !== 'editing';
  const current = () => alive.current && isCurrentDraftSession(token);
  const invalidateSelection = () => setSelected((items) => items.map((item) => ({ ...item, confirmed: false })));
  const clearPrivate = () => {
    setSelected([]); setAttachments([]); setPage(null); setLeadKey(''); frozen.current = null;
    onAccessLost?.();
  };
  const load = async (nextScope: OptionScope, cursor?: string, search = query) => {
    const sequence = ++requestSequence.current;
    setScope(nextScope); setDirectoryState('loading'); setDirectoryError(null);
    if (!cursor) setPage(null);
    try {
      const result = await readOptions(nextScope, cursor, search);
      if (!current() || sequence !== requestSequence.current) return;
      const changed = snapshotVersion.current !== null && snapshotVersion.current !== result.snapshot_version;
      if (changed) {
        invalidateSelection(); setDirectoryError('目录已更新，请重新选择或确认所有对象。');
        if (cursor) { void load(nextScope, undefined, search); return; }
      }
      snapshotVersion.current = result.snapshot_version;
      setPage((previous) => cursor && previous && !changed
        ? { ...result, items: [...previous.items, ...result.items] as DispatchOptionsResponse['items'] } : result);
      setDirectoryState('ready');
    } catch (error) {
      if (!current() || sequence !== requestSequence.current) return;
      logDispatchFailure('directory_read', error);
      if (accessLost(error)) clearPrivate(); else invalidateSelection();
      setPage(null); setDirectoryState('failed'); setDirectoryError(dispatchFailure('GET', error).text);
    }
  };
  useEffect(() => {
    alive.current = true;
    void load({ kind: 'user' }, undefined, '');
    return () => { alive.current = false; requestSequence.current += 1; frozen.current = null; };
    // A fresh authenticated composer starts one directory chain.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const update = <K extends keyof DispatchDraft>(key: K, value: DispatchDraft[K]) => {
    setNotice(null); setDraft((previous) => ({ ...previous, [key]: value }));
  };
  const addTarget = (option: DispatchOption) => {
    setSelected((items) => {
      const index = items.findIndex((item) => optionKey(item.option) === optionKey(option));
      return index >= 0 ? items.map((item, i) => i === index ? { option, confirmed: true } : item)
        : [...items, { option, confirmed: true }];
    });
  };
  const send = async (submission: Submission) => {
    if (!current() || sending.current) return;
    sending.current = true; setPhase('pending'); setNotice(null);
    try {
      const result = await onSubmit(submission.body, submission.key);
      if (!current()) return;
      frozen.current = null; setPhase('success'); setNoticeError(false); setNotice(result);
      console.info('[work-dispatch]', { stage: 'request_confirmed', operation: series ? 'series_save' : 'dispatch' });
    } catch (error) {
      if (!current()) return;
      logDispatchFailure(series ? 'series_save' : 'dispatch', error);
      const failure = dispatchFailure('POST', error);
      setNoticeError(true);
      if (accessLost(error)) clearPrivate();
      const definite = error instanceof ApiError && error.status >= 400 && error.status < 500;
      if (failure.category === 'stop' || definite && submission.uncertain) {
        frozen.current = null; setPhase('stopped');
        setNotice(`${failure.text}${submission.uncertain ? ' 先前提交结果仍待确认，请先到工作事项核对。' : ''}`);
      } else if (definite) {
        frozen.current = null; setPhase('editing');
        setNotice(error instanceof ApiError && error.status === 412 ? '规则已更新，请重新读取后再编辑。' : failure.text);
      } else {
        submission.uncertain = true; frozen.current = submission; setPhase('uncertain');
        setNotice(`${failure.text} 请重试原请求或先核对结果。`);
      }
    } finally { sending.current = false; }
  };
  const publish = () => {
    const title = trimDispatchText(draft.title);
    const requirement = trimDispatchText(draft.requirement);
    const receipt = trimDispatchText(draft.receipt);
    const lead = selected.find((item) => optionKey(item.option) === leadKey);
    if (!title || [...title].length > 200 || [...requirement].length > 10000 || [...receipt].length > 2000
      || selected.length < 1 || selected.length > 100 || selected.some((item) => !item.confirmed)
      || (draft.dispatchMode === 'collaborative' && !lead) || (legacy && !legacyConfirmed) || !timeValid) {
      setNoticeError(true); setNotice('请核对标题、已确认目录对象、唯一主办和截止时间。'); return;
    }
    const body: DispatchRequest = { kind: draft.kind, title, requirement, receipt_requirement: receipt,
      due_at: series ? null : instant, reminder_choices: series || instant ? [...draft.reminders] : [],
      targets: selected.map((item) => targetOf(item.option)), dispatch_mode: draft.dispatchMode,
      completion_mode: draft.completionMode, lead_target: draft.dispatchMode === 'collaborative' && lead ? targetOf(lead.option) : null,
      attachment_ids: attachments.map((item) => item.attachment_id) };
    const submission = { body, key: crypto.randomUUID(), uncertain: false }; frozen.current = submission; void send(submission);
  };
  const store = () => {
    const choice = choices.find((value) => value.instant === instant);
    const ok = saveDraft({ ...draft, ...(choice ? { dueInstant: choice.instant, dueZone: zone, dueOffset: choice.offset } : {}) }, token);
    setNoticeError(!ok); setNotice(ok ? '草稿已暂存；刷新、关闭页面或退出登录会丢失。下次需重新选择对象和附件。' : '草稿未保存，请核对登录状态。');
  };
  return <div className={styles.page}>
    {!series ? <div className={styles.toolbar}><h1>任务交办</h1><Link to="/work-object-series">周期任务规则</Link></div> : null}
    <section className={styles.panel}>
      <div className={styles.draftNotice}>发布前请核对要求与目标。草稿仅在本次会话内暂存，刷新后需到工作事项核对先前发布结果。 <Link to="/work-objects">核对工作事项</Link></div>
      <div className={styles.form}>
        {typeof children === 'function' ? children(locked) : children}
        <h2 className={styles.sectionHead}>基本信息</h2>
        <div className={styles.gridKindTitle}>
          <div className={styles.field}><label htmlFor="dispatch-kind">类型</label><Select<DispatchKind> id="dispatch-kind" virtual={false}
            value={draft.kind} disabled={locked} options={DISPATCH_KINDS.map((value) => ({ label: value, value }))}
            onChange={(value) => update('kind', value)} /></div>
          <div className={styles.field}><label htmlFor="dispatch-title">标题</label><Input id="dispatch-title" maxLength={200}
            value={draft.title} disabled={locked} onChange={(event) => update('title', event.target.value)} /></div>
        </div>
        <div className={styles.gridRequirement}>
          <div className={styles.field}><label>办理方式</label><Radio.Group name="dispatch-mode" value={draft.dispatchMode} disabled={locked}
            onChange={(event) => update('dispatchMode', event.target.value)}>
            <Radio value="independent">各自独立办理</Radio><Radio value="collaborative">主协办协作</Radio></Radio.Group>
            <p className={styles.caption}>{draft.dispatchMode === 'independent' ? '每个目标分别生成一个工作事项，分别办结。' : '共同生成一个工作事项，所有协办对外提交后由唯一主办汇总。'}</p></div>
          <div className={styles.field}><label>完成模式</label><Radio.Group name="dispatch-completion-mode" value={draft.completionMode} disabled={locked}
            onChange={(event) => update('completionMode', event.target.value)}><Radio value="submit_completes">正式提交即完成</Radio>
            <Radio value="confirm_required">派发人确认后完成</Radio></Radio.Group>
            <p className={styles.caption}>部门目标须先由负责人审核；本人自办也可选择提交即完成。</p></div>
        </div>
        <h2 className={styles.sectionHead}>交办对象</h2>
        <p className={styles.caption}>候选由服务端按当前身份提供；普通人员仅本人、负责人可派部门及本部门人员。领导岗位名单未配置时不会开放领导权限。</p>
        <div className={styles.chipWell} role="group" aria-label="交办对象（目录选择）">
          {selected.length === 0 ? <span>尚未选择对象</span> : selected.map((item) => <span className={styles.chip} key={optionKey(item.option)}>
            {labelOf(item.option)}{item.confirmed ? '' : '（待重新确认）'}
            <Button type="text" disabled={locked} aria-label={`移除 ${labelOf(item.option)}`} onClick={() => setSelected((items) => items.filter((entry) => optionKey(entry.option) !== optionKey(item.option)))}><Icon name="close" size={14} /></Button>
          </span>)}
        </div>
        <div className={styles.directory}>
          <div className={styles.directoryActions}>
            <Button disabled={locked} onClick={() => void load({ kind: 'user' }, undefined, '')}>本人 / 可选人员</Button>
            <Button disabled={locked} onClick={() => void load({ kind: 'department' }, undefined, '')}>可选部门</Button>
            <Input.Search aria-label="搜索目录对象" placeholder="搜索当前目录" value={query} disabled={locked}
              onChange={(event) => setQuery(event.target.value)} onSearch={() => void load(scope)} />
            <Button disabled={locked} onClick={() => void load(scope)}>重新读取目录</Button>
          </div>
          {directoryState === 'loading' ? <p role="status">正在读取目录…</p> : null}
          {directoryError ? <p role="alert">{directoryError}</p> : null}
          {page && directoryState === 'ready' ? <>
            <p className={styles.caption}>当前能力：{page.can_self_dispatch ? '本人自办 ' : ''}{page.can_dispatch_departments ? '部门派发 ' : ''}{page.can_dispatch_others ? '人员派发' : ''}</p>
            {page.items.length === 0 ? <p>当前范围没有可选对象。</p> : null}
            {page.unselectable_count > 0 ? <p>有 {page.unselectable_count} 个对象当前不能唯一解析，请核对目录。</p> : null}
            <ul className={styles.optionList}>{page.items.map((option) => <li key={optionKey(option)}><span>{labelOf(option)}</span>
              {option.kind === 'department' ? <Button aria-label={`查看 ${option.department_display_name} 人员`} disabled={locked} onClick={() => void load({ kind: 'user', department_id: option.department_id }, undefined, '')}>查看人员</Button> : null}
              <Button aria-label={`选择 ${labelOf(option)}`} disabled={locked || selected.length >= 100 && !selected.some((item) => optionKey(item.option) === optionKey(option))}
                onClick={() => addTarget(option)}>{selected.some((item) => optionKey(item.option) === optionKey(option)) ? '重新确认' : '选择'}</Button>
            </li>)}</ul>
            {page.has_more ? <Button disabled={locked} onClick={() => void load(scope, page.next_cursor!)}>加载下一页</Button> : null}
          </> : null}
        </div>
        {draft.dispatchMode === 'collaborative' ? <div className={styles.field}><label htmlFor="dispatch-lead">唯一主办</label>
          <Select id="dispatch-lead" value={leadKey || undefined} placeholder="从已选对象选择一名主办" disabled={locked}
            options={selected.map((item) => ({ value: optionKey(item.option), label: labelOf(item.option) }))} onChange={setLeadKey} /></div> : null}
        {legacy ? <label><Checkbox checked={legacyConfirmed} disabled={locked} onChange={(event) => setLegacyConfirmed(event.target.checked)} />已核对旧草稿意图并重新选择目标：{draft.assignee} {draft.visibility} {draft.targets.join('、')}</label> : null}
        {!series ? <div className={styles.field}><label htmlFor="dispatch-due">截止时间</label>
          <input className={styles.dateInput} id="dispatch-due" type="datetime-local" value={draft.dueAt} disabled={locked}
            onChange={(event) => { setChosenInstant(''); setTimeConfirmed(true); setDraft((old) => ({ ...old, dueAt: event.target.value, dueInstant: undefined, reminders: event.target.value ? old.reminders : [] })); }} />
          <p className={styles.caption}>时区：{zone} {choices.map((choice) => choice.offset).join(' / ')}</p>
          {draft.dueAt && choices.length === 0 ? <p role="alert">该本地日期时间不存在，请重新选择。</p> : null}
          {choices.length > 1 ? <Select aria-label="选择截止时间偏移" virtual={false} value={chosenInstant || undefined} disabled={locked}
            options={choices.map((choice) => ({ label: choice.offset, value: choice.instant }))} onChange={setChosenInstant} /> : null}
          {!timeConfirmed ? <Button disabled={locked} onClick={() => setTimeConfirmed(true)}>确认旧截止时间与时区</Button> : null}
        </div> : null}
        <h2 className={styles.sectionHead}>办理要求与回执材料</h2>
        <div className={styles.gridRequirement}>
          <div className={styles.field}><label htmlFor="dispatch-requirement">办理要求与交付物</label><Input.TextArea id="dispatch-requirement"
            rows={4} maxLength={10000} value={draft.requirement} disabled={locked} onChange={(event) => update('requirement', event.target.value)} /></div>
          <div className={styles.field}><label htmlFor="dispatch-receipt">回执要求</label><Input.TextArea id="dispatch-receipt"
            rows={4} maxLength={2000} value={draft.receipt} disabled={locked} onChange={(event) => update('receipt', event.target.value)} /></div>
        </div>
        <div role="group" aria-label="附件"><AttachmentsPanel purpose="task_material" visibility="shared" value={attachments} onChange={setAttachments} readOnly={locked} onAccessLost={clearPrivate} /></div>
        <div className={styles.field}><label>站内到期提示</label><Checkbox.Group aria-label="站内到期提示" role="group" disabled={locked || !series && !draft.dueAt} options={[...REMINDER_CHOICES]}
          value={[...draft.reminders]} onChange={(values) => update('reminders', REMINDER_CHOICES.filter((choice) => values.includes(choice)))} />
          <p className={styles.caption}>提示显示在站内工作台，不代表已发送邮件、短信或 IM。</p></div>
      </div>
      <div className={styles.footer}>
        <span className={styles.caption}>已选择 {selected.length} 个目标 · {draft.dispatchMode === 'collaborative' ? '共同协作' : '分别办理'}</span>
        <div className={styles.footerActions}>
          {!series ? <Button disabled={locked} onClick={store}>暂存草稿</Button> : null}
          {phase === 'editing' ? <Button type="primary" disabled={directoryState !== 'ready' || selected.length === 0 || selected.some((item) => !item.confirmed) || legacy && !legacyConfirmed || !timeValid} onClick={publish}>{submitLabel}</Button> : null}
          {phase === 'pending' ? <Button loading>正在提交</Button> : null}
          {phase === 'uncertain' && frozen.current ? <Button onClick={() => void send(frozen.current!)}>重试原请求</Button> : null}
          {phase === 'success' && !series ? <><Link to="/work-objects">查看工作事项</Link><Button onClick={() => { setDraft({ ...EMPTY_DRAFT }); setSelected([]); setAttachments([]); setLeadKey(''); setPhase('editing'); setNotice(null); }}>继续创建</Button></> : null}
        </div>
      </div>
      {notice ? <p aria-label="交办状态" className={styles.notice} role={noticeError ? 'alert' : 'status'}>{notice}</p> : null}
    </section>
  </div>;
}
