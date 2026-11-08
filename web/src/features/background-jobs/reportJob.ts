import { useAuthStore } from '../../stores/authStore';
import { clearRecovery, loadRecovery, storeRecovery } from './recoveryStorage';
import type { BackgroundJobResponse } from '../../generated/background-jobs/background-jobs.schemas';

export const REPORT_KIND = 'report.oa_pending_workflows_xlsx';
const STORAGE_KEY = 'eternalai.report-jobs.v1';
const MAX_STORED = 5;
const JOB_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export function isReportJobId(value: unknown): value is string {
  return typeof value === 'string' && JOB_ID.test(value);
}

/** Same-tab recovery list: job ids only, bound to the account scope that accepted them;
 * the server still re-checks ownership on every read. */
export function loadReportJobs(scope: string): string[] {
  return loadRecovery(STORAGE_KEY, scope).filter(isReportJobId).slice(-MAX_STORED);
}

export function rememberReportJob(scope: string | null, jobId: string): void {
  if (scope === null || !isReportJobId(jobId)
      || useAuthStore.getState().status !== 'authenticated') return;
  storeRecovery(STORAGE_KEY, scope,
    [...loadReportJobs(scope).filter((item) => item !== jobId), jobId].slice(-MAX_STORED));
}

export function forgetReportJob(scope: string | null, jobId: string): void {
  if (scope === null) return;
  storeRecovery(STORAGE_KEY, scope, loadReportJobs(scope).filter((item) => item !== jobId));
}

export interface ReportResult {
  readonly artifactId: string;
  readonly rowCount: number;
}

/** Only the whitelisted summary of a succeeded report job; anything else is not shown. */
export function reportResult(job: BackgroundJobResponse): ReportResult | null {
  const summary = job.result_summary;
  if (job.status !== 'succeeded' || job.result_code !== 'ok' || summary === null
      || typeof summary !== 'object') return null;
  const { artifact_id: artifactId, row_count: rowCount, verified } = summary as Record<string, unknown>;
  if (!isReportJobId(artifactId) || verified !== true || typeof rowCount !== 'number'
      || !Number.isSafeInteger(rowCount) || rowCount < 0) return null;
  return { artifactId, rowCount };
}

export function reportDownloadUrl(artifactId: string): string {
  return `/api/v1/file-artifacts/${encodeURIComponent(artifactId)}/content`;
}

const REAUTHORIZE = '需要先到软件中心重新授权 OA，再重新导出。';
const RETRY_LATER = 'OA 暂不可用，请稍后重新导出。';

export function reportFailureText(code: string | null): string {
  switch (code) {
    case 'oa_session_expired':
      return 'OA 登录已过期。' + REAUTHORIZE;
    case 'oa_unbound':
      return REAUTHORIZE;
    case 'oa_forbidden':
      return 'OA 拒绝了本次读取，请确认本人 OA 账号可查看待办。';
    case 'oa_unavailable':
    case 'oa_incomplete':
      return RETRY_LATER;
    case 'file_artifacts_unavailable':
      return '文件服务未启用，请联系管理员。';
    default:
      return '报表未能生成，请稍后重新导出。';
  }
}

// Authentication changes drop the recovery list synchronously (same rule as browser runs).
useAuthStore.subscribe((state, previous) => {
  if (state.status === 'unauthenticated'
      || (previous.status === 'authenticated'
        && (state.status !== 'authenticated' || state.generation !== previous.generation))) {
    clearRecovery(STORAGE_KEY);
  }
});
