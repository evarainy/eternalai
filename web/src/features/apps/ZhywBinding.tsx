import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { bindZhyw, systemEntryErrorMessage } from '../../api/systemEntry';
import { useAuthStore } from '../../stores/authStore';
import { useZhywBinding, zhywBindingKey } from './zhywBindingState';
import styles from './Systems.module.css';

export function ZhywAccountSettings() {
  const generation = useAuthStore((state) => state.generation);
  return <BindingForm key={generation} generation={generation} />;
}

function BindingForm({ generation }: { generation: number }) {
  const queryClient = useQueryClient();
  const binding = useZhywBinding();
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const loginId = String(new FormData(form).get('login_id') ?? '').trim();
    if (!loginId || saving) return;
    const current = () => useAuthStore.getState().generation === generation;
    setSaving(true);
    setMessage(null);
    try {
      const updated = await bindZhyw(loginId);
      if (!current()) return;
      await queryClient.cancelQueries({ queryKey: zhywBindingKey(generation) });
      if (!current()) return;
      queryClient.setQueryData(zhywBindingKey(generation), updated);
      form.reset();
      setMessage({ tone: 'ok', text: binding.data?.bound ? '绑定已更新。' : '已绑定，可以点击卡片登录。' });
    } catch (error) {
      if (current()) setMessage({ tone: 'error', text: systemEntryErrorMessage(error) });
    } finally {
      if (current()) setSaving(false);
    }
  }

  if (binding.isPending) return <p className={styles.bindingFact} role="status">正在读取综合业务登录设置…</p>;
  if (binding.isError) return <p className={styles.bindingNote} data-tone="error" role="alert">暂时读不到综合业务登录设置。</p>;
  if (!binding.data?.configured) return <p className={styles.bindingFact}>综合业务免登入口未配置。</p>;

  return (
    <form aria-label="综合业务登录账号" autoComplete="off" className={styles.binding}
      onSubmit={(event) => { void save(event); }}>
      <label className={styles.bindingField} data-focus-ring="host">
        <span>本人 OA 登录账号</span>
        <input disabled={saving} name="login_id" required type="text" />
      </label>
      {message ? <p className={styles.bindingNote} data-tone={message.tone}
        role={message.tone === 'error' ? 'alert' : 'status'}>{message.text}</p> : null}
      <div className={styles.bindingActions}>
        <button className="wb-btn wb-btn-primary wb-btn-sm" disabled={saving} type="submit">
          {binding.data.bound ? '更新绑定' : '绑定'}
        </button>
      </div>
    </form>
  );
}
