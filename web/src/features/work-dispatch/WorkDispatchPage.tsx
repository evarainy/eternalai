import { isCurrentDraftSession, useDraftSession } from '../../stores/sessionDraftStore';
import type { DraftSessionToken } from '../../stores/sessionDraftStore';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError } from '../../api/mutator';
import { readOptions, postDispatch, validReceipt, optionKey, targetOf, trimDispatchText } from './dispatchApi';
import type { DispatchOption, OptionScope } from './dispatchApi';
import type { DispatchOptionsResponse, DispatchWorkObjectsRequest } from '../../generated/work-objects/work-objects.schemas';
import { dispatchFailure } from './dispatchErrors';
import { browserZone, localTime, timeChoices } from './dispatchTime';
import { Button, Input, Select } from 'antd';
import { Icon } from '../../shared/ui/Icon';
import {
  DISPATCH_KINDS,
  REMINDER_CHOICES,
  loadDraft,
  saveDraft,
} from './dispatchDraft';
import type { DispatchDraft, DispatchKind, ReminderChoice } from './dispatchDraft';
import styles from './WorkDispatchPage.module.css';

const SAVE_NOTICE = '草稿已暂存；刷新、关闭页面或退出登录后会丢失。交办对象下次需重新选择。';
const SAVE_FAILED_NOTICE = '草稿没存上，请确认登录状态后重试。';
const labelOf = (option: DispatchOption) => option.kind === 'user'
  ? `${option.display_name}（${option.department_display_name}，目录编号 ${option.directory_user_id}）` : option.department_display_name;
type Selected = { option: DispatchOption; version: number; confirmed: boolean };
type Frozen = { body: DispatchWorkObjectsRequest; key: string; uncertain: boolean };

export default function WorkDispatchPage() {
  const token = useDraftSession();
  return token === null ? null : <DispatchForm key={`${token.generation}:${token.revision}`} token={token} />;
}

function DispatchForm({ token }: { token: DraftSessionToken }) {
  const queryClient = useQueryClient();
  const [zone] = useState(browserZone);
  const [draft, setDraft] = useState<DispatchDraft>(() => {
    const saved = loadDraft(token);
    return saved.dueInstant ? { ...saved, dueAt: localTime(saved.dueInstant, zone) } : saved;
  });
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeError, setNoticeError] = useState(false);
  const [scope, setScope] = useState<OptionScope>({ kind: 'department' });
  const [page, setPage] = useState<DispatchOptionsResponse | null>(null);
  const [directoryState, setDirectoryState] = useState<'loading' | 'ready' | 'failed'>('loading');
  const [directoryError, setDirectoryError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Selected[]>([]);
  const [legacyConfirmed, setLegacyConfirmed] = useState(false);
  const [timeConfirmed, setTimeConfirmed] = useState(() => !draft.dueAt || !!draft.dueInstant);
  const [chosenInstant, setChosenInstant] = useState(draft.dueInstant ?? '');
  const [phase, setPhase] = useState<'editing' | 'pending' | 'uncertain' | 'success' | 'stopped'>('editing');
  const frozen = useRef<Frozen | null>(null);
  const sending = useRef(false);
  const alive = useRef(true);
  const requestSequence = useRef(0);
  const version = useRef<number | null>(null);
  const currentScope = useRef(scope);
  const choices = useMemo(() => timeChoices(draft.dueAt, zone), [draft.dueAt, zone]);
  const instant = draft.dueAt === '' ? null : chosenInstant || (choices.length === 1 ? choices[0]!.instant : null);
  const timeValid = draft.dueAt === '' || (timeConfirmed && choices.some((choice) => choice.instant === instant));
  const legacy = !!(draft.assignee || draft.visibility || draft.targets.length);
  const locked = phase !== 'editing';
  const current = () => alive.current && isCurrentDraftSession(token);
  const invalidateSelection = () => setSelected((items) => items.map((item) => ({ ...item, confirmed: false })));

  const load = async (nextScope: OptionScope, cursor?: string, recovering = false) => {
    const sequence = ++requestSequence.current;
    currentScope.current = nextScope;
    setScope(nextScope);
    setDirectoryState('loading');
    setDirectoryError(null);
    if (!cursor) setPage(null);
    try {
      const result = await readOptions(nextScope, cursor);
      if (!current() || sequence !== requestSequence.current) return;
      const changed = version.current !== null && version.current !== result.snapshot_version;
      if (changed) {
        invalidateSelection();
        setDirectoryError('目录已更新，请重新确认所有交办对象。');
      }
      version.current = result.snapshot_version;
      if (changed && cursor) { void load(nextScope); return; }
      setPage((previous) => cursor && previous && !changed
        ? { ...result, items: [...previous.items, ...result.items] as DispatchOptionsResponse['items'] } : result);
      setDirectoryState('ready');
    } catch (error) {
      if (!current() || sequence !== requestSequence.current) return;
      const failure = dispatchFailure('GET', error);
      invalidateSelection();
      setPage(null);
      setDirectoryState('failed');
      setDirectoryError(failure.text);
      if (failure.category === 'snapshot' && !recovering) void load(nextScope, undefined, true);
    }
  };

  useEffect(() => {
    alive.current = true;
    void load({ kind: 'department' });
    return () => { alive.current = false; requestSequence.current += 1; frozen.current = null; };
    // One request chain per mounted authenticated form. No automatic retries.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const update = <Key extends keyof DispatchDraft>(key: Key, value: DispatchDraft[Key]) => {
    if (locked) return;
    setNotice(null);
    setDraft((previous) => ({ ...previous, [key]: value }));
  };
  const changeTime = (value: string) => {
    if (locked) return;
    setChosenInstant('');
    setTimeConfirmed(true);
    setDraft((previous) => ({ ...previous, dueAt: value, dueInstant: undefined,
      dueZone: undefined, dueOffset: undefined, reminders: value ? previous.reminders : [] }));
  };
  const addTarget = (option: DispatchOption) => {
    if (locked || directoryState !== 'ready' || !page) return;
    setSelected((items) => {
      const found = items.findIndex((item) => optionKey(item.option) === optionKey(option));
      const confirmed = { option, version: page.snapshot_version, confirmed: true };
      if (found >= 0) return items.map((item, i) => i === found ? confirmed : item);
      return items.length >= 100 ? items : [...items, confirmed];
    });
  };
  const toggleReminder = (choice: ReminderChoice) => {
    if (!draft.dueAt) return;
    update('reminders', REMINDER_CHOICES.filter((item) =>
      item === choice ? !draft.reminders.includes(item) : draft.reminders.includes(item)));
  };
  const send = async (submission: Frozen) => {
    if (!current() || sending.current) return;
    sending.current = true;
    setPhase('pending');
    setNotice(null);
    try {
      const result = await postDispatch(submission.body, submission.key);
      if (!current()) return;
      if (!validReceipt(result, submission.body.targets.length)) throw new Error('invalid_receipt');
      const success = `${result.replayed ? '已确认原提交' : '已发布'}，共${result.created_count}条。`;
      setPhase('success');
      setNoticeError(false);
      setNotice(success);
      try {
        await queryClient.invalidateQueries({ queryKey: ['work-objects', token.generation] }, { throwOnError: true });
      } catch {
        if (current()) setNotice(`${success}列表刷新失败，请到工作事项重新读取。`);
      }
    } catch (error) {
      if (!current()) return;
      const failure = dispatchFailure('POST', error);
      setNoticeError(true);
      if (failure.category === 'directory') {
        setDirectoryState('failed'); setPage(null); invalidateSelection(); setDirectoryError(failure.text);
      }
      if (failure.category === 'reject' && !submission.uncertain) {
        frozen.current = null;
        setPhase('editing');
        if (!(error instanceof ApiError && error.status === 422 && error.code === 'dispatch_request_invalid')) {
          setDirectoryState('failed'); setPage(null); setDirectoryError('请重新读取目录并核对对象。');
        }
        invalidateSelection();
        setNotice(failure.text);
      } else {
        submission.uncertain = true;
        setPhase(failure.category === 'stop' ? 'stopped' : 'uncertain');
        setNotice(`${failure.text} 先前提交结果待确认，请先核对，不能另建同一请求。`);
      }
    } finally { sending.current = false; }
  };
  const publish = () => {
    if (locked || sending.current || !current()) return;
    const title = trimDispatchText(draft.title);
    const requirement = trimDispatchText(draft.requirement);
    const receipt = trimDispatchText(draft.receipt);
    if (!title || [...title].length > 200 || [...requirement].length > 10000 || [...receipt].length > 2000
      || directoryState !== 'ready' || selected.length < 1 || selected.length > 100
      || selected.some((item) => !item.confirmed) || (legacy && !legacyConfirmed) || !timeValid) {
      setNoticeError(true); setNotice('请核对标题与字段长度、目录对象、旧内容及截止时间后再发布。'); return;
    }
    const body: DispatchWorkObjectsRequest = { kind: draft.kind, title, requirement, receipt_requirement: receipt,
      due_at: instant, reminder_choices: instant ? REMINDER_CHOICES.filter((choice) => draft.reminders.includes(choice)) : [],
      targets: selected.map((item) => targetOf(item.option)) };
    const submission = { body, key: crypto.randomUUID(), uncertain: false };
    frozen.current = submission;
    void send(submission);
  };
  const store = () => {
    if (!current() || locked) return;
    const choice = choices.find((value) => value.instant === instant);
    const saved = { ...draft, ...(timeValid && choice ? { dueInstant: choice.instant, dueZone: zone, dueOffset: choice.offset } : {}) };
    const ok = saveDraft(saved, token);
    setNoticeError(!ok);
    setNotice(ok ? SAVE_NOTICE : SAVE_FAILED_NOTICE);
  };
  const targetCount = selected.length;

  return (
    <div className={styles.page}>
      <h1 className={styles.pageTitle}>任务交办</h1>


      <section className={styles.brief}>
        <Input.TextArea
          aria-label="用一句话说明要交办的事"
          autoSize={{ maxRows: 6, minRows: 3 }}
          className={styles.briefInput}
          disabled={locked}
          id="dispatch-brief"
          onChange={(event) => update('brief', event.target.value)}
          placeholder="例：让各科室 9 月 5 日前报送第三季度政务信息"
          value={draft.brief}
          variant="borderless"
        />
        <div className={styles.briefBar}>
          <span className={styles.caption}>
            生成草稿还没有接进来；可直接在下方逐项填写。
          </span>
          <Button className={styles.briefButton} disabled type="primary">
            <Icon name="spark" size={17} strokeWidth={1.9} />
            生成草稿
          </Button>
        </div>
      </section>

      <section className={styles.panel}>
        <div className={styles.draftNotice}>
          <span className={styles.draftMark}>
            <Icon name="alert" size={17} strokeWidth={1.9} />
          </span>
          <b className={styles.draftTitle}>交办内容</b>
          <span className={styles.draftHint}>
            刷新前如已点过发布：结果待确认，请先到工作事项核对。草稿仅在本次登录期间暂存，刷新或关闭页面会丢失。 <Link to="/work-objects">核对工作事项</Link>
          </span>
        </div>

        <div className={styles.form}>
          <h2 className={styles.sectionHead}>
            <span>基本信息</span>
            <i aria-hidden="true" />
          </h2>
          <div className={styles.gridKindTitle}>
            <div className={styles.field}>
              <label htmlFor="dispatch-kind">类型</label>
              <Select<DispatchKind>
                className={styles.select}
                disabled={locked}
                id="dispatch-kind"
                onChange={(value) => update('kind', value)}
                options={DISPATCH_KINDS.map((kind) => ({
                  label: kind,
                  value: kind,
                }))}
                value={draft.kind}
                virtual={false}
              />
            </div>
            <div className={styles.field}>
              <label htmlFor="dispatch-title">标题</label>
              <Input
                disabled={locked}
                id="dispatch-title"
                onChange={(event) => update('title', event.target.value)}
                value={draft.title}
              />
            </div>
          </div>

          <h2 className={styles.sectionHead}>
            <span>交办范围与时限</span>
            <i aria-hidden="true" />
          </h2>
          <div className={styles.gridScope}>
            <div className={styles.field}>
              <label htmlFor="dispatch-assignee">责任人 / 责任部门</label>
              <Input.TextArea
                autoSize={{ minRows: 1, maxRows: 4 }}
                id="dispatch-assignee"
                readOnly
                value={selected.map((item) => `${labelOf(item.option)}${item.confirmed ? '' : '（待核对）'}`).join('、')}
              />
            </div>
            <div className={styles.field}>
              <label htmlFor="dispatch-due">截止时间</label>

              <input
                className={styles.dateInput}
                id="dispatch-due"
                onChange={(event) => changeTime(event.target.value)}
                type="datetime-local"
                value={draft.dueAt}
                disabled={locked}
              />
              <span className={styles.caption}>{zone} {choices.map((choice) => choice.offset).join(' / ')}</span>
              {draft.dueAt && choices.length === 0 ? <span role="alert">该日期时间不存在或无法确认，请重新选择。</span> : null}
              {choices.length > 1 ? <select aria-label="选择截止时间偏移" value={chosenInstant} disabled={locked}
                onChange={(event) => setChosenInstant(event.target.value)}>
                <option value="">此时间出现两次，请选择偏移</option>
                {choices.map((choice) => <option key={choice.instant} value={choice.instant}>{choice.offset}</option>)}
              </select> : null}
              {!timeConfirmed ? <Button disabled={locked} onClick={() => setTimeConfirmed(true)}>确认旧截止时间与时区</Button> : null}
            </div>
            <div className={styles.field}>
              <label htmlFor="dispatch-visibility">可见范围</label>
              <Input
                id="dispatch-visibility"
                readOnly
                value="由系统按部门和发起人确定"
              />
            </div>
          </div>

          <div className={styles.field}>
            <span className={styles.fieldLabel} id="dispatch-target-label">交办对象（目录选择）</span>
            <div aria-labelledby="dispatch-target-label" role="group" className={styles.chipWell}>
              {selected.map((item) => <span className={styles.chip} key={optionKey(item.option)}>
                <span>{labelOf(item.option)}{item.confirmed ? '' : '（待核对）'}</span>
                <button type="button" className={styles.chipRemove} disabled={locked}
                  aria-label={`移除交办对象 ${labelOf(item.option)}`}
                  onClick={() => setSelected((items) => items.filter((entry) => optionKey(entry.option) !== optionKey(item.option)))}>
                  <Icon name="close" size={14} strokeWidth={2.3} />
                </button>
              </span>)}
            </div>
            <div className={styles.directory}>
              <div className={styles.directoryActions}>
                <Button disabled={phase === 'pending'} onClick={() => void load({ kind: 'department' })}>返回部门首页</Button>
                <Button disabled={phase === 'pending'} onClick={() => void load(currentScope.current)}>重新读取目录</Button>
              </div>
              {directoryState === 'loading' ? <p>正在读取目录…</p> : null}
              {directoryError ? <p role="alert">{directoryError}</p> : null}
              {directoryState === 'ready' && page?.items.length === 0 ? <p>当前没有可选{scope.kind === 'department' ? '部门' : '人员'}。</p> : null}
              {page && directoryState === 'ready' ? <>
                {page.unselectable_count > 0 ? <p>有人员暂不可选，请联系管理员核对目录（{page.unselectable_count} 人）。</p> : null}
                <ul className={styles.optionList}>
                  {page.items.map((option) => <li key={optionKey(option)}>
                    <span>{labelOf(option)}</span>
                    {option.kind === 'department' ? <Button aria-label={`查看 ${option.department_display_name} 人员`} disabled={phase === 'pending'} onClick={() => void load({ kind: 'user', department_id: option.department_id })}>查看人员</Button> : null}
                    <Button aria-label={`选择 ${labelOf(option)}`} disabled={locked || (selected.length >= 100 && !selected.some((item) => optionKey(item.option) === optionKey(option)))}
                      onClick={() => addTarget(option)}>选择</Button>
                  </li>)}
                </ul>
                {page.has_more ? <Button disabled={phase === 'pending'} onClick={() => void load(scope, page.next_cursor!)}>下一页</Button> : null}
              </> : null}
            </div>
            <p className={styles.caption}>{targetCount === 0 ? '还没有交办对象。' : `已选择交办对象 ${targetCount} 个，同一对象只保留一次；最多 100 个。`}</p>
            {legacy ? <div className={styles.legacy}>
              <p>旧内容待重新确认：责任人 {draft.assignee}；可见范围 {draft.visibility}；对象 {draft.targets.join('、')}。</p>
              <label><input type="checkbox" checked={legacyConfirmed} disabled={locked} onChange={(event) => setLegacyConfirmed(event.target.checked)} />我已核对旧意图与新的责任和可见范围摘要，并重新选择对象</label>
            </div> : null}
          </div>

          <h2 className={styles.sectionHead}>
            <span>办理要求与回执</span>
            <i aria-hidden="true" />
          </h2>
          <div className={styles.gridRequirement}>
            <div className={styles.field}>
              <label htmlFor="dispatch-requirement">办理要求与交付物</label>
              <Input.TextArea
                disabled={locked}
                id="dispatch-requirement"
                onChange={(event) => update('requirement', event.target.value)}
                rows={2}
                value={draft.requirement}
              />
            </div>
            <div className={styles.field}>
              <span className={styles.fieldLabel} id="dispatch-attachment-label">
                附件
              </span>
              <div
                aria-describedby="dispatch-attachment-help"
                aria-labelledby="dispatch-attachment-label"
                className={styles.attachmentWell}
                role="group"
              >
                <Button className={styles.chipAdd} disabled>
                  <Icon name="plus" size={15} strokeWidth={2.2} />
                  添加附件
                </Button>
              </div>
              <p className={styles.caption} id="dispatch-attachment-help">
                <Icon name="help" size={14} />
                Word / PDF / 图片，单个不超过 20 MB；附件还传不上去，可先存草稿。
              </p>
            </div>
            <div className={styles.field}>
              <label htmlFor="dispatch-receipt">回执要求</label>
              <Input
                disabled={locked}
                id="dispatch-receipt"
                onChange={(event) => update('receipt', event.target.value)}
                value={draft.receipt}
              />
            </div>
            <div className={styles.field}>
              <span className={styles.fieldLabel} id="dispatch-reminder-label">
                提醒策略（可多选，各提醒一次）
              </span>
              <div
                aria-labelledby="dispatch-reminder-label"
                className={styles.reminderRow}
                role="group"
              >
                {REMINDER_CHOICES.map((choice) => {
                  const selected = draft.reminders.includes(choice);
                  return (
                    <button
                      disabled={locked || !draft.dueAt}
                      aria-pressed={selected}
                      className={selected ? styles.reminderOn : styles.reminderOff}
                      key={choice}
                      onClick={() => toggleReminder(choice)}
                      type="button"
                    >
                      {choice}
                    </button>
                  );
                })}
              </div>
              <p className={styles.caption}>仅记录提醒设置，自动提醒尚未启用；清空截止时间将取消提醒。</p>
            </div>
          </div>
        </div>

        <footer className={styles.footer}>
          <div className={styles.footerCopy}>
            <p className={styles.caption}>
              {targetCount === 0
                ? '发布前对方不可见。'
                : `发布后，${targetCount} 个交办对象的工作事项中各生成一条；发布前对方不可见。`}
            </p>
          </div>
          <div className={styles.footerActions}>
            <Button className={styles.footerButton} disabled={locked} onClick={store}>
              存草稿
            </Button>
            <Button
              className={`${styles.footerButton} ${styles.publishButton}`}
              disabled={locked || directoryState !== 'ready' || selected.length === 0 || selected.some((item) => !item.confirmed) || !timeValid || (legacy && !legacyConfirmed)}
              onClick={publish}
            >

              <span>发布</span>
            </Button>
            {phase === 'uncertain' ? <Button disabled={directoryState !== 'ready'} onClick={() => { if (frozen.current) void send(frozen.current); }}>重试原请求</Button> : null}
            {phase === 'success' ? <Button onClick={() => { frozen.current = null; setPhase('editing'); setNotice(null); setSelected([]); }}>新建交办</Button> : null}
          </div>
        </footer>
        {notice === null ? null : (
          <p className={styles.notice} role={noticeError ? 'alert' : 'status'}>
            <Icon name={notice === SAVE_NOTICE ? 'check' : 'alert'} size={14} />
            {notice}
          </p>
        )}
      </section>
    </div>
  );
}
