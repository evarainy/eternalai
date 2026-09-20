import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Input, Space } from 'antd';
import { ApiError } from '../../api/mutator';
import type { LifecycleEventView } from '../../generated/work-objects/work-objects.schemas';
import { useAuthStore } from '../../stores/authStore';
import { lifecycleErrorText, readLifecycle, readLifecycleEvents, sendLifecycleCommand } from './workObjectLifecycleApi';
import type { LifecycleCommand, PendingLifecycleCommand } from './workObjectLifecycleApi';
import styles from './InternalWorkLifecyclePanel.module.css';

const labels = { assigned: '待本人接单', department_pending: '待部门接单', in_progress: '办理中', completed: '已办结' };
const eventLabels = { accept: '接单', feedback: '反馈进展', complete: '办结事项' };

export default function InternalWorkLifecyclePanel({ workObjectId }: { workObjectId: string }) {
  const generation = useAuthStore((state) => state.generation);
  return <LifecyclePanel key={`${generation}:${workObjectId}`} id={workObjectId} generation={generation} />;
}

function LifecyclePanel({ id, generation }: { id: string; generation: number }) {
  const client = useQueryClient();
  const live = useRef(false);
  const [snapshot, setSnapshot] = useState<Awaited<ReturnType<typeof readLifecycle>>>();
  const [events, setEvents] = useState<LifecycleEventView[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [text, setText] = useState('');
  const [review, setReview] = useState(false);
  const [pending, setPending] = useState<PendingLifecycleCommand>();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const current = () => live.current && useAuthStore.getState().generation === generation;

  async function refresh() {
    const [next, page] = await Promise.all([readLifecycle(id), readLifecycleEvents(id)]);
    if (!current()) return;
    setSnapshot(next); setEvents(page.items); setCursor(page.next_after_version);
  }
  useEffect(() => {
    live.current = true;
    const controller = new AbortController();
    void Promise.all([readLifecycle(id, controller.signal), readLifecycleEvents(id)]).then(([next, page]) => {
      if (controller.signal.aborted || !live.current || useAuthStore.getState().generation !== generation) return;
      setSnapshot(next); setEvents(page.items); setCursor(page.next_after_version);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted && live.current && useAuthStore.getState().generation === generation) setNotice(lifecycleErrorText(error));
    });
    return () => { live.current = false; controller.abort(); };
  }, [id, generation]);

  async function submit(command: PendingLifecycleCommand) {
    if (!current() || busy) return;
    setPending(command); setBusy(true); setReview(false); setNotice('');
    let committed = false;
    try {
      await sendLifecycleCommand(id, command);
      committed = true;
      if (!current()) return;
      setPending(undefined); setText(''); setNotice('操作已提交');
      await Promise.all([
        refresh(),
        client.cancelQueries({ queryKey: ['work-objects', generation] }).then(async () => {
          if (!current()) return;
          await client.invalidateQueries({ queryKey: ['work-objects', generation], refetchType: 'all' }, { throwOnError: true });
        }),
      ]);
    } catch (error) {
      if (!current()) return;
      if (committed) setNotice('操作已提交，最新状态暂未读取');
      else if (!(error instanceof ApiError) || error.status >= 500) setNotice('结果待确认，请重试同一次请求');
      else {
        setPending(undefined); setNotice(lifecycleErrorText(error));
        if (error.status === 412) {
          setSnapshot(undefined);
          try { await refresh(); } catch { if (current()) setNotice('事项已更新，最新状态暂未读取'); }
        }
      }
    } finally { if (current()) setBusy(false); }
  }
  function start(body: LifecycleCommand) {
    if (!snapshot || pending || busy) return;
    void submit({ body, key: crypto.randomUUID(), etag: snapshot.etag });
  }
  const commands = snapshot?.view.available_commands ?? [];
  return <section className={styles.panel} aria-label="事项办理">
    <h3>{snapshot ? labels[snapshot.view.status] : '读取办理状态'}</h3>
    {snapshot?.view.completed_at ? <p>办结时间：{snapshot.view.completed_at}</p> : null}
    {notice ? <Alert title={notice} type="info" /> : null}
    {snapshot?.view.unavailable_reason ? <Alert type="info" title={lifecycleErrorText(new ApiError(403, snapshot.view.unavailable_reason, ''))} /> : null}
    <Button disabled={busy} onClick={() => { void refresh().catch((error: unknown) => { if (current()) setNotice(lifecycleErrorText(error)); }); }}>重新读取</Button>
    {pending ? <Button disabled={busy} loading={busy} onClick={() => void submit(pending)}>重试同一次请求</Button> : <>
      {commands.includes('accept') ? <Button disabled={busy} onClick={() => start({ operation: 'accept' })}>接单</Button> : null}
      {commands.includes('feedback') || commands.includes('complete') ? <>
        <Input.TextArea aria-label="办理说明" value={text} maxLength={2000} disabled={busy || review} onChange={(event) => setText(event.target.value)} />
        <Space>
          {commands.includes('feedback') ? <Button disabled={busy || review || !text.trim()} onClick={() => start({ operation: 'feedback', text: text.trim() })}>反馈进展</Button> : null}
          {commands.includes('complete') ? <Button disabled={busy || review || !text.trim()} onClick={() => setReview(true)}>办结事项</Button> : null}
        </Space>
        {review ? <div className={styles.review}><p>本次办结说明：{text.trim()}</p><p>办结后不能继续修改</p>
          <Button onClick={() => start({ operation: 'complete', text: text.trim() })}>确认办结</Button>
          <Button onClick={() => setReview(false)}>返回修改</Button></div> : null}
      </> : null}
    </>}
    <div aria-label="办理活动">{events.map((event) => <article className={styles.event} key={event.event_id}>
      <strong>{eventLabels[event.operation]}</strong> <time>{event.occurred_at}</time><p>{event.text}</p>
    </article>)}</div>
    {cursor !== null ? <Button disabled={busy} onClick={() => {
      setBusy(true);
      void readLifecycleEvents(id, cursor).then((page) => {
        if (!current()) return;
        setEvents((old) => [...old, ...page.items.filter((item) => !old.some((event) => event.event_id === item.event_id))]);
        setCursor(page.next_after_version);
      }).catch((error: unknown) => { if (current()) setNotice(lifecycleErrorText(error)); })
        .finally(() => { if (current()) setBusy(false); });
    }}>加载更多</Button> : null}
  </section>;
}
