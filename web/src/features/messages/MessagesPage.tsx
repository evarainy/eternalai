import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Space } from 'antd';
import { Link, useSearchParams } from 'react-router-dom';
import { PlaceholderPage } from '../../shared/ui/PlaceholderPage';
import { useAuthStore } from '../../stores/authStore';
import { DeliveryPanel } from './DeliveryPanel';
import { DELIVERY_STATE_LABELS, deliveryError, deliveryTime, readDeliveries } from './deliveryApi';
import type { DeliveryView } from './deliveryApi';
import styles from './delivery.module.css';

function DeliveryWorkspace() {
  const generation = useAuthStore((state) => state.generation);
  const [params] = useSearchParams();
  const selected = params.get('delivery');
  const [items, setItems] = useState<DeliveryView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const sequence = useRef(0);

  useEffect(() => {
    let live = true;
    const request = ++sequence.current;
    setBusy(true);
    setError(null);
    void readDeliveries().then((result) => {
      if (live && sequence.current === request && useAuthStore.getState().generation === generation) setItems(result.items);
    }).catch((failure: unknown) => {
      if (live && sequence.current === request && useAuthStore.getState().generation === generation) setError(deliveryError(failure));
    }).finally(() => {
      if (live && sequence.current === request && useAuthStore.getState().generation === generation) setBusy(false);
    });
    return () => { live = false; };
  }, [generation, revision]);

  return <section className={styles.workspace}>
    <Space wrap><h1>消息</h1><Button loading={busy} onClick={() => setRevision((value) => value + 1)}>刷新送核记录</Button></Space>
    <p>从 AI 助手的文字结果选择“发给同事核实”，在这里查看原发送结果与本人回执。</p>
    {error ? <Alert type="warning" showIcon title={error} /> : null}
    <div className={styles.columns}>
      <nav aria-label="文字送核记录">
        {items.length === 0 && error === null ? <p>{busy ? '正在读取送核记录。' : '还没有可查看的文字送核记录。'}</p> : null}
        <ul className={styles.list}>{items.map((item) => <li key={item.delivery_id}>
          <Link to={`/messages?delivery=${encodeURIComponent(item.delivery_id)}`} aria-current={selected === item.delivery_id ? 'true' : undefined}>
            {item.viewer_role === 'initiator' ? `发给 ${item.recipient_ai_user_id}` : `来自 ${item.initiator_ai_user_id}`}
            <small>{DELIVERY_STATE_LABELS[item.state]} · {deliveryTime(item.created_at)}</small>
          </Link>
        </li>)}</ul>
      </nav>
      {selected ? <DeliveryPanel key={`${generation}/${selected}`} deliveryId={selected} /> : <p>选择一条记录查看文字卡与核实结果。</p>}
    </div>
  </section>;
}

export default function MessagesPage() {
  const status = useAuthStore((state) => state.status);
  const generation = useAuthStore((state) => state.generation);
  if (status === 'authenticated') return <DeliveryWorkspace key={generation} />;
  return (
    <PlaceholderPage
      title="消息"
      icon="mail"
      reason="消息功能还没有开发，这里收不到也发不出消息。"
      nextStep="OA 里的通知和待办，请照原来的方式直接去 OA 查看。"
      actions={[{ label: '去工作事项', to: '/work-objects' }]}
    />
  );
}
