import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../../api/mutator';
import { activateBrowserBinding, getBrowserBindingStatus } from '../../api/browserBinding';
import {
  bindPasswordApiV1CredentialBindingsTargetSystemPut as bindPassword,
  unbindPasswordApiV1CredentialBindingsTargetSystemDelete as unbindPassword,
} from '../../generated/credential-bindings/credential-bindings';
import { Icon } from '../../shared/ui/Icon';
import { useAuthStore } from '../../stores/authStore';
import { passwordBindingQueryKey, usePasswordBinding } from './passwordAccounts';
import type { PasswordTarget } from './systems';
import styles from './Systems.module.css';

type Busy = null | 'bind' | 'activate' | 'unbind';

/**
 * 一个系统的账号设置：看状态、输账号密码绑定、解除绑定。OA 另有「启用自动登录」。
 *
 * 账号密码不进任何状态：提交时从表单里读出来，随即清空输入框。换了登录身份后发回来的结果一律丢弃。
 * 换了登录身份，整张表单重建：上一个人没提交的输入、进行到一半的操作和提示都不留给下一个人。
 */
export function PasswordBinding({ name, target }: { name: string; target: PasswordTarget }) {
  const generation = useAuthStore((state) => state.generation);
  return <PasswordForm generation={generation} key={`${generation}/${target}`} name={name} target={target} />;
}

function PasswordForm({ generation, name, target }: { generation: number; name: string; target: PasswordTarget }) {
  const queryClient = useQueryClient();
  const queryKey = passwordBindingQueryKey(generation, target);
  const browserQueryKey = ['browser-binding', generation, 'oa'] as const;
  const bindingQuery = usePasswordBinding(target);
  const browserQuery = useQuery({
    queryKey: browserQueryKey,
    queryFn: ({ signal }) => getBrowserBindingStatus(signal),
    enabled: target === 'oa',
    refetchOnWindowFocus: 'always',
  });
  const [busy, setBusy] = useState<Busy>(null);
  const [missing, setMissing] = useState({ login_id: false, password: false });
  const [unbindArmed, setUnbindArmed] = useState(false);
  const [message, setMessage] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);

  const binding = bindingQuery.data;
  const bound = binding?.bound === true;
  // 正在提交或状态没读准时，不拿旧的自动登录状态做判断，也不把「不知道」说成「未启用」。
  const browserUnknown = busy !== null || browserQuery.isFetching || browserQuery.isError
    || browserQuery.data === undefined;
  const browser = target !== 'oa' || browserUnknown ? undefined : browserQuery.data;
  const browserFailed = browserQuery.isError && busy === null && !browserQuery.isFetching;
  const autoLoginOn = browser?.binding_state === 'active';
  const canActivate = browser?.binding_id != null && browser.binding_revision != null
    && browser.binding_state === 'unverified';
  const current = () => useAuthStore.getState().generation === generation;

  const fail = (error: unknown, verb: string) => {
    setMessage({
      tone: 'error',
      text: error instanceof ApiError && error.status >= 400 && error.status < 500
        ? `${verb}没有成功：${name}没有通过这次验证，请核对账号和密码。`
        : `${verb}没有成功：凭证服务暂时不可用，请稍后再试。`,
    });
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy !== null) return;
    const form = event.currentTarget;
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    const action = submitter instanceof HTMLButtonElement && submitter.value === 'activate' ? 'activate' : 'bind';
    const data = new FormData(form);
    const credential = {
      login_id: String(data.get('login_id') ?? ''),
      password: String(data.get('password') ?? ''),
    };
    const nextMissing = { login_id: credential.login_id === '', password: credential.password === '' };
    setMissing(nextMissing);
    if (nextMissing.login_id || nextMissing.password) return;
    const selection = action === 'activate' && browser?.binding_id != null && browser.binding_revision != null
      ? { binding_id: browser.binding_id, binding_revision: browser.binding_revision } : null;
    if (action === 'activate' && selection === null) return;
    form.reset();
    setBusy(action);
    setMessage(null);
    setUnbindArmed(false);
    try {
      if (selection !== null) {
        const updated = await activateBrowserBinding(selection, credential);
        if (!current()) return;
        await queryClient.cancelQueries({ queryKey: browserQueryKey });
        if (!current()) return;
        queryClient.setQueryData(browserQueryKey, updated);
        setMessage({ tone: 'ok', text: '自动登录已启用。' });
        return;
      }
      const updated = await bindPassword(target, credential);
      if (!current()) return;
      queryClient.setQueryData(queryKey, updated);
      if (target === 'oa') await queryClient.resetQueries({ queryKey: browserQueryKey });
      if (!current()) return;
      setMessage({
        tone: 'ok',
        text: target === 'oa' ? (bound ? '密码已更新。' : '已绑定。') : `已保存。${name}接入后会用这组账号验证。`,
      });
    } catch (error) {
      if (!current()) return;
      fail(error, action === 'activate' ? '启用' : '绑定');
      if (target === 'oa') void queryClient.resetQueries({ queryKey: browserQueryKey });
    } finally {
      if (current()) setBusy(null);
    }
  };

  const unbind = async () => {
    if (busy !== null) return;
    if (!unbindArmed) { setUnbindArmed(true); return; }
    setBusy('unbind');
    setMessage(null);
    setUnbindArmed(false);
    try {
      const updated = await unbindPassword(target);
      if (!current()) return;
      queryClient.setQueryData(queryKey, updated);
      if (target === 'oa') await queryClient.resetQueries({ queryKey: browserQueryKey });
      if (!current()) return;
      setMessage({ tone: 'ok', text: '已解除绑定。' });
    } catch {
      if (!current()) return;
      setMessage({ tone: 'error', text: '解除没有成功：凭证服务暂时不可用，请稍后再试。' });
      if (target === 'oa') void queryClient.resetQueries({ queryKey: browserQueryKey });
    } finally {
      if (current()) setBusy(null);
    }
  };

  return (
    <form autoComplete="off" className={styles.binding} noValidate onSubmit={(event) => { void submit(event); }}>
      {bindingQuery.isError ? (
        <p className={styles.bindingNote} data-tone="error" role="alert">
          读不到绑定状态，这不等于没绑上。
          <button onClick={() => { void bindingQuery.refetch(); }} type="button">重试</button>
        </p>
      ) : null}
      {target === 'oa' && bound && browserFailed ? (
        <p className={styles.bindingNote} data-tone="error" role="alert">
          读不到自动登录状态，这不等于没启用。
          <button onClick={() => { void browserQuery.refetch(); }} type="button">重试</button>
        </p>
      ) : null}
      {target === 'oa' && bound && !browserFailed ? (
        <p className={styles.bindingFact}>
          <span
            aria-hidden="true"
            className={styles.light}
            data-state={browserUnknown ? 'busy' : autoLoginOn ? 'on' : 'off'}
          />
          {browserUnknown ? '正在读取自动登录状态' : autoLoginOn ? '自动登录已启用' : '自动登录未启用'}
        </p>
      ) : null}
      {target === 'oa' && binding?.poll_status === 'retrying' ? (
        <p className={styles.bindingFact}>连续 {binding.poll_failure_count} 次没连上，稍后会自动再试。</p>
      ) : null}
      <label className={styles.bindingField} data-focus-ring="host" data-invalid={missing.login_id ? 'true' : 'false'}>
        <span>账号</span>
        <input
          aria-invalid={missing.login_id}
          autoComplete="off"
          disabled={busy !== null}
          name="login_id"
          onChange={() => setMissing((state) => (state.login_id ? { ...state, login_id: false } : state))}
          type="text"
        />
      </label>
      <label className={styles.bindingField} data-focus-ring="host" data-invalid={missing.password ? 'true' : 'false'}>
        <span>密码</span>
        <input
          aria-invalid={missing.password}
          autoComplete="off"
          disabled={busy !== null}
          name="password"
          onChange={() => setMissing((state) => (state.password ? { ...state, password: false } : state))}
          type="password"
        />
      </label>
      {missing.login_id || missing.password ? <p className={styles.bindingNote} data-tone="error" role="alert">请输入账号和密码。</p> : null}
      {message === null ? null : (
        <p className={styles.bindingNote} data-tone={message.tone} role={message.tone === 'error' ? 'alert' : 'status'}>
          {message.text}
        </p>
      )}
      <div className={styles.bindingActions}>
        <button className="wb-btn wb-btn-primary wb-btn-sm" disabled={busy !== null} type="submit" value="bind">
          {busy === 'bind' ? <Icon className={styles.spin} name="retry" size={15} /> : null}
          {bound ? '更新密码' : '绑定'}
        </button>
        {canActivate ? (
          <button className="wb-btn wb-btn-sm" disabled={busy !== null} type="submit" value="activate">
            {busy === 'activate' ? <Icon className={styles.spin} name="retry" size={15} /> : null}
            启用自动登录
          </button>
        ) : null}
        <span className="wb-grow" />
        {bound ? (
          <button
            className="wb-btn wb-btn-urge wb-btn-sm"
            data-sure={unbindArmed ? 'true' : 'false'}
            disabled={busy !== null}
            onBlur={() => setUnbindArmed(false)}
            onClick={() => { void unbind(); }}
            type="button"
          >
            {unbindArmed ? '确认解除' : '解除绑定'}
          </button>
        ) : null}
      </div>
    </form>
  );
}

/** 还没有接入的系统：绑定入口先占着位置，接入后换成真的。 */
export function PendingBinding() {
  return (
    <form className={styles.binding} onSubmit={(event) => event.preventDefault()}>
      <label className={styles.bindingField}><span>账号</span><input disabled name="login_id" type="text" /></label>
      <label className={styles.bindingField}><span>密码</span><input disabled name="password" type="password" /></label>
      <div className={styles.bindingActions}>
        <button className="wb-btn wb-btn-primary wb-btn-sm" disabled type="submit">绑定</button>
        <span className={styles.bindingPending}>这个系统还没有接入</span>
      </div>
    </form>
  );
}
