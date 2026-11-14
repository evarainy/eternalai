import { useState } from 'react';
import type { FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { ApiError } from '../api/mutator';
import { peekReauthPath, reauthLoginNotice } from '../app/reauthRestore';
import { withAuthCookieLock } from '../app/sessionKeepalive';
import { identityQueryKey, notifyIdentityRecheck, notifyIdentitySwitch } from '../app/identity';
import { loginApiV1AuthLoginPost } from '../generated/auth/auth';
import { readMeApiV1MeGet } from '../generated/me/me';
import type { LoginApiV1AuthLoginPostBody } from '../generated/auth/auth.schemas';
import { Icon } from '../shared/ui/Icon';
import { useAuthStore } from '../stores/authStore';
import { getReturnPath } from './loginNavigation';
import { beginIMLogin, suspendIMIdentity } from '../features/messages/hostBridge';
import styles from './LoginPage.module.css';

const LOGIN_FAILED_MESSAGE = '登录失败，请检查登录信息后重试。';
const LOGIN_UNAVAILABLE_MESSAGE = '认证服务暂时不可用，请稍后重试。';

const WEEKDAYS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'] as const;

/** 写法与外壳顶部的时钟一致。 */
function todayLine(now: Date): string {
  return `${now.getMonth() + 1} 月 ${now.getDate()} 日 · ${WEEKDAYS[now.getDay()]}`;
}

/**
 * 登录页：和进去以后是同一副样子——左边是墨，右边是一张纸。
 *
 * 墨上只有产品名和今天的日期；纸上只有两个输入框和一枚主按钮。账号密码不进任何状态：提交时
 * 从表单里读出来，随即清空输入框。
 */
export default function LoginPage() {
  const [submitting, setSubmitting] = useState(false);
  const [missing, setMissing] = useState<{ loginid: boolean; userpassword: boolean }>({
    loginid: false, userpassword: false,
  });
  const [errorMessage, setErrorMessage] = useState<string>();
  const [sessionNotice] = useState(reauthLoginNotice);
  const markAuthenticated = useAuthStore((state) => state.markAuthenticated);
  const queryClient = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const loginid = String(data.get('loginid') ?? '');
    const userpassword = String(data.get('userpassword') ?? '');
    const nextMissing = { loginid: loginid === '', userpassword: userpassword === '' };
    setMissing(nextMissing);
    if (nextMissing.loginid || nextMissing.userpassword) return;
    form.reset();
    void handleLogin({ loginid, userpassword });
  };

  const handleLogin = async (credential: LoginApiV1AuthLoginPostBody) => {
    setSubmitting(true);
    setErrorMessage(undefined);
    try {
      suspendIMIdentity('unauthenticated');
      await withAuthCookieLock(async () => {
        notifyIdentitySwitch();
        const response = await loginApiV1AuthLoginPost(credential);
        if (response.authenticated !== true) {
          setErrorMessage(LOGIN_FAILED_MESSAGE);
          return;
        }
        // History state survives a reload of this page; the sealed copy's route is the fallback.
        const returnPath = getReturnPath(location.state ?? { from: peekReauthPath() });
        queryClient.clear();
        const identity = await readMeApiV1MeGet();
        markAuthenticated();
        // Seed the confirmed account before recheck can mistake an empty cache for a switch.
        queryClient.setQueryData(identityQueryKey(useAuthStore.getState().generation), identity);
        beginIMLogin();
        notifyIdentityRecheck();
        navigate(returnPath, { replace: true });
      });
    } catch (error) {
      setErrorMessage(
        error instanceof ApiError && error.status === 401
          ? LOGIN_FAILED_MESSAGE
          : LOGIN_UNAVAILABLE_MESSAGE,
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.screen}>
      <aside className={styles.ink}>
        <div className={styles.brand}>
          <span className={styles.brandMark}>
            <Icon name="bolt" size={20} strokeWidth={2} />
          </span>
          <span className={styles.brandName}>EternalAI</span>
        </div>
        <div className={styles.inkFoot}>
          <p className={styles.product}>办事工作台</p>
          <p className={styles.today}>{todayLine(new Date())}</p>
        </div>
      </aside>

      <main className={styles.paper}>
        <form autoComplete="off" className={styles.form} noValidate onSubmit={handleSubmit}>
          <h1 className={styles.title}>登录</h1>
          <p className={styles.lead}>用你的 OA 账号和密码</p>

          {sessionNotice !== null && !errorMessage ? (
            <p className={styles.notice} data-tone="warn" role="status">
              <Icon name="alert" size={16} />
              {sessionNotice}
            </p>
          ) : null}
          {errorMessage ? (
            <p className={styles.notice} data-tone="error" role="alert">
              <Icon name="alert" size={16} />
              {errorMessage}
            </p>
          ) : null}

          <label className={styles.field} data-focus-ring="host" data-invalid={missing.loginid ? 'true' : 'false'}>
            <span>账号</span>
            <input
              aria-invalid={missing.loginid}
              autoComplete="off"
              autoFocus
              disabled={submitting}
              name="loginid"
              onChange={() => setMissing((state) => (state.loginid ? { ...state, loginid: false } : state))}
              type="text"
            />
            {missing.loginid ? <em role="alert">请输入账号。</em> : null}
          </label>
          <label className={styles.field} data-focus-ring="host" data-invalid={missing.userpassword ? 'true' : 'false'}>
            <span>密码</span>
            <input
              aria-invalid={missing.userpassword}
              autoComplete="off"
              disabled={submitting}
              name="userpassword"
              onChange={() => setMissing((state) => (state.userpassword ? { ...state, userpassword: false } : state))}
              type="password"
            />
            {missing.userpassword ? <em role="alert">请输入密码。</em> : null}
          </label>

          <button
            className={`wb-btn wb-btn-primary ${styles.submit}`}
            data-loading={submitting ? 'true' : 'false'}
            disabled={submitting}
            type="submit"
          >
            {submitting ? <Icon name="retry" size={17} /> : null}
            {submitting ? '正在登录…' : '登录'}
          </button>

          <p className={styles.note}>登录信息只用于这一次 OA 验证，提交后立即清除。</p>
        </form>
      </main>
    </div>
  );
}
