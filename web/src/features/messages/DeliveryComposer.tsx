import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Modal, Select } from 'antd';
import { useAuthStore } from '../../stores/authStore';
import { DeliveryPanel } from './DeliveryPanel';
import { deliveryError, prepareDelivery, readDeliveryRecipients } from './deliveryApi';
import type { DeliveryRecipient, DeliveryView, PrepareDelivery } from './deliveryApi';
import styles from './delivery.module.css';

function DeliveryComposer({ text }: { text: string }) {
  const generation = useAuthStore((state) => state.generation);
  const [recipients, setRecipients] = useState<DeliveryRecipient[]>([]);
  const [recipient, setRecipient] = useState<string>();
  const [note, setNote] = useState(text);
  const [deadline, setDeadline] = useState('');
  const [view, setView] = useState<DeliveryView | null>(null);
  const [pending, setPending] = useState<PrepareDelivery | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingRecipients, setLoadingRecipients] = useState(true);
  const live = useRef(true);
  const inFlight = useRef(false);
  const current = () => live.current && useAuthStore.getState().generation === generation
    && useAuthStore.getState().status === 'authenticated';

  useEffect(() => {
    live.current = true;
    void readDeliveryRecipients().then((result) => {
      if (live.current && useAuthStore.getState().generation === generation) setRecipients(result.items);
    }).catch((failure: unknown) => {
      if (live.current && useAuthStore.getState().generation === generation) setError(deliveryError(failure));
    }).finally(() => {
      if (live.current && useAuthStore.getState().generation === generation) setLoadingRecipients(false);
    });
    return () => { live.current = false; };
  }, [generation]);

  const expiresAt = Date.parse(deadline);
  const valid = recipient !== undefined && note.trim().length > 0 && Number.isFinite(expiresAt)
    && expiresAt > Date.now();
  const prepare = async () => {
    if (inFlight.current || !current() || pending === null && !valid) return;
    const request = pending ?? { recipient_ai_user_id: recipient!, note,
      send_expires_at: new Date(expiresAt).toISOString(), idempotency_key: crypto.randomUUID() };
    inFlight.current = true;
    setPending(request);
    setBusy(true);
    setError(null);
    try {
      const preview = await prepareDelivery(request);
      if (current()) setView(preview);
    } catch (failure) {
      if (current()) setError(deliveryError(failure));
    } finally {
      inFlight.current = false;
      if (current()) setBusy(false);
    }
  };

  if (view !== null) return <DeliveryPanel deliveryId={view.delivery_id} initial={view} />;
  return <div className={styles.form}>
    {error ? <Alert type="warning" showIcon title={error} /> : null}
    <label htmlFor="delivery-recipient">收件人</label>
    <Select id="delivery-recipient" value={recipient} loading={loadingRecipients}
      disabled={busy || pending !== null} showSearch={{ optionFilterProp: 'label' }}
      placeholder="选择已核验 IM 身份的同事"
      options={recipients.map((person) => ({ value: person.ai_user_id,
        label: `${person.display_name}（${person.ai_user_id}，IM ${person.im_user_id}）` }))}
      onChange={setRecipient} />
    {!loadingRecipients && error === null && recipients.length === 0 ? <p>当前没有可选的已核验 IM 收件人，请联系维护人员核对试点名单。</p> : null}
    <label htmlFor="delivery-note">要发给同事核实的文字与留言</label>
    <Input.TextArea id="delivery-note" value={note} disabled={busy || pending !== null}
      autoSize={{ minRows: 5, maxRows: 14 }} onChange={(event) => setNote(event.target.value)} />
    <label htmlFor="delivery-deadline">最迟发送时间（本机时间）</label>
    <Input id="delivery-deadline" type="datetime-local" value={deadline} disabled={busy || pending !== null}
      onChange={(event) => setDeadline(event.target.value)} />
    <p>预览后由你明确确认；确认后可关闭页面，尚未开始的发送可在消息页取消。</p>
    <Button type="primary" loading={busy} disabled={busy || pending === null && !valid}
      onClick={() => { void prepare(); }}>{pending === null ? '预览实际文字卡' : '检查原预览请求'}</Button>
  </div>;
}

export function DeliveryComposerButton({ text, disabled = false }: { text: string; disabled?: boolean }) {
  const generation = useAuthStore((state) => state.generation);
  const status = useAuthStore((state) => state.status);
  const [open, setOpen] = useState(false);
  const [openedGeneration, setOpenedGeneration] = useState(generation);
  const visible = open && status === 'authenticated' && openedGeneration === generation;
  return <>
    <Button disabled={disabled || status !== 'authenticated'} onClick={() => {
      setOpenedGeneration(generation); setOpen(true);
    }}>发给同事核实</Button>
    <Modal title="发给同事核实" open={visible} footer={null} width={760} destroyOnHidden
      onCancel={() => setOpen(false)}>
      {visible ? <DeliveryComposer key={generation} text={text} /> : null}
    </Modal>
  </>;
}
