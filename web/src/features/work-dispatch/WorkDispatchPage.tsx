import { useEffect, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { ApiError } from '../../api/mutator';
import { useReauthForm } from '../../app/reauthRestore';
import { isCurrentDraftSession, useDraftSession } from '../../stores/sessionDraftStore';
import type { DraftSessionToken } from '../../stores/sessionDraftStore';
import { readOptions, postDispatch, validReceipt, optionKey, targetOf, trimDispatchText } from './dispatchApi';
import type { DispatchOption, DispatchOptionsResponse, DispatchRequest, OptionScope } from './dispatchApi';
import { accessLost, dispatchFailure, logDispatchFailure } from './dispatchErrors';
import { browserZone, localTime, timeChoices } from './dispatchTime';
import { DISPATCH_KINDS, EMPTY_DRAFT, REMINDER_CHOICES, loadDraft, saveDraft, clearDraft, parseDraft } from './dispatchDraft';
import type { DispatchDraft } from './dispatchDraft';
import AttachmentsPanel from './AttachmentsPanel';
import type { AttachmentView } from './AttachmentsPanel';
import { PageFrame } from '../../shared/ui/PageFrame';
import { DispatchStatus } from './DispatchStatus';
import styles from './WorkDispatchPage.module.css';
import { Icon } from '../../shared/ui/Icon';

type Selected = { option: DispatchOption; confirmed: boolean };
type Submission = { body: DispatchRequest; key: string; uncertain: boolean };
const labelOf = (option: DispatchOption) => option.kind === 'user'
  ? `${option.display_name}（${option.department_display_name}，目录编号 ${option.directory_user_id}）` : option.department_display_name;
/** 已选对象在标签上只写名字；完整的目录信息在悬停提示里。 */
const shortLabelOf = (option: DispatchOption) => option.kind === 'user' ? option.display_name : option.department_display_name;

/** 单选标签组：方向键在组内切换并跟着移焦点，Tab 只停在当前选中的那一枚上。 */
function ChoiceChips<T extends string>({ disabled, label, labelledBy, onChange, options, value }: {
  disabled: boolean; label?: string; labelledBy?: string; onChange: (value: T) => void;
  options: readonly { value: T; text: string }[]; value: T;
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1
      : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0;
    if (step === 0 || disabled) return;
    event.preventDefault();
    const index = (options.findIndex((option) => option.value === value) + step + options.length) % options.length;
    onChange(options[index]!.value);
    (event.currentTarget.children[index] as HTMLElement | undefined)?.focus();
  };
  return <div aria-label={label} aria-labelledby={labelledBy} className={styles.chips} onKeyDown={onKeyDown} role="radiogroup">
    {options.map((option) => <button aria-checked={option.value === value} className={styles.chip} disabled={disabled}
      key={option.value} onClick={() => onChange(option.value)} role="radio" tabIndex={option.value === value ? 0 : -1}
      type="button">{option.text}</button>)}
  </div>;
}

const KIND_OPTIONS = DISPATCH_KINDS.map((value) => ({ value, text: value }));
const DISPATCH_MODE_LABELS = { independent: '各自独立办理', collaborative: '主协办协作' } as const;
const COMPLETION_MODE_LABELS = { submit_completes: '提交即完成', confirm_required: '派发人确认后完成' } as const;
const DISPATCH_MODE_OPTIONS = (['independent', 'collaborative'] as const).map((value) => ({ value, text: DISPATCH_MODE_LABELS[value] }));
const COMPLETION_MODE_OPTIONS = (['submit_completes', 'confirm_required'] as const).map((value) => ({ value, text: COMPLETION_MODE_LABELS[value] }));

const STATUS_HREF = '/work-dispatch';
const NEW_HREF = '/work-dispatch?tab=new';

/**
 * 任务交办：默认看「交办情况」，「新建交办」是第二个标签（地址带 `?tab=new`）。
 * 新建表单打开过一次后一直留着，切去看交办情况再回来，没存的内容还在。
 */
export default function WorkDispatchPage() {
  const token = useDraftSession();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const tab = searchParams.get('tab') === 'new' ? 'new' : 'status';
  const [composerOpened, setComposerOpened] = useState(tab === 'new');
  if (tab === 'new' && !composerOpened) setComposerOpened(true);
  if (token === null) return null;
  return <PageFrame
    actions={<>
      <Link className="wb-btn wb-btn-quiet" to="/work-object-series">周期任务规则</Link>
      {tab === 'status' ? <Link className="wb-btn wb-btn-primary" to={NEW_HREF}><Icon name="plus" size={16} />新建交办</Link> : null}
    </>}
    lead={<nav aria-label="任务交办" className="wb-seg">
      <Link aria-current={tab === 'status' ? 'page' : undefined} to={STATUS_HREF}>交办情况</Link>
      <Link aria-current={tab === 'new' ? 'page' : undefined} to={NEW_HREF}>新建交办</Link>
    </nav>}
    title="任务交办"
  >
    {tab === 'status' ? <DispatchStatus newHref={NEW_HREF} /> : null}
    {composerOpened ? <div hidden={tab !== 'new'}><DispatchComposer key={`${token.generation}:${token.revision}`} token={token}
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
    }} /></div> : null}
  </PageFrame>;
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
  children, submitLabel = '发布', onSubmit, onAccessLost }: DispatchComposerProps) {
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
  const [initialDraft] = useState(draft);
  // 目录选择面板：新建时默认摊开；「更多」里是办理方式和完成条件。
  const [pickerOpen, setPickerOpen] = useState(initialRequest === undefined);
  const [moreOpen, setMoreOpen] = useState(false);
  // Only the page-level new dispatch keeps unsaved text across a same-account re-login.
  useReauthForm('work-dispatch', {
    enabled: !series && initialRequest === undefined,
    read: () => draft,
    parse: parseDraft,
    isPristine: () => JSON.stringify(draft) === JSON.stringify(initialDraft),
    apply: (restored) => {
      const next = restored.dueInstant ? { ...restored, dueAt: localTime(restored.dueInstant, zone) } : restored;
      setDraft(next);
      setChosenInstant(next.dueInstant ?? '');
      setTimeConfirmed(!next.dueAt || !!next.dueInstant);
    },
  });
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
  const isSelected = (option: DispatchOption) => selected.some((item) => optionKey(item.option) === optionKey(option));
  const remindersOff = locked || !series && !draft.dueAt;
  const moreShown = moreOpen || draft.dispatchMode === 'collaborative';
  const canPublish = directoryState === 'ready' && selected.length > 0 && selected.every((item) => item.confirmed)
    && !(legacy && !legacyConfirmed) && timeValid;
  const reset = () => {
    setDraft({ ...EMPTY_DRAFT }); setSelected([]); setAttachments([]); setLeadKey(''); setPhase('editing'); setNotice(null);
  };

  // 版式照 v7 样板：左边是一张可以直接改的表（大标题 + 一行一项），右边是「发布后」会发生什么和发布按钮。
  return <div className={styles.composer}>
    <div className={styles.form}>
      {typeof children === 'function' ? children(locked) : children}
      <input aria-label="标题" className={styles.titleInput} disabled={locked} id="dispatch-title" maxLength={200}
        onChange={(event) => update('title', event.target.value)} placeholder="要交办什么事" value={draft.title} />

      <div className={styles.f}>
        <span className={styles.fl} id="dispatch-kind-label">类型</span>
        <ChoiceChips disabled={locked} labelledBy="dispatch-kind-label" onChange={(value) => update('kind', value)}
          options={KIND_OPTIONS} value={draft.kind} />
      </div>

      <div className={styles.f}>
        <span className={styles.fl}>交办对象</span>
        <div>
          <div aria-label="交办对象（目录选择）" className={styles.chips} role="group">
            {selected.map((item) => <span className={styles.chip} data-on="true" data-stale={item.confirmed ? 'false' : 'true'}
              key={optionKey(item.option)} title={labelOf(item.option)}>
              {shortLabelOf(item.option)}{item.confirmed ? '' : '（待重新确认）'}
              <button aria-label={`移除 ${labelOf(item.option)}`} disabled={locked} type="button"
                onClick={() => {
                  if (optionKey(item.option) === leadKey) setLeadKey('');
                  setSelected((items) => items.filter((entry) => optionKey(entry.option) !== optionKey(item.option)));
                }}>
                <Icon name="close" size={13} strokeWidth={2.2} />
              </button>
            </span>)}
            <button aria-expanded={pickerOpen} className={`${styles.chip} ${styles.chipAdd}`} disabled={locked}
              onClick={() => setPickerOpen((open) => !open)} type="button">
              <Icon name="plus" size={15} />{selected.length === 0 ? '选择对象' : '添加'}
            </button>
          </div>
          {pickerOpen ? <div className={styles.picker}>
            <div className={styles.pickerBar}>
              <div aria-label="目录范围" className="wb-seg" role="group">
                <button aria-pressed={scope.kind === 'user'} disabled={locked} onClick={() => void load({ kind: 'user' }, undefined, '')} type="button">人员</button>
                <button aria-pressed={scope.kind === 'department'} disabled={locked} onClick={() => void load({ kind: 'department' }, undefined, '')} type="button">部门</button>
              </div>
              <label className={`wb-field ${styles.pickerSearch}`} data-focus-ring="host">
                <Icon name="search" size={16} />
                <input aria-label="搜索目录对象" disabled={locked} onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void load(scope); } }}
                  placeholder="搜索，回车查找" value={query} />
              </label>
              <button className="wb-btn wb-btn-quiet wb-btn-sm" disabled={locked} onClick={() => void load(scope)} type="button">
                <Icon name="retry" size={15} />重新读取
              </button>
            </div>
            {directoryState === 'loading' ? <p className={styles.hint} role="status">正在读取目录…</p> : null}
            {directoryError ? <p className={styles.alert} role="alert">{directoryError}</p> : null}
            {page && directoryState === 'ready' ? <>
              {page.items.length === 0 ? <p className={styles.hint}>当前范围没有可选对象。</p> : null}
              {page.unselectable_count > 0 ? <p className={styles.hint}>有 {page.unselectable_count} 个对象当前不能唯一解析，请核对目录。</p> : null}
              <ul className={styles.options}>{page.items.map((option) => <li key={optionKey(option)}>
                <span>{labelOf(option)}</span>
                {option.kind === 'department' ? <button aria-label={`查看 ${option.department_display_name} 人员`} className="wb-btn wb-btn-quiet wb-btn-sm" disabled={locked}
                  onClick={() => void load({ kind: 'user', department_id: option.department_id }, undefined, '')} type="button">查看人员</button> : null}
                <button aria-label={`选择 ${labelOf(option)}`} className="wb-btn wb-btn-sm" data-on={isSelected(option) ? 'true' : 'false'}
                  disabled={locked || selected.length >= 100 && !isSelected(option)} onClick={() => addTarget(option)} type="button">
                  {isSelected(option) ? '重新确认' : '选择'}
                </button>
              </li>)}</ul>
              {page.has_more ? <button className="wb-btn wb-btn-quiet wb-btn-sm" disabled={locked} onClick={() => void load(scope, page.next_cursor!)} type="button">加载下一页</button> : null}
            </> : null}
          </div> : null}
        </div>
      </div>

      {legacy ? <div className={styles.f}>
        <span className={styles.fl}>旧草稿</span>
        <label className={styles.check}>
          <input checked={legacyConfirmed} disabled={locked} onChange={(event) => setLegacyConfirmed(event.target.checked)} type="checkbox" />
          已核对旧草稿意图并重新选择目标：{draft.assignee} {draft.visibility} {draft.targets.join('、')}
        </label>
      </div> : null}

      {!series ? <div className={styles.f} data-focus-ring="host">
        <label className={styles.fl} htmlFor="dispatch-due">截止时间</label>
        <div>
          <input className={`${styles.box} ${styles.boxShort}`} disabled={locked} id="dispatch-due" type="datetime-local" value={draft.dueAt}
            onChange={(event) => { setChosenInstant(''); setTimeConfirmed(true); setDraft((old) => ({ ...old, dueAt: event.target.value, dueInstant: undefined, reminders: event.target.value ? old.reminders : [] })); }} />
          {draft.dueAt ? <p className={styles.hint}>时区：{zone} {choices.map((choice) => choice.offset).join(' / ')}</p> : null}
          {draft.dueAt && choices.length === 0 ? <p className={styles.alert} role="alert">该本地日期时间不存在，请重新选择。</p> : null}
          {choices.length > 1 ? <select aria-label="选择截止时间偏移" className={`${styles.box} ${styles.boxShort}`} disabled={locked}
            onChange={(event) => setChosenInstant(event.target.value)} value={chosenInstant}>
            <option disabled value="">这个时刻出现两次，请选一个</option>
            {choices.map((choice) => <option key={choice.instant} value={choice.instant}>{choice.offset}</option>)}
          </select> : null}
          {!timeConfirmed ? <button className="wb-btn wb-btn-sm" disabled={locked} onClick={() => setTimeConfirmed(true)} type="button">确认旧截止时间与时区</button> : null}
        </div>
      </div> : null}

      <div className={styles.f} data-focus-ring="host">
        <label className={styles.fl} htmlFor="dispatch-requirement">办理要求</label>
        <textarea className={styles.box} disabled={locked} id="dispatch-requirement" maxLength={10000}
          onChange={(event) => update('requirement', event.target.value)} placeholder="要对方做什么、交什么" rows={3} value={draft.requirement} />
      </div>

      <div className={styles.f} data-focus-ring="host">
        <label className={styles.fl} htmlFor="dispatch-receipt">回执要求</label>
        <textarea className={styles.box} disabled={locked} id="dispatch-receipt" maxLength={2000}
          onChange={(event) => update('receipt', event.target.value)} placeholder="办完后要对方回什么，可以不填" rows={2} value={draft.receipt} />
      </div>

      <div className={styles.f}>
        <span className={styles.fl}>附件</span>
        <div aria-label="附件" role="group">
          <AttachmentsPanel onAccessLost={clearPrivate} onChange={setAttachments} purpose="task_material" readOnly={locked} value={attachments} visibility="shared" />
        </div>
      </div>

      <div className={styles.f}>
        <span className={styles.fl}>提醒</span>
        <div>
          <div aria-label="站内到期提示" className={styles.chips} role="group">
            {REMINDER_CHOICES.map((choice) => <button aria-pressed={draft.reminders.includes(choice)} className={styles.chip} disabled={remindersOff}
              key={choice} type="button"
              onClick={() => update('reminders', REMINDER_CHOICES.filter((value) => (value === choice) !== draft.reminders.includes(value)))}>{choice}</button>)}
          </div>
          <p className={styles.hint}>{!series && !draft.dueAt ? '设了截止时间才能提醒。' : '提醒只显示在工作台里，不发短信、邮件或 IM 消息。'}</p>
        </div>
      </div>

      <div className={styles.f}>
        <span className={styles.fl}>更多</span>
        {moreShown ? <div className={styles.more}>
          <div>
            <ChoiceChips disabled={locked} label="办理方式" onChange={(value) => update('dispatchMode', value)}
              options={DISPATCH_MODE_OPTIONS} value={draft.dispatchMode} />
            <p className={styles.hint}>{draft.dispatchMode === 'independent' ? '每个对象各生成一条工作事项，分别办结。' : '共同生成一条工作事项，协办提交后由唯一主办汇总。'}</p>
          </div>
          {draft.dispatchMode === 'collaborative' ? <div>
            <select aria-label="唯一主办" className={`${styles.box} ${styles.boxShort}`} disabled={locked} id="dispatch-lead"
              onChange={(event) => setLeadKey(event.target.value)} value={leadKey}>
              <option disabled value="">从已选对象里选一名主办</option>
              {selected.map((item) => <option key={optionKey(item.option)} value={optionKey(item.option)}>{labelOf(item.option)}</option>)}
            </select>
          </div> : null}
          <div>
            <ChoiceChips disabled={locked} label="完成模式" onChange={(value) => update('completionMode', value)}
              options={COMPLETION_MODE_OPTIONS} value={draft.completionMode} />
            <p className={styles.hint}>交给部门的事先由负责人审核。</p>
          </div>
        </div> : <p className={styles.moreShut}>
          {DISPATCH_MODE_LABELS[draft.dispatchMode]} · {COMPLETION_MODE_LABELS[draft.completionMode]}
          <button onClick={() => setMoreOpen(true)} type="button">展开</button>
        </p>}
      </div>
    </div>

    <aside className={styles.aside}>
      {phase === 'success' ? <span className="wb-stamp">{series ? '已保存' : '已发布'}</span> : null}
      <h2>{series ? '保存后' : '发布后'}</h2>
      <ul>
        <li><Icon name="users" size={16} />{selected.length === 0 ? '还没有选交办对象'
          : draft.dispatchMode === 'collaborative' ? `${selected.length} 个对象共同办理 1 条工作事项` : `${selected.length} 个对象各收到 1 条工作事项`}</li>
        {series ? null : <li><Icon name="clock" size={16} />{draft.dueAt ? `截止 ${draft.dueAt.replace('T', ' ')}` : '没有设截止时间'}</li>}
        <li><Icon name="bell" size={16} />{draft.reminders.length > 0 && (series || draft.dueAt) ? `${draft.reminders.join('、')}提醒` : '不提醒'}</li>
        {series ? null : <li><Icon name="todo" size={16} />进度在「交办情况」里看</li>}
      </ul>
      {phase === 'editing' ? <button className="wb-btn wb-btn-primary" disabled={!canPublish} onClick={publish} type="button">{submitLabel}</button> : null}
      {phase === 'pending' ? <button className="wb-btn wb-btn-primary" disabled type="button">正在提交</button> : null}
      {phase === 'uncertain' && frozen.current ? <button className="wb-btn" onClick={() => void send(frozen.current!)} type="button">重试原请求</button> : null}
      {phase === 'success' && !series ? <>
        <Link className="wb-btn" to="/work-objects">查看工作事项</Link>
        <button className="wb-btn wb-btn-quiet" onClick={reset} type="button">继续创建</button>
      </> : null}
      {!series && phase === 'editing' ? <button className="wb-btn wb-btn-quiet" onClick={store} type="button">暂存草稿</button> : null}
      {notice ? <p aria-label="交办状态" className={styles.notice} data-tone={noticeError ? 'error' : 'ok'} role={noticeError ? 'alert' : 'status'}>{notice}</p> : null}
      {/* 刷新后本页不记得上一次提交有没有成功，所以核对的去处一直写在这里。 */}
      <p className={styles.asideNote}>发布前请核对要求与目标。草稿仅在本次会话内暂存，刷新后需到工作事项核对先前发布结果。 <Link to="/work-objects">核对工作事项</Link></p>
    </aside>
  </div>;
}
