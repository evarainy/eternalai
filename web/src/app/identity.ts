import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { readMeApiV1MeGet } from '../generated/me/me';
import type { MeResponse } from '../generated/me/me.schemas';
import { useAuthStore } from '../stores/authStore';

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
export const IDENTITY_STALE_TIME_MS = 5 * 60 * 1000;

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
    enabled: status !== 'unauthenticated',
    staleTime: IDENTITY_STALE_TIME_MS,
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
  const status = useAuthStore((state) => state.status);
  const generation = useAuthStore((state) => state.generation);
  const { data } = useIdentityQuery();

  useEffect(() => {
    // 只把 `unknown` 收敛掉。若已经明确退出登录，一个还在路上的启动确认**不得**把人重新标成已登录。
    if (data !== undefined && status === 'unknown' &&
        useAuthStore.getState().status === 'unknown' &&
        useAuthStore.getState().generation === generation) {
      useAuthStore.getState().markAuthenticated();
    }
  }, [data, status, generation]);
}

const AUTH_CHANNEL = 'eternalai-auth';
const RECHECK_MESSAGE = 'recheck-identity';

export function notifyIdentityRecheck(): void {
  try {
    if (typeof BroadcastChannel === 'undefined') return;
    const channel = new BroadcastChannel(AUTH_CHANNEL);
    channel.postMessage(RECHECK_MESSAGE);
    channel.close();
  } catch { /* Focus and protected requests still consult the server. */ }
}

export function useIdentityRevalidation(): boolean {
  const generation = useAuthStore((state) => state.generation);
  const status = useAuthStore((state) => state.status);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    let sequence = 0;
    let channel: BroadcastChannel | null = null;
    setFailed(false);
    const recheck = async () => {
      const current = useAuthStore.getState();
      if (current.status !== 'authenticated' || current.generation !== generation) return;
      const requestSequence = ++sequence;
      const isCurrent = () => live && requestSequence === sequence &&
        useAuthStore.getState().generation === generation;
      try {
        // Bypass cache. The generated client cannot abort this HTTP request.
        await readMeApiV1MeGet();
        if (isCurrent()) setFailed(false);
      } catch {
        if (isCurrent()) setFailed(true);
      }
    };
    const onFocus = () => { void recheck(); };
    try {
      if (typeof BroadcastChannel !== 'undefined') {
        channel = new BroadcastChannel(AUTH_CHANNEL);
        channel.onmessage = (event: MessageEvent<unknown>) => {
          if (event.data === RECHECK_MESSAGE) void recheck();
        };
      }
    } catch { /* Focus revalidation remains available. */ }
    window.addEventListener('focus', onFocus);
    return () => {
      live = false;
      channel?.close();
      window.removeEventListener('focus', onFocus);
    };
  }, [generation]);

  return status === 'authenticated' && failed;
}
