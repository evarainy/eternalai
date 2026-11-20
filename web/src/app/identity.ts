import { useEffect, useState, useSyncExternalStore } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { QueryClient, UseQueryResult } from '@tanstack/react-query';
import { customInstance } from '../api/mutator';
import { readMeApiV1MeGet } from '../generated/me/me';
import type { MeResponse } from '../generated/me/me.schemas';
import { useAuthStore } from '../stores/authStore';
import { withAuthCookieLock } from './sessionKeepalive';
import {
  beginIMLogin, imBridgeBlocked, imLogoutInProgress, suspendIMIdentity,
} from '../features/messages/hostBridge';

/**
 * 「我是谁」只问后端一次，三个消费点（顶栏、用户菜单、AI 助手问候语）读同一份缓存。
 *
 * 查询在 `status === 'unauthenticated'` 时关掉。这不只是省一次请求：401 会让
 * `mutator.ts` 置为未认证，`AuthenticationEffects` 随即 `queryClient.clear()`，
 * 若查询仍处于 enabled，清空后挂载中的 observer 会立刻重取，再拿到 401——一个自我维持的循环。
 * 关掉它，循环就不存在。
 */
export const IDENTITY_QUERY_KEY = ['me'] as const;
export const identityQueryKey = (generation: number) => [...IDENTITY_QUERY_KEY, generation] as const;

export interface CurrentIdentity {
  /** 来自服务端签名的会话票据；只要还登录着就一定有。 */
  displayName: string | null;
  /** 来自 OA；取不到就是 null，绝不回落到任何默认部门。 */
  departmentName: string | null;
  /** 常量路径或 null；OA 侧的头像地址属人员信息，前端永远看不到它。 */
  avatarPath: string | null;
}

export function useIdentityQuery(): UseQueryResult<MeResponse> {
  const status = useAuthStore((state) => state.status);
  const generation = useAuthStore((state) => state.generation);

  return useQuery({
    queryKey: identityQueryKey(generation),
    queryFn: () => readMeApiV1MeGet(),
    enabled: status !== 'unauthenticated' && !imLogoutInProgress(),
    // Never refetched in the background. Startup, login and revalidation seed each generation with
    // a confirmed result; a background refetch after another tab switched accounts would put that
    // account into this generation and hide the switch. Only an empty key is fetched.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

export function useCurrentIdentity(): CurrentIdentity {
  const { data } = useIdentityQuery();

  return {
    displayName: data?.display_name ?? null,
    departmentName: data?.org?.department_name ?? null,
    avatarPath: data?.avatar_path ?? null,
  };
}

/**
 * 启动确认：挂在路由之上跑一次，把 `unknown` 收敛成 `authenticated` 或 `unauthenticated`。
 *
 * - 200 → `markAuthenticated()`。
 * - 401 → `mutator.ts` 已经 `markUnauthenticated(generation)`，这里不需要再做。
 * - **网络错误 / 后端不可达（重试耗尽）→ 什么都不做，停在 `unknown`。** 把「连不上后端」翻译成
 *   「你没登录」会在每次抖动时把人推到登录页，训练用户在异常状态下反复输密码——那是钓鱼形状的习惯。
 */
export function useIdentityBootstrap(): void {
  const queryClient = useQueryClient();
  const status = useAuthStore((state) => state.status);
  const generation = useAuthStore((state) => state.generation);
  const { data } = useIdentityQuery();
  // A switch announced during startup: wait until revalidation restarts the check after it.
  const switching = useIdentityPending() === 'switching';

  useEffect(() => {
    // 只把 `unknown` 收敛掉。若已经明确退出登录，一个还在路上的启动确认**不得**把人重新标成已登录。
    if (!switching && !imLogoutInProgress() && data !== undefined && status === 'unknown' &&
        useAuthStore.getState().status === 'unknown' &&
        useAuthStore.getState().generation === generation) {
      useAuthStore.getState().markAuthenticated();
      seedConfirmedIdentity(queryClient, data);
    }
  }, [data, status, generation, queryClient, switching]);
}

const AUTH_CHANNEL = 'eternalai-auth';
const RECHECK_MESSAGE = 'recheck-identity';
const SWITCH_MESSAGE = 'switch-identity';

function notifyIdentityChange(message: string): void {
  try {
    if (typeof BroadcastChannel === 'undefined') return;
    const channel = new BroadcastChannel(AUTH_CHANNEL);
    channel.postMessage(message);
    channel.close();
  } catch { /* Focus and protected requests still consult the server. */ }
}

export function notifyIdentityRecheck(): void { notifyIdentityChange(RECHECK_MESSAGE); }
export function notifyIdentitySwitch(): void { notifyIdentityChange(SWITCH_MESSAGE); }

/**
 * 身份复核发出后、当前身份确认之前不为 null，复核失败也保持。`switching` 表示别的标签页正在登录或退出，
 * 只给本人看的内容一律收起；`rechecking` 是切回标签页等普通复核。换了人会递增代次，页面按新代次重新挂载。
 */
type IdentityPending = 'rechecking' | 'switching';
export const IDENTITY_SWITCHING_NOTICE = '另一个标签页正在切换登录账号，确认后再显示。';
let identityPending: IdentityPending | null = null;
const pendingListeners = new Set<() => void>();

function setIdentityPending(value: IdentityPending | null): void {
  if (identityPending === value) return;
  identityPending = value;
  pendingListeners.forEach((listener) => listener());
}

function subscribeIdentityPending(listener: () => void): () => void {
  pendingListeners.add(listener);
  return () => { pendingListeners.delete(listener); };
}

export function useIdentityPending(): IdentityPending | null {
  return useSyncExternalStore(subscribeIdentityPending, () => identityPending);
}

export function useIdentityUnconfirmed(): boolean {
  return useIdentityPending() !== null;
}

/** 只给本人看、按代次分键的查询；进入待确认时立即取消，已安排的重试不得以别人的 cookie 落地。 */
const PRIVATE_QUERY_PREFIXES = [['memories'], ['conversations'], ['my-skills']] as const;
/** 锁内的身份读取最多等这么久：悬挂的请求不能一直占着锁，挡住别的标签页登录或退出。 */
const LOCKED_READ_TIMEOUT_MS = 8_000;
/** 启动期等锁超时后隔这么久再排队，不在锁外读取。 */
const STARTUP_LOCK_RETRY_MS = 1_000;

function holdPrivateQueries(queryClient: QueryClient, value: IdentityPending): void {
  if (identityPending !== 'switching') setIdentityPending(value);
  for (const queryKey of PRIVATE_QUERY_PREFIXES) void queryClient.cancelQueries({ queryKey });
}

/**
 * 启动确认期间收到换号：丢弃可能读到旧账号的那次启动结果，换号写锁释放后重新确认。
 * 启动页面只显示 BootGate，没有个人内容，所以这里可以结束待确认。
 */
function restartStartupCheck(): void {
  useAuthStore.getState().invalidateIdentity();
  setIdentityPending(null);
}

// Logged out: nothing private is shown; a later login or startup check starts unheld.
useAuthStore.subscribe((state, previous) => {
  if (state.status === 'unauthenticated' && previous.status !== 'unauthenticated') {
    setIdentityPending(null);
  }
});

export function currentIdentityPending(): IdentityPending | null { return identityPending; }

/** 在 `markAuthenticated()` 之后调用：把已确认的身份写进当前代次，并结束待确认。 */
export function seedConfirmedIdentity(queryClient: QueryClient, identity: MeResponse): void {
  queryClient.setQueryData(identityQueryKey(useAuthStore.getState().generation), identity);
  setIdentityPending(null);
}

let recheckNow: (() => void) | null = null;

/** 「重试核对」：按普通复核再问一次后端。 */
export function requestIdentityRecheck(): void { recheckNow?.(); }

export function useIdentityRevalidation(): boolean {
  const queryClient = useQueryClient();
  const generation = useAuthStore((state) => state.generation);
  const status = useAuthStore((state) => state.status);
  const [failed, setFailed] = useState(false);

  useEffect(() => { setFailed(false); }, [generation]);

  useEffect(() => {
    let live = true;
    let sequence = 0;
    let startupRetry = 0;
    let channel: BroadcastChannel | null = null;
    const recheck = async (cause: IdentityPending) => {
      const start = useAuthStore.getState();
      if (imLogoutInProgress() || start.status === 'unauthenticated') return;
      // A startup check may already hold the old account; only a switch makes that matter.
      if (start.status === 'unknown' && cause !== 'switching') return;
      const requestSequence = ++sequence;
      holdPrivateQueries(queryClient, cause);
      const isCurrent = (generation: number) => live && !imLogoutInProgress() &&
        requestSequence === sequence && useAuthStore.getState().generation === generation;
      let generation = start.generation;
      try {
        // Shared lock: a login or logout holding the cookie lock finishes before this read.
        // Bypass cache. The deadline aborts the request (no late 401 can log this page out)
        // and releases the lock; the hold stays until a later recheck confirms.
        const identity = await withAuthCookieLock(async () => {
          const current = useAuthStore.getState();
          if (!live || requestSequence !== sequence || imLogoutInProgress()) return null;
          if (current.status === 'unknown') {
            restartStartupCheck();
            return null;
          }
          if (current.status !== 'authenticated') return null;
          generation = current.generation;
          return customInstance<MeResponse>({
            url: '/api/v1/me', method: 'GET', signal: AbortSignal.timeout(LOCKED_READ_TIMEOUT_MS),
          });
        }, 'shared');
        if (identity === null || !isCurrent(generation)) return;
        const known = queryClient.getQueryData<MeResponse>(identityQueryKey(generation));
        // The server's account scope survives cookie renewal and includes tenant/user/roles.
        if (known?.restore_scope == null || identity.restore_scope !== known.restore_scope) {
          // Reuse the synchronous unauthenticated cleanup before accepting the new account.
          suspendIMIdentity('unauthenticated');
          beginIMLogin();
          useAuthStore.getState().markAuthenticated();
        }
        // Same account: refresh what pages read (e.g. the browser skill) under this generation.
        seedConfirmedIdentity(queryClient, identity);
        setFailed(false);
      } catch {
        if (!isCurrent(generation)) return;
        // Startup: never read outside the lock while a switch may be under way; queue again.
        if (useAuthStore.getState().status === 'unknown') {
          window.clearTimeout(startupRetry);
          startupRetry = window.setTimeout(() => {
            if (live && requestSequence === sequence && useAuthStore.getState().status === 'unknown') {
              void recheck('switching');
            }
          }, STARTUP_LOCK_RETRY_MS);
        } else setFailed(true);
      }
    };
    let wasHidden = document.visibilityState === 'hidden';
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') wasHidden = true;
      else if (wasHidden) { wasHidden = false; void recheck('rechecking'); }
    };
    const onFocus = () => { wasHidden = false; void recheck('rechecking'); };
    const ownRecheck = () => { void recheck('rechecking'); };
    recheckNow = ownRecheck;
    try {
      if (typeof BroadcastChannel !== 'undefined') {
        channel = new BroadcastChannel(AUTH_CHANNEL);
        channel.onmessage = (event: MessageEvent<unknown>) => {
          if (imLogoutInProgress()) return;
          if (event.data === SWITCH_MESSAGE) void recheck('switching');
          if (event.data === RECHECK_MESSAGE) {
            if (imBridgeBlocked() && useAuthStore.getState().status === 'unauthenticated') {
              beginIMLogin();
              useAuthStore.getState().invalidateIdentity();
            } else { void recheck('rechecking'); }
          }
        };
      }
    } catch { /* Focus revalidation remains available. */ }
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      live = false;
      window.clearTimeout(startupRetry);
      // Nothing can confirm a hold once its only revalidator is gone.
      setIdentityPending(null);
      if (recheckNow === ownRecheck) recheckNow = null;
      channel?.close();
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [queryClient]);

  return status === 'authenticated' && failed;
}
