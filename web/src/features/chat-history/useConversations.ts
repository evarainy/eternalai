/** 历史对话列表的刷新入口（按登录世代隔离缓存）。 */

import { useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { useAuthStore } from '../../stores/authStore';

/** A reply landed or a conversation changed: the list reads again under the current login. */
export function useRefreshConversations(): () => void {
  const generation = useAuthStore((state) => state.generation);
  const queryClient = useQueryClient();
  return useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['conversations', generation] });
  }, [queryClient, generation]);
}
