import { ApiError, customInstance } from '../../api/mutator';
import {
  cancelBackgroundJobApiV1BackgroundJobsJobIdCancelPost,
  getBackgroundJobApiV1BackgroundJobsJobIdGet,
  listBackgroundJobsApiV1BackgroundJobsGet,
} from '../../generated/background-jobs/background-jobs';
import type {
  BackgroundJobAcceptResponse,
  BackgroundJobCreateRequest,
  BackgroundJobResponse,
  HealthResponse,
} from '../../generated/background-jobs/background-jobs.schemas';

export const SELF_CHECK_KIND = 'system.worker_self_check';
const ACTIVE = new Set(['accepted', 'running', 'cancel_requested']);

export function isActive(job: BackgroundJobResponse): boolean {
  return ACTIVE.has(job.status);
}

function submitOnce(body: BackgroundJobCreateRequest, key: string): Promise<BackgroundJobAcceptResponse> {
  return customInstance<BackgroundJobAcceptResponse>({
    url: '/api/v1/background-jobs',
    method: 'POST',
    headers: { 'Idempotency-Key': key },
    data: body,
  });
}

/** One automatic retry with the same key settles an unknown commit outcome. */
export async function submitSelfCheck(holdSeconds: number): Promise<BackgroundJobAcceptResponse> {
  const key = crypto.randomUUID();
  const body = { kind: SELF_CHECK_KIND, params: { hold_seconds: holdSeconds } };
  try {
    return await submitOnce(body, key);
  } catch (error) {
    if (error instanceof ApiError && error.code === 'background_job_accept_outcome_unknown') {
      return submitOnce(body, key);
    }
    throw error;
  }
}

export async function latestSelfCheck(): Promise<BackgroundJobResponse | null> {
  const { items } = await listBackgroundJobsApiV1BackgroundJobsGet({ limit: 10 });
  return items.find((job) => job.kind === SELF_CHECK_KIND) ?? null;
}

export const readJob = getBackgroundJobApiV1BackgroundJobsJobIdGet;

/** /health answers 503 with the same body when a required check fails; keep that body. */
export async function readHealth(): Promise<HealthResponse> {
  const response = await fetch('/api/v1/health', { credentials: 'same-origin' });
  if (response.status !== 200 && response.status !== 503) throw new Error(`health_${response.status}`);
  return (await response.json()) as HealthResponse;
}

export const cancelJob = cancelBackgroundJobApiV1BackgroundJobsJobIdCancelPost;

export const STATUS_TEXT: Record<BackgroundJobResponse['status'], string> = {
  accepted: '已接收，等待后台处理',
  running: '后台处理中',
  cancel_requested: '正在取消',
  succeeded: '已完成',
  failed: '未能完成',
  cancelled: '已取消',
};

export const CHECK_TEXT: Record<string, string> = {
  database: '数据库',
  redis: '缓存服务',
  vllm: '大模型服务',
  organization_directory: '通讯录数据',
  organization_directory_dispatch_policy: '派发部门配置',
  background_worker: '后台服务',
};
