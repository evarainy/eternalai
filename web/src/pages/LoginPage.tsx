import { useEffect, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
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

const pad = (value: number) => String(value).padStart(2, '0');

/** 现在几点几分；每到整分跳一次。 */
function useMinute(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    let timer: number;
    const schedule = () => {
      timer = window.setTimeout(() => {
        setNow(new Date());
        schedule();
      }, 60_000 - (Date.now() % 60_000));
    };
    schedule();
    return () => window.clearTimeout(timer);
  }, []);
  return now;
}

/** 墨上的大钟：时间、日期、产品名。日期写法与外壳顶部的时钟一致。 */
function Hero() {
  const now = useMinute();
  return (
    <div className={styles.hero}>
      <p aria-hidden="true" className={styles.clock}>
        {pad(now.getHours())}
        <i>:</i>
        {pad(now.getMinutes())}
      </p>
      <p className={styles.today}>{`${now.getMonth() + 1} 月 ${now.getDate()} 日 · ${WEEKDAYS[now.getDay()]}`}</p>
      <p className={styles.product}>办事工作台</p>
    </div>
  );
}

/**
 * 登录页：一整屏墨，墨上一只大钟，右边浮着一张纸。
 *
 * 墨上只有产品名、时间和日期，背后两团光缓缓游动；纸上只有两个输入框和一枚主按钮。账号密码不进
 * 任何状态：提交时从表单里读出来，随即清空输入框。
 */
export default function LoginPage() {
  const [submitting, setSubmitting] = useState(false);
  const [missing, setMissing] = useState<{ loginid: boolean; userpassword: boolean }>({
    loginid: false, userpassword: false,
  });
  const [errorMessage, setErrorMessage] = useState<string>();
  const [capsLock, setCapsLock] = useState(false);
  const [sessionNotice] = useState(reauthLoginNotice);
  const markAuthenticated = useAuthStore((state) => state.markAuthenticated);
  const queryClient = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();

  const watchCapsLock = (event: KeyboardEvent<HTMLInputElement>) => {
    setCapsLock(event.getModifierState('CapsLock'));
  };

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
      <div aria-hidden="true" className={styles.backdrop}>
        <i />
        <i />
        <span />
      </div>

      <aside className={styles.ink}>
        <div className={styles.brand}>
          <span className={styles.brandMark}>
            <Icon name="bolt" size={20} strokeWidth={2} />
          </span>
          <span className={styles.brandName}>EternalAI</span>
        </div>
        <Hero />
      </aside>

      <main className={styles.stage}>
        <form autoComplete="off" className={styles.card} noValidate onSubmit={handleSubmit}>
          <h1 className={styles.title}>登录</h1>

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
            <span className={styles.box}>
              <Icon name="user" size={18} />
              <input
                aria-invalid={missing.loginid}
                autoComplete="off"
                autoFocus
                disabled={submitting}
                name="loginid"
                onChange={() => setMissing((state) => (state.loginid ? { ...state, loginid: false } : state))}
                type="text"
              />
            </span>
            {missing.loginid ? <em role="alert">请输入账号。</em> : null}
          </label>
          <label className={styles.field} data-focus-ring="host" data-invalid={missing.userpassword ? 'true' : 'false'}>
            <span>密码</span>
            <span className={styles.box}>
              <Icon name="lock" size={18} />
              <input
                aria-invalid={missing.userpassword}
                autoComplete="off"
                disabled={submitting}
                name="userpassword"
                onBlur={() => setCapsLock(false)}
                onChange={() => setMissing((state) => (state.userpassword ? { ...state, userpassword: false } : state))}
                onKeyDown={watchCapsLock}
                onKeyUp={watchCapsLock}
                type="password"
              />
            </span>
            {missing.userpassword ? <em role="alert">请输入密码。</em> : null}
            {capsLock && !missing.userpassword ? <em data-tone="warn" role="status">大写锁定已打开。</em> : null}
          </label>

          <button
            className={`wb-btn wb-btn-primary ${styles.submit}`}
            data-loading={submitting ? 'true' : 'false'}
            disabled={submitting}
            type="submit"
          >
            {submitting ? <Icon name="retry" size={17} /> : null}
            {submitting ? '正在登录…' : '登录'}
            {submitting ? null : <Icon className={styles.go} name="right" size={17} />}
          </button>
        </form>
      </main>
    </div>
  );
}
