import { useEffect, useState } from 'react';
import { customInstance } from '../api/mutator';
import { renewApiV1AuthRenewPost, sessionApiV1AuthSessionGet } from '../generated/auth/auth';
import type { SessionStatusResponse } from '../generated/auth/auth.schemas';
import { useAuthStore } from '../stores/authStore';
import { discardReauthSnapshot } from './reauthRestore';

/**
 * Keep the signed-in user's OA Session and the application login alive only while they actually
 * use the page. The two schedulers share nothing but the real-input timestamp below.
 */

const TICK_MS = 60_000;
const ACTIVE_WINDOW_MS = 10 * 60_000;
const UNAVAILABLE_BANNER_AFTER = 3;
const ACTIVITY_EVENTS = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const;
const AUTH_COOKIE_LOCK = 'eternalai-auth-cookie';
const AUTH_LOCK_WAIT_MS = 10_000;
const EXPIRY_GRACE_MS = 2_000;
const MAX_TIMER_MS = 2_147_483_647;

// Only these DOM events count as use; status reads, OA keepalive and business APIs never do.
let lastInputAt = 0;
let inputWatchers = 0;
const onInput = () => { lastInputAt = Date.now(); };

function watchInput(): () => void {
  if (inputWatchers++ === 0) {
    for (const event of ACTIVITY_EVENTS) window.addEventListener(event, onInput, { passive: true });
  }
  return () => {
    if (--inputWatchers === 0) {
      for (const event of ACTIVITY_EVENTS) window.removeEventListener(event, onInput);
    }
  };
}

/**
 * Serialize renew / login / logout across tabs so an in-flight renew drains first. Without
 * Web Locks, or after a bounded wait, the request still goes out: a late cookie only points at a
 * revoked instance and costs one extra login.
 */
export async function withAuthCookieLock<T>(operation: () => Promise<T>): Promise<T> {
  const locks = typeof navigator === 'undefined' ? undefined : navigator.locks;
  if (locks === undefined || typeof locks.request !== 'function') return operation();
  const waiting = new AbortController();
  const timer = window.setTimeout(() => { waiting.abort(); }, AUTH_LOCK_WAIT_MS);
  let acquired = false;
  try {
    return await locks.request(AUTH_COOKIE_LOCK, { mode: 'exclusive', signal: waiting.signal }, () => {
      acquired = true;
      window.clearTimeout(timer);
      return operation();
    });
  } catch (error) {
    if (acquired) throw error;
    return operation();
  } finally {
    window.clearTimeout(timer);
  }
}

const OA_KEEPALIVE_STATUSES = [
  'alive', 'recovered', 'refreshed_by_other', 'backoff', 'unavailable',
  'binding_unavailable', 'rejected', 'captcha_required', 'browser_bound',
  'suspended', 'recovery_disabled', 'disabled',
] as const;
type OAKeepaliveStatus = (typeof OA_KEEPALIVE_STATUSES)[number];

export const OA_KEEPALIVE_MESSAGES = {
  captcha_required: 'OA 当前要求验证码，系统无法自动重新登录，请退出本系统后重新登录。',
  rejected: 'OA 密码可能已修改，自动登录已停止。请到“账号绑定”重新验证 OA 密码。',
  suspended: '检测到 OA 登录可能与其他登录冲突，已暂停自动重新登录。请到“账号绑定”重新验证，或联系管理员。',
  recovery_disabled: 'OA 登录已失效，自动重新登录未成功。请到“账号绑定”重新验证 OA 密码，或退出本系统后重新登录。',
  unavailable: '暂时无法连接 OA，请稍后再试；您的登录状态没有变化。',
} as const;

function parseKeepalive(value: unknown): OAKeepaliveStatus {
  const status = typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>).oa : undefined;
  const known = OA_KEEPALIVE_STATUSES.find((candidate) => candidate === status);
  if (known === undefined) throw new Error('oa_keepalive_invalid');
  return known;
}

export async function keepaliveOA(): Promise<OAKeepaliveStatus> {
  return parseKeepalive(await customInstance<unknown>({
    url: '/api/v1/credential-bindings/oa/keepalive', method: 'POST',
  }));
}

/** Returns the OA banner text to show, or null. */
export function useSessionKeepalive(): string | null {
  const status = useAuthStore((state) => state.status);
  const generation = useAuthStore((state) => state.generation);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    setMessage(null);
    if (status !== 'authenticated') return undefined;
    let live = true;
    let inFlight = false;
    let unavailableCount = 0;
    // Only real input since this sign-in counts; a reload or an idle new tab does not keep OA alive.
    const startedAt = Date.now();
    const stopWatching = watchInput();
    const isCurrent = () => live && useAuthStore.getState().status === 'authenticated'
      && useAuthStore.getState().generation === generation;
    const tick = async () => {
      const lastActivity = lastInputAt >= startedAt ? lastInputAt : 0;
      if (inFlight || document.visibilityState !== 'visible'
          || Date.now() - lastActivity > ACTIVE_WINDOW_MS) return;
      inFlight = true;
      try {
        const result = await keepaliveOA();
        if (!isCurrent()) return;
        if (result === 'disabled') {
          window.clearInterval(timer);
          return;
        }
        if (result === 'unavailable' || result === 'backoff') {
          unavailableCount += 1;
          if (unavailableCount >= UNAVAILABLE_BANNER_AFTER) {
            setMessage(OA_KEEPALIVE_MESSAGES.unavailable);
          }
          return;
        }
        unavailableCount = 0;
        setMessage(result === 'captcha_required' || result === 'rejected' || result === 'suspended'
          || result === 'recovery_disabled' ? OA_KEEPALIVE_MESSAGES[result] : null);
      } catch {
        // The keepalive is best effort; 401 is already handled by the shared mutator.
      } finally {
        inFlight = false;
      }
    };
    const timer = window.setInterval(() => { void tick(); }, TICK_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
      stopWatching();
    };
  }, [status, generation]);

  return status === 'authenticated' ? message : null;
}

/**
 * Application login renewal, independent of the OA keepalive: its `disabled`, in-flight state and
 * activity window never stop this one. Renews when real input happened after the previous renew
 * request; the server alone decides whether a new ticket is issued.
 */
export function useAppSessionRenewal(): void {
  const status = useAuthStore((state) => state.status);
  const generation = useAuthStore((state) => state.generation);

  useEffect(() => {
    if (status !== 'authenticated') return undefined;
    let live = true;
    let renewInFlight = false;
    let lastRenewRequestAt = Date.now();
    let expiryTimer: number | undefined;
    let localDeadline = Number.POSITIVE_INFINITY;
    const stopWatching = watchInput();
    const isCurrent = () => live && useAuthStore.getState().status === 'authenticated'
      && useAuthStore.getState().generation === generation;

    // The server clock is authoritative; translate its idle deadline into this browser's clock.
    const schedule = (session: SessionStatusResponse | null | undefined, receivedAt: number) => {
      const idle = Date.parse(session?.idle_expires_at ?? '');
      const server = Date.parse(session?.server_time ?? '');
      if (!Number.isFinite(idle) || !Number.isFinite(server)) return;
      localDeadline = receivedAt + (idle - server);
      window.clearTimeout(expiryTimer);
      expiryTimer = window.setTimeout(() => { void check(); },
        Math.min(Math.max(localDeadline - Date.now() + EXPIRY_GRACE_MS, 0), MAX_TIMER_MS));
    };
    // 200 reschedules (another tab may have renewed); 401 is handled by the shared mutator.
    const check = async () => {
      try {
        const session = await sessionApiV1AuthSessionGet();
        if (isCurrent()) schedule(session, Date.now());
      } catch { /* 503 / network: stay quiet; the next request decides. */ }
    };
    const tick = async () => {
      if (renewInFlight || lastInputAt <= lastRenewRequestAt) return;
      lastRenewRequestAt = Date.now();
      renewInFlight = true;
      try {
        const result = await withAuthCookieLock(() => renewApiV1AuthRenewPost());
        if (!isCurrent()) return;
        if (result.status === 'ended') {
          // Forget the copy first so the transition below cannot capture it (A3).
          discardReauthSnapshot('principal_changed');
          useAuthStore.getState().markUnauthenticated(generation);
          return;
        }
        schedule(result.session, Date.now());
      } catch {
        // 503 / network: silent until the next tick with new input; 401 went through the mutator.
      } finally {
        renewInFlight = false;
      }
    };
    const onVisible = () => {
      if (document.visibilityState === 'visible' && Date.now() >= localDeadline) void check();
    };

    void check();
    const timer = window.setInterval(() => { void tick(); }, TICK_MS);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      live = false;
      window.clearInterval(timer);
      window.clearTimeout(expiryTimer);
      document.removeEventListener('visibilitychange', onVisible);
      stopWatching();
    };
  }, [status, generation]);
}
