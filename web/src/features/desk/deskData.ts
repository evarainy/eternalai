import { useQuery } from '@tanstack/react-query';
import { listWorkObjectsApiV1WorkObjectsGet as listWorkObjects } from '../../generated/work-objects/work-objects';
import { useAuthStore } from '../../stores/authStore';

/**
 * 案头卡片的数据。每张卡片各取各的：一个来源不通只影响用它的那几张，别的照常。
 * 先显示上次的数据再刷新（同一个 key 的缓存一分钟内直接复用）。
 * 各种小结的算法在 `shared/work/workSummaries.ts`，这里只管取数。
 */
export * from '../../shared/work/workSummaries';

const STALE_TIME_MS = 60_000;

function useDeskQuery<T>(name: string, queryFn: () => Promise<T>) {
  const generation = useAuthStore((state) => state.generation);
  return useQuery({
    queryKey: ['desk', generation, name] as const,
    queryFn,
    staleTime: STALE_TIME_MS,
  });
}

/** 没办完的事：OA 待办和内部工作事项。 */
export function useOpenWork() {
  return useDeskQuery('open-work', () => listWorkObjects({ oa_view: 'active', completion: 'active' }));
}

/** 近 30 天办结的内部事项（OA 已办结数据还没有接入）。 */
export function useCompletedWork() {
  return useDeskQuery('completed-work', () => listWorkObjects({ oa_view: 'active', completion: 'completed' }));
}

/** 我交办出去、还在办的事。 */
export function useSentWork() {
  return useDeskQuery('sent-work', () => listWorkObjects({ queue: 'mine_sent', completion: 'active' }));
}
