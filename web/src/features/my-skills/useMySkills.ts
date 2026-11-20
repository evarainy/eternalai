/** 本人功能列表：软件中心、聊天面板和 `$` 候选共用同一份缓存（按登录世代隔离）。 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { listSkills } from '../../api/userSkills';
import { useIdentityPending } from '../../app/identity';
import { useAuthStore } from '../../stores/authStore';

export function useMySkills(enabled = true) {
  const generation = useAuthStore((state) => state.generation);
  // Paused while the login is being confirmed (revalidation cancels what is in flight).
  const pending = useIdentityPending();
  return useQuery({
    queryKey: ['my-skills', generation],
    queryFn: listSkills,
    retry: false,
    enabled: enabled && pending === null,
  });
}

/** 保存、改名、删除、启停，或运行发现功能已变化后调用，让各处重新读取。 */
export function useRefreshMySkills(): () => void {
  const generation = useAuthStore((state) => state.generation);
  const queryClient = useQueryClient();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['my-skills', generation] });
  }, [queryClient, generation]);
}
