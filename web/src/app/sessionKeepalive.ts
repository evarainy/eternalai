import { useEffect, useState } from 'react';
import { customInstance } from '../api/mutator';
import { useAuthStore } from '../stores/authStore';

/** Keep the signed-in user's OA Session alive only while they actually use the page. */

const TICK_MS = 60_000;
const ACTIVE_WINDOW_MS = 10 * 60_000;
const UNAVAILABLE_BANNER_AFTER = 3;
const ACTIVITY_EVENTS = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const;

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
    // Only real input counts; a reload or an idle new tab does not keep OA alive.
    let lastActivity = 0;
    const onActivity = () => { lastActivity = Date.now(); };
    const isCurrent = () => live && useAuthStore.getState().status === 'authenticated'
      && useAuthStore.getState().generation === generation;
    const tick = async () => {
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
    for (const event of ACTIVITY_EVENTS) {
      window.addEventListener(event, onActivity, { passive: true });
    }
    const timer = window.setInterval(() => { void tick(); }, TICK_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
      for (const event of ACTIVITY_EVENTS) window.removeEventListener(event, onActivity);
    };
  }, [status, generation]);

  return status === 'authenticated' ? message : null;
}
