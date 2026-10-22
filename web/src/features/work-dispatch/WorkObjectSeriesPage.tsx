import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Checkbox, Input, InputNumber, Modal, Switch, Table, Tag } from 'antd';
import { Link, useNavigate } from 'react-router-dom';
import { ApiError } from '../../api/mutator';
import { usePageContextRegistration } from '../../app/usePageContextRegistration';
import type { PageContextDeclaration } from '../../contracts/pageContext';
import { useAIDockStore } from '../../stores/aiDockStore';
import { isCurrentDraftSession, useDraftSession } from '../../stores/sessionDraftStore';
import type { DraftSessionToken } from '../../stores/sessionDraftStore';
import { DispatchComposer } from './WorkDispatchPage';
import type { DispatchRequest } from './dispatchApi';
import { accessLost, logDispatchFailure, seriesFailure } from './dispatchErrors';
import { browserZone, validTimeZone, zonedTimestamp } from './dispatchTime';
import { getSeries, listSeries, saveSeries, setSeriesEnabled, seriesHistory, seriesOccurrences } from './seriesApi';
import type { SeriesHistoryItem, SeriesOccurrence, SeriesPage, SeriesRequest, SeriesView } from './seriesApi';
import styles from './WorkObjectSeriesPage.module.css';

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const OPERATIONS = { create: '创建', update: '修改', enable: '开启', disable: '关闭' };
const weekdayLabel = (days: number[]) => days.map((day) => WEEKDAYS[day - 1]).join('、');
const emptyContext: PageContextDeclaration = { surface_id: 'work-object-series', organization_scope: null,
  work_object_refs: [], source_refs: [], filters: [], selected_metric: null, allowed_capabilities: [],
  freshness: { state: 'unknown', observed_at: null }, visibility: 'principal' };

export default function WorkObjectSeriesPage() {
  const token = useDraftSession();
  return token ? <SeriesPageContent key={`${token.generation}:${token.revision}`} token={token} /> : null;
}

function EmptySeriesContext() { usePageContextRegistration(emptyContext); return null; }

function SeriesPageContent({ token }: { token: DraftSessionToken }) {
  const navigate = useNavigate();
  const [page, setPage] = useState<SeriesPage<SeriesView> | null>(null);
  const [detail, setDetail] = useState<{ series: SeriesView; etag: string } | null>(null);
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [toggleBusy, setToggleBusy] = useState(false);
  const [togglePending, setTogglePending] = useState<{ id: string; enabled: boolean; etag: string; key: string } | null>(null);
  const alive = useRef(true);
  const epoch = useRef(0);
  const listSequence = useRef(0);
  const detailSequence = useRef(0);
  const toggleSending = useRef(false);
  const current = (requestEpoch: number) => alive.current && requestEpoch === epoch.current && isCurrentDraftSession(token);
  const clearPrivate = () => {
    epoch.current += 1; setPage(null); setDetail(null); setEditing(false); setCreating(false); setTogglePending(null);
    setNotice('周期规则或关联事项当前不可访问，已清除原展示。请重新读取核对。');
    useAIDockStore.getState().clearPageContext('work-object-series');
    useAIDockStore.getState().clearPageContext('work-object-series-history');
  };
  const loadList = async (cursor?: string) => {
    const requestEpoch = epoch.current;
    const sequence = ++listSequence.current;
    setLoading(true); setNotice(null);
    if (!cursor) setPage(null);
    try {
      const result = await listSeries(cursor);
      if (!current(requestEpoch) || sequence !== listSequence.current) return;
      setPage((old) => cursor && old ? { ...result, items: [...old.items, ...result.items] } : result);
    } catch (error) {
      if (!current(requestEpoch) || sequence !== listSequence.current) return;
      logDispatchFailure('series_list', error);
      if (accessLost(error)) clearPrivate();
      setNotice(seriesFailure(error));
    } finally { if (alive.current && sequence === listSequence.current) setLoading(false); }
  };
  const open = async (id: string) => {
    const requestEpoch = epoch.current;
    const sequence = ++detailSequence.current;
    setDetail(null); setEditing(false); setCreating(false); setNotice(null); setTogglePending(null);
    try {
      const response = await getSeries(id);
      if (!current(requestEpoch) || sequence !== detailSequence.current) return;
      if (!response.etag) throw new Error('series_etag_missing');
      setDetail({ series: response.data, etag: response.etag });
    } catch (error) {
      if (!current(requestEpoch) || sequence !== detailSequence.current) return;
      logDispatchFailure('series_read', error);
      if (accessLost(error)) clearPrivate();
      setNotice(seriesFailure(error));
    }
  };
  useEffect(() => {
    alive.current = true; void loadList();
    return () => { alive.current = false; epoch.current += 1; };
    // The key binds this page to one authenticated draft session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const toggle = async (request: NonNullable<typeof togglePending>) => {
    if (toggleSending.current) return;
    const requestEpoch = epoch.current;
    toggleSending.current = true; setToggleBusy(true); setNotice(null);
    try {
      const response = await setSeriesEnabled(request.id, request.enabled, request.etag, request.key);
      if (!current(requestEpoch)) return;
      if (!response.etag) throw new Error('series_etag_missing');
      setDetail({ series: response.data.series, etag: response.etag }); setTogglePending(null);
      setPage((old) => old ? { ...old, items: old.items.map((item) => item.series_id === request.id ? response.data.series : item) } : old);
      setNotice(response.data.replayed ? '已确认原启停操作。' : request.enabled ? '规则已开启，从下一计划时刻生效。' : '规则已关闭，已生成事项继续办理。');
      console.info('[work-dispatch]', { stage: 'request_confirmed', operation: request.enabled ? 'series_enable' : 'series_disable' });
    } catch (error) {
      if (!current(requestEpoch)) return;
      logDispatchFailure('series_toggle', error);
      if (accessLost(error)) clearPrivate();
      else if (error instanceof ApiError && error.status >= 400 && error.status < 500) setTogglePending(null);
      else setTogglePending(request);
      setNotice(seriesFailure(error));
    } finally { toggleSending.current = false; if (alive.current) setToggleBusy(false); }
  };
  return <div className={styles.page}>
    {!detail ? <EmptySeriesContext /> : null}
    <div className={styles.toolbar}><h1>周期任务</h1><Link to="/work-dispatch">单次任务交办</Link>
      <Button type="primary" onClick={() => { detailSequence.current += 1; setCreating(true); setEditing(false); setDetail(null); setTogglePending(null); }}>创建周期规则</Button>
      <Button loading={loading} onClick={() => void loadList()}>重新读取</Button></div>
    <p className={styles.caption}>规则修改仅影响以后生成的事项；上一期未办结，下一期仍按时生成。关闭期间不补发，启用期间漏期按计划日补齐。</p>
    {notice ? <p role="status" className={styles.notice}>{notice}</p> : null}
    <section className={styles.panel}>
      <Table<SeriesView> rowKey="series_id" dataSource={page?.items ?? []} pagination={false} loading={loading} scroll={{ x: 700 }}
        columns={[
          { title: '任务模板', key: 'title', render: (_, item) => <Button type="link" onClick={() => void open(item.series_id)}>{item.template.title}</Button> },
          { title: '生成计划', key: 'schedule', render: (_, item) => <>{weekdayLabel(item.weekdays)} {item.local_time}<br /><small>{item.timezone}</small></> },
          { title: '状态', key: 'enabled', render: (_, item) => <Tag color={item.enabled ? 'green' : 'default'}>{item.enabled ? '已开启' : '已关闭'}</Tag> },
          { title: '下一计划', key: 'next', render: (_, item) => zonedTimestamp(item.next_due_at, item.timezone) },
          { title: '版本', dataIndex: 'version' },
        ]} locale={{ emptyText: '当前没有可见周期规则' }} />
      {page?.next_cursor ? <Button onClick={() => void loadList(page.next_cursor!)}>加载更多规则</Button> : null}
    </section>
    {creating || editing && detail ? <SeriesEditor key={creating ? 'new' : `${detail!.series.series_id}:${detail!.series.version}`}
      token={token} existing={creating ? undefined : detail!} onAccessLost={clearPrivate}
      onCancel={() => { setCreating(false); setEditing(false); }}
      onSaved={(series, etag) => {
        setCreating(false); setEditing(false); setDetail({ series, etag });
        setPage((old) => old ? { ...old, items: [series, ...old.items.filter((item) => item.series_id !== series.series_id)] } : { items: [series], next_cursor: null });
        setNotice('周期规则已保存。');
      }} /> : null}
    {detail && !editing ? <section className={styles.panel}>
      <div className={styles.toolbar}><h2>{detail.series.template.title}</h2>
        <Button disabled={toggleBusy || togglePending !== null} onClick={() => setEditing(true)}>修改规则与模板</Button>
        <Button disabled={toggleBusy || togglePending !== null} onClick={() => {
          const request = { id: detail.series.series_id, enabled: !detail.series.enabled, etag: detail.etag, key: crypto.randomUUID() };
          setTogglePending(request); void toggle(request);
        }}>{detail.series.enabled ? '关闭规则' : '开启规则'}</Button>
        <Button onClick={() => void open(detail.series.series_id)}>重新读取详情</Button>
      </div>
      {togglePending && !toggleBusy ? <Button onClick={() => void toggle(togglePending)}>重试原启停请求</Button> : null}
      <p>{weekdayLabel(detail.series.weekdays)} {detail.series.local_time}，时区 {detail.series.timezone}，开始日期 {detail.series.start_date}。</p>
      <p>每期截止：计划生成日后 {detail.series.deadline_spec.days_after} 个自然日 {detail.series.deadline_spec.local_time}；补发沿用计划日，可能已逾期。</p>
      <p>{detail.series.template.dispatch_mode === 'collaborative' ? '主协办协作' : '各自独立办理'} · {detail.series.template.completion_mode === 'confirm_required' ? '派发人验收' : '正式提交即完成'}</p>
      <p className={styles.text}>{detail.series.template.requirement}</p>
      <p className={styles.caption}>回执材料：{detail.series.template.receipt_requirement || '无额外文本要求'}</p>
      <SeriesHistory key={`${detail.series.series_id}:${detail.series.version}`} seriesId={detail.series.series_id} token={token} includeRuleHistory
        onOpenWorkObject={(id) => navigate(`/work-objects?work_object_id=${encodeURIComponent(id)}`)} onAccessLost={clearPrivate} />
    </section> : null}
  </div>;
}

function SeriesEditor({ token, existing, onSaved, onCancel, onAccessLost }: {
  token: DraftSessionToken; existing?: { series: SeriesView; etag: string };
  onSaved: (series: SeriesView, etag: string) => void; onCancel: () => void; onAccessLost: () => void;
}) {
  const [weekdays, setWeekdays] = useState(existing?.series.weekdays ?? [1]);
  const [time, setTime] = useState(existing?.series.local_time ?? '09:00');
  const [zone, setZone] = useState(existing?.series.timezone ?? browserZone());
  const [start, setStart] = useState(existing?.series.start_date ?? localDate(browserZone()));
  const [days, setDays] = useState<number | null>(existing?.series.deadline_spec.days_after ?? 0);
  const [deadlineTime, setDeadlineTime] = useState(existing?.series.deadline_spec.local_time ?? '17:30');
  const [enabled, setEnabled] = useState(existing?.series.enabled ?? true);
  const [scheduleError, setScheduleError] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const frozen = useRef<{ key: string; body: SeriesRequest; existing?: { id: string; etag: string } } | null>(null);
  const submit = async (request: DispatchRequest, key: string) => {
    if (!frozen.current || frozen.current.key !== key) {
      if (!weekdays.length || !validTimeZone(zone) || !start || days === null || !Number.isInteger(days) || days < 0
        || !/^\d{2}:\d{2}(:\d{2})?$/.test(time) || !/^\d{2}:\d{2}(:\d{2})?$/.test(deadlineTime)) {
        setScheduleError('请选择星期、有效 IANA 时区、开始日期，以及非负整数自然日与固定时刻。');
        throw new ApiError(422, 'work_object_series_request_invalid', 'Invalid series schedule.');
      }
      setScheduleError(null);
      const { due_at: _dueAt, ...template } = request;
      frozen.current = { key, body: { weekdays: [...weekdays].sort((a, b) => a - b), local_time: time, timezone: zone,
        start_date: start, deadline_spec: { days_after: days, local_time: deadlineTime }, template, enabled },
        existing: existing ? { id: existing.series.series_id, etag: existing.etag } : undefined };
    }
    const response = await saveSeries(frozen.current.body, key, frozen.current.existing);
    if (!response.etag) throw new Error('series_etag_missing');
    if (alive.current && isCurrentDraftSession(token)) onSaved(response.data.series, response.etag);
    return response.data.replayed ? '已确认原规则保存。' : '周期规则已保存。';
  };
  return <section className={styles.editor}>
    <div className={styles.toolbar}><h2>{existing ? '编辑周期规则' : '创建周期规则'}</h2><Button onClick={onCancel}>取消编辑</Button></div>
    <DispatchComposer token={token} series submitLabel={existing ? '保存规则' : '创建规则'} onSubmit={submit}
      initialRequest={existing ? { ...existing.series.template, due_at: null } : undefined}
      initialAttachments={existing?.series.attachments} onAccessLost={onAccessLost}>
      {(locked) => <>
        <h3>生成计划</h3>
        <div className={styles.field}><label>生成星期</label><Checkbox.Group disabled={locked} value={weekdays}
          options={WEEKDAYS.map((label, index) => ({ label, value: index + 1 }))} onChange={(values) => setWeekdays(values as number[])} /></div>
        <div className={styles.grid}>
          <div className={styles.field}><label htmlFor="series-time">生成时刻</label><input id="series-time" type="time" step="1" value={time} disabled={locked} onChange={(event) => setTime(event.target.value)} /></div>
          <div className={styles.field}><label htmlFor="series-zone">规则时区（IANA）</label><Input id="series-zone" value={zone} disabled={locked} placeholder="Asia/Shanghai" onChange={(event) => setZone(event.target.value)} /></div>
          <div className={styles.field}><label htmlFor="series-start">开始日期</label><input id="series-start" type="date" value={start} disabled={locked} onChange={(event) => setStart(event.target.value)} /></div>
        </div>
        <div className={styles.grid}>
          <div className={styles.field}><label htmlFor="series-days">计划生成日后自然日数（0 为当天）</label><InputNumber id="series-days" min={0} precision={0} value={days} disabled={locked} onChange={setDays} /></div>
          <div className={styles.field}><label htmlFor="series-deadline">固定截止时刻（规则时区）</label><input id="series-deadline" type="time" step="1" value={deadlineTime} disabled={locked} onChange={(event) => setDeadlineTime(event.target.value)} /></div>
          <div className={styles.field}><label>规则状态</label><Switch checked={enabled} disabled={locked} checkedChildren="开启" unCheckedChildren="关闭" onChange={setEnabled} /></div>
        </div>
        <p className={styles.caption}>截止以计划日计算，补发不顺延。修改不影响历史实例；关闭不撤回已生成任务。夏令时不存在的本地时刻会明确拒绝，重复时刻取较早一次。</p>
        {scheduleError ? <p role="alert">{scheduleError}</p> : null}
      </>}
    </DispatchComposer>
  </section>;
}

function localDate(zone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  return ['year', 'month', 'day'].map((type) => parts.find((item) => item.type === type)!.value).join('-');
}

export interface SeriesHistoryPanelProps { seriesId: string; onOpenWorkObject: (workObjectId: string) => void; onAccessLost?: () => void }
export function SeriesHistoryPanel(props: SeriesHistoryPanelProps) {
  const token = useDraftSession();
  return token ? <SeriesHistory key={`${token.generation}:${token.revision}:${props.seriesId}`} {...props} token={token} /> : null;
}

function SeriesHistory({ seriesId, onOpenWorkObject, onAccessLost, token, includeRuleHistory = false }: SeriesHistoryPanelProps & { token: DraftSessionToken; includeRuleHistory?: boolean }) {
  const [history, setHistory] = useState<SeriesPage<SeriesHistoryItem> | null>(null);
  const [occurrences, setOccurrences] = useState<SeriesPage<SeriesOccurrence> | null>(null);
  const [snapshot, setSnapshot] = useState<SeriesHistoryItem | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [observedAt, setObservedAt] = useState<string | null>(null);
  const alive = useRef(true);
  const epoch = useRef(0);
  const sequence = useRef({ history: 0, occurrences: 0 });
  const current = (requestEpoch: number) => alive.current && requestEpoch === epoch.current && isCurrentDraftSession(token);
  const clearPrivate = () => {
    epoch.current += 1; setHistory(null); setOccurrences(null); setSnapshot(null); setObservedAt(null);
    useAIDockStore.getState().clearPageContext('work-object-series-history'); onAccessLost?.();
  };
  const fail = (operation: string, error: unknown) => {
    logDispatchFailure(operation, error);
    if (accessLost(error)) clearPrivate();
    setNotice(seriesFailure(error));
  };
  const loadHistory = async (cursor?: string) => {
    const requestEpoch = epoch.current; const requestSequence = ++sequence.current.history;
    try {
      const result = await seriesHistory(seriesId, cursor);
      if (!current(requestEpoch) || requestSequence !== sequence.current.history) return;
      setHistory((old) => cursor && old ? { ...result, items: [...old.items, ...result.items] } : result);
    } catch (error) { if (current(requestEpoch) && requestSequence === sequence.current.history) fail('series_history', error); }
  };
  const loadOccurrences = async (cursor?: string) => {
    const requestEpoch = epoch.current; const requestSequence = ++sequence.current.occurrences;
    try {
      const result = await seriesOccurrences(seriesId, cursor);
      if (!current(requestEpoch) || requestSequence !== sequence.current.occurrences) return;
      setOccurrences((old) => cursor && old ? { ...result, items: [...old.items, ...result.items] } : result);
      setObservedAt(new Date().toISOString());
    } catch (error) { if (current(requestEpoch) && requestSequence === sequence.current.occurrences) fail('series_occurrences', error); }
  };
  const refresh = async () => {
    setLoading(true); setNotice(null); setHistory(null); setOccurrences(null); setSnapshot(null); setObservedAt(null);
    await Promise.all([...(includeRuleHistory ? [loadHistory()] : []), loadOccurrences()]);
    if (alive.current) setLoading(false);
  };
  useEffect(() => {
    alive.current = true; void refresh();
    return () => { alive.current = false; epoch.current += 1; };
    // Series/session key guarantees a new request chain and private-state reset.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const context = useMemo<PageContextDeclaration>(() => ({ ...emptyContext, surface_id: 'work-object-series-history',
    work_object_refs: (occurrences?.items.flatMap((item) => item.items.map((task) => ({ work_object_id: task.work_object_id }))) ?? []).slice(0, 200),
    freshness: observedAt ? { state: 'reported', observed_at: observedAt } : { state: 'unknown', observed_at: null },
  }), [occurrences, observedAt]);
  usePageContextRegistration(context);
  return <div className={styles.history}>
    <div className={styles.toolbar}><h3>逐期历史与规则记录</h3><Button loading={loading} onClick={() => void refresh()}>重新读取历史</Button></div>
    {notice ? <p role="alert">{notice}</p> : null}
    <h4>生成期次</h4>
    <Table<SeriesOccurrence> rowKey="occurrence_id" dataSource={occurrences?.items ?? []} pagination={false} scroll={{ x: 700 }}
      columns={[
        { title: '计划时刻（UTC）', dataIndex: 'scheduled_at', render: (value: string) => value },
        { title: '模板版本', dataIndex: 'series_version' },
        { title: '生成状态', key: 'state', render: (_, item) => item.state === 'blocked' ? <Tag color="red">受阻：{item.blocked_code}</Tag> : <Tag color="green">已生成</Tag> },
        { title: '当前可见事项', key: 'tasks', render: (_, item) => <>{item.items.map((task) => <div key={task.work_object_id}>
          <Button type="link" onClick={() => onOpenWorkObject(task.work_object_id)}>{task.title}</Button><span>{task.status}</span>
          <small> 截止 {task.due_at ?? '未设置'}</small></div>)}{item.visible_count === 0 ? '无当前可见事项' : null}</> },
      ]} locale={{ emptyText: '暂无可见期次' }} />
    {occurrences?.next_cursor ? <Button onClick={() => void loadOccurrences(occurrences.next_cursor!)}>加载更多期次</Button> : null}
    {includeRuleHistory ? <><h4>规则操作记录</h4>
    <Table<SeriesHistoryItem> rowKey="version" dataSource={history?.items ?? []} pagination={false}
      columns={[{ title: '版本', dataIndex: 'version' }, { title: '操作', dataIndex: 'operation', render: (value: SeriesHistoryItem['operation']) => OPERATIONS[value] },
        { title: '时间（UTC）', dataIndex: 'occurred_at' }, { title: '当时内容', key: 'snapshot', render: (_, item) => <Button onClick={() => setSnapshot(item)}>查看版本</Button> }]} />
    {history?.next_cursor ? <Button onClick={() => void loadHistory(history.next_cursor!)}>加载更多操作记录</Button> : null}</> : null}
    <Modal open={snapshot !== null} title={snapshot ? `规则版本 ${snapshot.version}` : ''} footer={null} onCancel={() => setSnapshot(null)}>
      {snapshot ? <><h4>{snapshot.snapshot.template.title}</h4><p>{weekdayLabel(snapshot.snapshot.weekdays)} {snapshot.snapshot.local_time}（{snapshot.snapshot.timezone}）</p>
        <p>开始 {snapshot.snapshot.start_date} · {snapshot.snapshot.enabled ? '开启' : '关闭'}</p>
        <p>计划日后 {snapshot.snapshot.deadline_spec.days_after} 天 {snapshot.snapshot.deadline_spec.local_time} 截止</p>
        <p className={styles.text}>{snapshot.snapshot.template.requirement}</p><p>回执：{snapshot.snapshot.template.receipt_requirement}</p>
        <p>目标 {snapshot.snapshot.template.targets.length} 个 · {snapshot.snapshot.template.dispatch_mode === 'collaborative' ? '主协办协作' : '独立办理'} · {snapshot.snapshot.template.completion_mode === 'confirm_required' ? '派发人验收' : '提交即完成'}</p>
        <ul>{snapshot.snapshot.template.targets.map((target) => <li key={JSON.stringify(target)}>{target.kind === 'department' ? '部门' : '人员'}：{target.department_id}{target.kind === 'user' ? ` / ${target.directory_user_id}` : ''}</li>)}</ul>
        <p>附件：{snapshot.snapshot.attachments.map((item) => item.filename).join('、') || '无'}</p>
      </> : null}
    </Modal>
  </div>;
}
