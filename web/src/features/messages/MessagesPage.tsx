import { useEffect, useState } from 'react';
import { Alert, Button, Drawer } from 'antd';
import { Link, useSearchParams } from 'react-router-dom';
import { PlaceholderPage } from '../../shared/ui/PlaceholderPage';
import { useAuthStore } from '../../stores/authStore';
import { DeliveryPanel } from './DeliveryPanel';
import { DELIVERY_STATE_LABELS, deliveryError, deliveryTime, readDeliveries } from './deliveryApi';
import type { DeliveryView } from './deliveryApi';
import { ImWorkspace } from './im/ImWorkspace';
import styles from './delivery.module.css';

function DeliveryWorkspace() {
  const generation = useAuthStore((state) => state.generation);
  const [params] = useSearchParams();
  const selected = params.get('delivery') || null;
  const [items, setItems] = useState<DeliveryView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let live = true;
    setBusy(true);
    setError(null);
    void readDeliveries().then((result) => {
      if (live && useAuthStore.getState().generation === generation) setItems(result.items);
    }).catch((failure: unknown) => {
      if (live && useAuthStore.getState().generation === generation) setError(deliveryError(failure));
    }).finally(() => {
      if (live && useAuthStore.getState().generation === generation) setBusy(false);
    });
    return () => { live = false; };
  }, [generation, revision]);

  return <section className={styles.workspace}>
    <Button loading={busy} onClick={() => setRevision((value) => value + 1)}>刷新送核记录</Button>
    <p>从 Chat 的附件区或 AI 助手结果选择“发给同事核实”，在这里查看原发送结果与本人回执。</p>
    {error ? <Alert type="warning" showIcon title={error} /> : null}
    <div className={styles.columns}>
      <nav aria-label="文字送核记录">
        {items.length === 0 && error === null ? <p>{busy ? '正在读取送核记录。' : '还没有可查看的送核记录。'}</p> : null}
        <ul className={styles.list}>{items.map((item) => <li key={item.delivery_id}>
          <Link to={`/messages?delivery=${encodeURIComponent(item.delivery_id)}`} aria-current={selected === item.delivery_id ? 'true' : undefined}>
            {item.viewer_role === 'initiator' ? `发给 ${item.recipient_ai_user_id}` : `来自 ${item.initiator_ai_user_id}`}
            <small>{DELIVERY_STATE_LABELS[item.state]} · {deliveryTime(item.created_at)}</small>
          </Link>
        </li>)}</ul>
      </nav>
      {selected ? <DeliveryPanel key={`${generation}/${selected}`} deliveryId={selected} /> : <p>选择一条记录查看原卡片与核实结果。</p>}
    </div>
  </section>;
}

/**
 * 消息页（FE_PLAN §4.2）：自建 IM 区。数据与接收器挂在 AppShell 的登录生命周期上（`useImLifecycle`），
 * 这里只负责呈现；按 auth generation 重新挂载，换身份时输入框里的上传随卸载中止。
 * 送核记录改为按需打开的抽屉：带 `?delivery=` 打开（AI 助手结果与送核卡片的既有链接），关掉即去掉参数。
 * 本人助手抽屉与会话内送核卡片在 lane 4 接入。
 */
export default function MessagesPage() {
  const status = useAuthStore((state) => state.status);
  const generation = useAuthStore((state) => state.generation);
  const [params, setParams] = useSearchParams();

  if (status !== 'authenticated') {
    return (
      <PlaceholderPage
        title="消息"
        icon="mail"
        reason="消息尚未启用，或当前登录状态还未确认。"
        nextStep="OA 里的通知和待办，请照原来的方式直接去 OA 查看。"
        actions={[{ label: '去工作事项', to: '/work-objects' }]}
      />
    );
  }
  return (
    <>
      <ImWorkspace key={generation} />
      <Drawer
        destroyOnHidden
        onClose={() => setParams((current) => {
          const next = new URLSearchParams(current);
          next.delete('delivery');
          return next;
        })}
        open={params.has('delivery')}
        size="large"
        title="送核记录"
      >
        <DeliveryWorkspace key={generation} />
      </Drawer>
    </>
  );
}
