import { ApiError, customInstance } from '../../api/mutator';
import { useAuthStore } from '../../stores/authStore';
import type {
  BackgroundJobAcceptResponse,
  BackgroundJobResponse,
} from '../../generated/background-jobs/background-jobs.schemas';
import {
  parseSheetFillPlan,
  type SheetFillField,
  type SheetFillPlan,
  type SheetFillSource,
} from '../../contracts/sheetFillPlan';
import { isReportJobId } from './reportJob';
import { clearRecovery, loadRecovery, storeRecovery } from './recoveryStorage';

export const SHEET_FILL_KIND = 'sheet.fill_xlsx';
const STORAGE_KEY = 'eternalai.sheet-fill-submissions.v1';
const MAX_STORED = 5;
const UNKNOWN_OUTCOME = 'background_job_accept_outcome_unknown';

export const SOURCE_LABELS: Record<SheetFillSource, { source: string; key: string }> = {
  oa_pending: { source: '本人 OA 待办', key: '待办标题' },
  document: { source: '固定文档问答', key: '问题' },
};

export const FIELD_LABELS: Record<SheetFillField, string> = {
  status: '状态',
  received_at: '接收时间',
  created_at: '创建时间',
  workflow_type_id: '流程类型编号',
  todo_id: '待办编号',
  quote: '文档原文',
};

/** A confirmed plan whose acceptance has not been seen yet (same-tab recovery). */
export interface PendingSheetFill {
  readonly responseId: string;
  readonly sessionId: string;
  readonly idempotencyKey: string;
  readonly plan: SheetFillPlan;
}

function parsePending(value: unknown): PendingSheetFill | null {
  if (typeof value !== 'object' || value === null) return null;
  const { responseId, sessionId, idempotencyKey, plan } = value as Record<string, unknown>;
  const parsedPlan = parseSheetFillPlan(plan);
  if (typeof responseId !== 'string' || !responseId || typeof sessionId !== 'string'
      || !sessionId || !isReportJobId(idempotencyKey) || parsedPlan === null) return null;
  return { responseId, sessionId, idempotencyKey, plan: parsedPlan };
}

/** Only records written under the given account scope (see recoveryStorage). */
export function loadPendingSheetFills(scope: string): PendingSheetFill[] {
  return loadRecovery(STORAGE_KEY, scope).map(parsePending)
    .filter((item): item is PendingSheetFill => item !== null).slice(-MAX_STORED);
}

export function rememberPendingSheetFill(scope: string | null, item: PendingSheetFill): void {
  if (scope === null || useAuthStore.getState().status !== 'authenticated') return;
  storeRecovery(STORAGE_KEY, scope, [
    ...loadPendingSheetFills(scope).filter((entry) => entry.responseId !== item.responseId), item,
  ].slice(-MAX_STORED));
}

export function forgetPendingSheetFill(scope: string | null, responseId: string): void {
  if (scope === null) return;
  storeRecovery(STORAGE_KEY, scope,
    loadPendingSheetFills(scope).filter((entry) => entry.responseId !== responseId));
}

function submitOnce(item: PendingSheetFill): Promise<BackgroundJobAcceptResponse> {
  return customInstance<BackgroundJobAcceptResponse>({
    url: '/api/v1/background-jobs',
    method: 'POST',
    headers: { 'Idempotency-Key': item.idempotencyKey },
    data: { kind: SHEET_FILL_KIND, params: item.plan, session_id: item.sessionId },
  });
}

export type SheetFillSubmitOutcome =
  | { readonly kind: 'accepted'; readonly jobId: string }
  | { readonly kind: 'refused'; readonly code: string }
  | { readonly kind: 'unconfirmed' };

/**
 * Same key, plan and session on every attempt: the server deduplicates. One automatic
 * retry settles an unknown commit; network or server failures leave it unconfirmed.
 */
export async function submitSheetFill(item: PendingSheetFill): Promise<SheetFillSubmitOutcome> {
  const generation = useAuthStore.getState().generation;
  for (const attempt of [1, 2]) {
    try {
      const accepted = await submitOnce(item);
      if (accepted.kind !== SHEET_FILL_KIND || !isReportJobId(accepted.job_id)) {
        return { kind: 'unconfirmed' };
      }
      return { kind: 'accepted', jobId: accepted.job_id };
    } catch (error) {
      if (!(error instanceof ApiError)) return { kind: 'unconfirmed' };
      if (error.code === UNKNOWN_OUTCOME && attempt === 1) {
        const auth = useAuthStore.getState();
        if (auth.status !== 'authenticated' || auth.generation !== generation) {
          return { kind: 'refused', code: 'identity_changed' };
        }
        continue;
      }
      if (error.code === 'identity_changed' || error.status === 401) {
        return { kind: 'refused', code: error.code };
      }
      // 5xx after an unknown first outcome or any server failure: never claimed refused.
      if (error.status >= 500 || attempt === 2) return { kind: 'unconfirmed' };
      return { kind: 'refused', code: error.code };
    }
  }
  return { kind: 'unconfirmed' };
}

export function sheetFillRefusalText(code: string): string {
  switch (code) {
    case 'sheet_fill_source_changed':
      return '上传的表格已变化或不属于本次对话，请重新上传后再填表。';
    case 'sheet_fill_document_forbidden':
      return '当前账号不能使用固定文档问答填表。';
    case 'sheet_fill_sheet_size_unknown':
      return '表格的行列数无法确定，请另存后重新上传。';
    case 'sheet_fill_fidelity_unsupported':
      return '表格含图表、图片等对象，首版暂不支持，请另存为仅含数据的表格后重试';
    case 'sheet_fill_too_many_rows':
      return '填写的数据行超过上限（OA 待办每表 2000 行，文档问答合计 30 个问题）。';
    case 'oa_unbound':
      return '需要先在软件中心授权 OA，再重新填表。';
    case 'oa_unavailable':
      return 'OA 暂不可用，请稍后再试。';
    case 'report_session_required':
    case 'session_not_found':
      return '填表需要在原 AI 助手对话中确认，请重新上传后再试。';
    case 'background_job_limit_reached':
      return '进行中的后台任务已达上限，请稍后再试。';
    case 'background_job_idempotency_conflict':
      return '该确认已用于另一项任务，请重新发起填表。';
    case 'identity_changed':
    case 'authentication_required':
      return '登录身份已变化，请重新登录后再填表。';
    default:
      return '填表任务未能提交，请重新发起。';
  }
}

export interface SheetFillResult {
  readonly artifactId: string;
  readonly filled: number;
  readonly skipped: number;
  readonly notFound: number;
  readonly multiple: number;
  readonly docNoEvidence: number;
  readonly docFailed: number;
  readonly tooLong: number;
}

const COUNT_KEYS = [
  'filled_cells', 'skipped_nonempty', 'marked_not_found', 'marked_multiple',
  'marked_doc_no_evidence', 'marked_doc_failed', 'marked_too_long',
] as const;

function isCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

/** Only the whitelisted summary of a succeeded sheet fill job; anything else is not shown. */
export function sheetFillResult(job: BackgroundJobResponse): SheetFillResult | null {
  const summary = job.result_summary;
  if (job.status !== 'succeeded' || job.result_code !== 'ok' || summary === null
      || typeof summary !== 'object') return null;
  const values = summary as Record<string, unknown>;
  if (!isReportJobId(values.artifact_id) || values.verified !== true
      || COUNT_KEYS.some((key) => !isCount(values[key]))) return null;
  const count = (key: (typeof COUNT_KEYS)[number]) => values[key] as number;
  return {
    artifactId: values.artifact_id,
    filled: count('filled_cells'),
    skipped: count('skipped_nonempty'),
    notFound: count('marked_not_found'),
    multiple: count('marked_multiple'),
    docNoEvidence: count('marked_doc_no_evidence'),
    docFailed: count('marked_doc_failed'),
    tooLong: count('marked_too_long'),
  };
}

export function sheetFillResultText(result: SheetFillResult): string {
  const marked = result.notFound + result.multiple + result.docNoEvidence + result.docFailed
    + result.tooLong;
  const parts = [
    ['未找到', result.notFound], ['多条', result.multiple], ['文档无依据', result.docNoEvidence],
    ['问答失败', result.docFailed], ['过长', result.tooLong],
  ].filter(([, value]) => (value as number) > 0).map(([label, value]) => `${label} ${value}`);
  const detail = parts.length > 0 ? `（${parts.join('、')}）` : '';
  const skipped = result.skipped > 0 ? `，已有内容未覆盖 ${result.skipped} 格` : '';
  return `已填写 ${result.filled} 格，标注 ${marked} 格${detail}${skipped}。`;
}

const REAUTHORIZE = '需要先到软件中心重新授权 OA，再重新填表。';

export function sheetFillFailureText(code: string | null): string {
  switch (code) {
    case 'sheet_fill_source_changed':
      return '上传的表格已变化或已过期，请重新上传后再填表。';
    case 'sheet_fill_fidelity_unsupported':
      return '表格含图表、图片等对象，首版暂不支持，请另存为仅含数据的表格后重试';
    case 'sheet_fill_verify_failed':
      return '填写结果核对未通过，未生成文件，请重新填表。';
    case 'sheet_fill_child_failed':
      return '表格读取或保存失败，请另存后重新上传。';
    case 'sheet_fill_document_unavailable':
      return '文档问答暂未在后台启用，请联系管理员。';
    case 'sheet_fill_document_forbidden':
      return '当前账号不能使用固定文档问答填表。';
    case 'oa_session_expired':
      return 'OA 登录已过期。' + REAUTHORIZE;
    case 'oa_unbound':
      return REAUTHORIZE;
    case 'oa_forbidden':
      return 'OA 拒绝了本次读取，请确认本人 OA 账号可查看待办。';
    case 'oa_unavailable':
    case 'oa_incomplete':
      return 'OA 暂不可用，请稍后重新填表。';
    case 'file_artifacts_unavailable':
      return '文件服务未启用，请联系管理员。';
    default:
      return '表格未能填写，请稍后重新填表。';
  }
}

// Authentication changes drop pending submissions synchronously (same rule as report jobs).
useAuthStore.subscribe((state, previous) => {
  if (state.status === 'unauthenticated'
      || (previous.status === 'authenticated'
        && (state.status !== 'authenticated' || state.generation !== previous.generation))) {
    clearRecovery(STORAGE_KEY);
  }
});
