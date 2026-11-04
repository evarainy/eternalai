import { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Radio, Space, Tag } from 'antd';
import { Link } from 'react-router-dom';
import { useAuthStore } from '../../stores/authStore';
import {
  cancelDelivery, confirmDelivery, DELIVERY_STATE_LABELS, deliveryError, deliveryResultReason, deliveryTime,
  readDelivery, reconcileDelivery, verifyDelivery,
} from './deliveryApi';
import type { DeliveryView, VerificationResult, VerifyDelivery } from './deliveryApi';
import styles from './delivery.module.css';

export function DeliveryPanel({ deliveryId, initial = null }: {
  deliveryId: string;
  initial?: DeliveryView | null;
}) {
  const generation = useAuthStore((state) => state.generation);
  const [view, setView] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [result, setResult] = useState<VerificationResult>('verified');
  const [comment, setComment] = useState('');
  const [pendingVerification, setPendingVerification] = useState<VerifyDelivery | null>(null);
  const active = useRef(true);
  const inFlight = useRef(false);
  const current = useCallback(() => active.current && useAuthStore.getState().generation === generation
    && useAuthStore.getState().status === 'authenticated', [generation]);

  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  const run = useCallback(async (request: () => Promise<DeliveryView>, reading = false) => {
    if (inFlight.current || !current()) return;
    inFlight.current = true;
    setBusy(true);
    setError(null);
    try {
      const next = await request();
      if (current()) {
        setView(next);
        setUncertain(false);
        if (!reading) setPendingVerification(null);
      }
    } catch (failure) {
      if (current()) {
        setError(deliveryError(failure));
        if (!reading) setUncertain(true);
      }
    } finally {
      inFlight.current = false;
      if (current()) setBusy(false);
    }
  }, [current]);

  useEffect(() => {
    if (initial === null) void run(() => readDelivery(deliveryId), true);
  }, [deliveryId, initial, run]);

  const verify = () => {
    const request = pendingVerification ?? { result, comment, idempotency_key: crypto.randomUUID() };
    setPendingVerification(request);
    void run(() => verifyDelivery(deliveryId, request));
  };
  const isInitiator = view?.viewer_role === 'initiator';
  const canVerify = view !== null && !isInitiator && view.confirmed_at !== null
    && view.state !== 'cancelled' && view.state !== 'expired';
  const resultReason = view === null ? null : deliveryResultReason(view.result_code);

  return (
    <section className={styles.panel} aria-label="文字送核记录">
      {error ? <Alert type="warning" showIcon title={error} /> : null}
      <Space wrap>
        <Button loading={busy} onClick={() => { void run(() => readDelivery(deliveryId), true); }}>读取原记录</Button>
        <Link to={`/messages?delivery=${encodeURIComponent(deliveryId)}`}>在消息页查看</Link>
      </Space>
      {view === null ? <p role="status">{error ? '尚未读取到送核内容，请重新读取原记录。' : '正在读取送核内容。'}</p> : <>
        <dl className={styles.facts}>
          <div><dt>发起人</dt><dd>{view.initiator_ai_user_id}</dd></div>
          <div><dt>收件人</dt><dd>{view.recipient_ai_user_id}</dd></div>
          <div><dt>发送身份</dt><dd>EternalAI 助手，受发起人委托 · Bot {view.sender_bot_id}</dd></div>
          <div><dt>目标</dt><dd>一对一私聊 · {view.target_conversation_ref}</dd></div>
          <div><dt>最迟发送</dt><dd>{deliveryTime(view.send_expires_at)}</dd></div>
          <div><dt>发送结果</dt><dd><Tag>{DELIVERY_STATE_LABELS[view.state]}</Tag>{view.remote_message_id ? `消息编号 ${view.remote_message_id}` : ''}</dd></div>
          <div><dt>本人核实</dt><dd>{view.verifications.length === 0 ? '乙尚未明确核实' : '已有乙本人回执，见下方记录'}</dd></div>
        </dl>
        {resultReason === null ? null : <p>{resultReason}</p>}
        <p className={styles.label}>实际文字卡与留言</p>
        <pre className={styles.card}>{view.card_content}</pre>
        {view.state === 'unknown' || view.state === 'sending' ? (
          <Alert type="warning" showIcon title="这条消息可能已经发送。只核对原结果，不会自动重发。" />
        ) : null}
        {uncertain ? <p role="status">本次操作结果未确认，请先读取原记录。</p> : null}
        {isInitiator ? <Space wrap>
          {view.state === 'prepared' ? <Button type="primary" disabled={busy || uncertain}
            onClick={() => { void run(() => confirmDelivery(view)); }}>确认以上内容并私发</Button> : null}
          {view.state === 'prepared' || view.state === 'ready' ? <Button disabled={busy}
            onClick={() => { void run(() => cancelDelivery(deliveryId)); }}>取消尚未开始的发送</Button> : null}
          {view.state === 'unknown' || view.state === 'sending' ? <Button disabled={busy}
            onClick={() => { void run(() => reconcileDelivery(deliveryId)); }}>核对发送结果</Button> : null}
        </Space> : null}
        {canVerify ? <div className={styles.form}>
          <p className={styles.label}>我已核对以上文字卡与留言</p>
          <Radio.Group value={result} disabled={busy || pendingVerification !== null}
            onChange={(event) => setResult(event.target.value as VerificationResult)}>
            <Radio value="verified">已核实</Radio><Radio value="needs_changes">需修改</Radio>
          </Radio.Group>
          <label htmlFor={`verification-comment-${deliveryId}`}>核实留言（可不填）</label>
          <Input.TextArea id={`verification-comment-${deliveryId}`} value={comment} autoSize={{ minRows: 2, maxRows: 6 }}
            disabled={busy || pendingVerification !== null} onChange={(event) => setComment(event.target.value)} />
          <Button type="primary" disabled={busy} onClick={verify}>
            {pendingVerification === null ? '提交本人核实结果' : '检查原核实请求'}
          </Button>
        </div> : null}
        {view.verifications.length > 0 ? <div>
          <p className={styles.label}>乙本人核实记录</p>
          <ol className={styles.history}>{view.verifications.map((entry) => <li key={entry.verification_id}>
            <strong>{entry.result === 'verified' ? '已核实' : '需修改'}</strong> · {deliveryTime(entry.created_at)}
            {entry.comment ? <p className={styles.note}>{entry.comment}</p> : null}
          </li>)}</ol>
        </div> : null}
      </>}
    </section>
  );
}
