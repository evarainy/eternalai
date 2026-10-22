import { ApiError } from '../../api/mutator';
import { requestWorkDispatch } from '../../api/workDispatchTransport';
import type { DispatchTarget, CompletionMode, DispatchMode } from './dispatchApi';
import type { AttachmentView } from './AttachmentsPanel';
import type { WorkObjectListResponse } from '../../generated/work-objects/work-objects.schemas';

export type LifecycleStatus = 'assigned' | 'department_pending' | 'in_progress' | 'awaiting_department_review' | 'awaiting_acceptance' | 'completed' | 'withdrawn';
export type RelationStatus = 'department_pending' | 'assigned' | 'in_progress' | 'awaiting_department_review' | 'submitted';
export type LifecycleOperation = LifecycleCommand['operation'];
export type LifecycleCommand =
  | { operation: 'assign'; handling_relation_id: string; directory_user_id: string }
  | { operation: 'accept'; handling_relation_id: string }
  | { operation: 'feedback'; handling_relation_id: string; text: string }
  | { operation: 'submit'; handling_relation_id: string; text: string; attachment_ids: string[] }
  | { operation: 'complete'; text: string }
  | { operation: 'department_review'; handling_relation_id: string; approved: boolean; reason: string }
  | { operation: 'submit_summary'; text: string; attachment_ids: string[] }
  | { operation: 'confirm' }
  | { operation: 'return'; reason: string }
  | { operation: 'revise'; title: string; requirement: string; receipt_requirement: string; completion_mode: CompletionMode }
  | { operation: 'withdraw'; reason: string }
  | { operation: 'extend'; due_at: string }
  | { operation: 'reassign'; targets: DispatchTarget[]; lead_target: DispatchTarget | null };
export interface PendingLifecycleCommand { body: LifecycleCommand; key: string; etag: string }
export interface HandlingRelationView {
  relation_id: string; target: DispatchTarget; role: 'single' | 'lead' | 'collaborator'; status: RelationStatus;
  assignee_directory_user_id: string | null; accepted_at: string | null; submitted_at: string | null;
  submitted_result: string | null; reviewed_at: string | null; review_result: boolean | null; review_reason: string | null;
  available_commands: string[];
}
export interface LifecycleView {
  title: string; requirement: string; receipt_requirement: string; due_at: string | null;
  work_object_id: string; status: LifecycleStatus; version: number; accepted_at: string | null; completed_at: string | null;
  dispatch_mode: DispatchMode | null; completion_mode: CompletionMode | null; frozen: boolean;
  relations: HandlingRelationView[]; available_commands: string[]; unavailable_reason: string | null;
}
export interface LifecycleEventView {
  actor_ai_user_id: string;
  event_id: string; work_object_id: string; operation: LifecycleOperation; from_status: LifecycleStatus; to_status: LifecycleStatus;
  result_version: number; occurred_at: string; text: string | null; handling_relation_id: string | null;
  relation_from_status: RelationStatus | null; relation_to_status: RelationStatus | null;
  payload: Record<string, unknown> | null;
}
export interface LifecycleEventsPage { items: LifecycleEventView[]; next_after_version: number | null }
export interface RemarkView { work_object_id: string; text: string; version: number; updated_by: string | null; updated_at: string | null; editable: boolean }
export interface RemarkHistoryEntry { work_object_id: string; remark_version: number; text: string; actor_ai_user_id: string; occurred_at: string }
export interface RemarkHistoryPage { items: RemarkHistoryEntry[]; next_cursor: string | null }
export interface PendingRemark { text: string; key: string; etag: string }
const path = (id: string) => `/api/v1/work-objects/${encodeURIComponent(id)}`;
const needEtag = (etag: string | null) => {
  if (!etag) throw new ApiError(502, 'work_object_response_invalid', '事项响应缺少版本，请重新读取。');
  return etag;
};
export async function readLifecycle(id: string, signal?: AbortSignal) {
  const result = await requestWorkDispatch<LifecycleView>({ url: `${path(id)}/lifecycle`, method: 'GET', signal });
  return { view: result.data, etag: needEtag(result.etag) };
}
export async function readLifecycleEvents(id: string, afterVersion = 0) {
  return (await requestWorkDispatch<LifecycleEventsPage>({ url: `${path(id)}/lifecycle/events`, method: 'GET', params: { after_version: afterVersion, limit: 50 } })).data;
}
export async function sendLifecycleCommand(id: string, command: PendingLifecycleCommand) {
  return (await requestWorkDispatch<{ event: LifecycleEventView; replayed: boolean }>({ url: `${path(id)}/lifecycle/commands`, method: 'POST',
    headers: { 'Idempotency-Key': command.key, 'If-Match': command.etag }, data: command.body })).data;
}
export async function readAttachments(id: string) {
  return (await requestWorkDispatch<AttachmentView[]>({ url: `${path(id)}/attachments`, method: 'GET' })).data;
}
export async function readRemark(id: string) {
  const response = await requestWorkDispatch<RemarkView>({ url: `${path(id)}/remarks`, method: 'GET' });
  return { view: response.data, etag: needEtag(response.etag) };
}
export async function editRemark(id: string, request: PendingRemark) {
  const response = await requestWorkDispatch<{ remark: RemarkView; replayed: boolean }>({ url: `${path(id)}/remarks`, method: 'PATCH',
    headers: { 'If-Match': request.etag, 'Idempotency-Key': request.key }, data: { text: request.text } });
  return { ...response.data, etag: needEtag(response.etag) };
}
export async function readRemarkHistory(id: string, cursor?: string) {
  return (await requestWorkDispatch<RemarkHistoryPage>({ url: `${path(id)}/remarks/history`, method: 'GET', params: { ...(cursor ? { cursor } : {}), limit: 50 } })).data;
}
export type WorkQueue = 'mine_sent' | 'department_unassigned' | 'department_review' | 'awaiting_acceptance';
export type WorkQueuePage = WorkObjectListResponse & { next_cursor?: string | null };
export async function readWorkQueue(params: { queue?: WorkQueue; completion?: 'active' | 'completed' | 'withdrawn'; history_from?: string; history_to?: string; cursor?: string; q?: string; oa_view?: 'all' }) {
  return (await requestWorkDispatch<WorkQueuePage>({ url: '/api/v1/work-objects', method: 'GET', params })).data;
}
export function lifecycleErrorText(error: unknown): string {
  if (!(error instanceof ApiError)) return '请求未确认成功，请主动重试原请求或重新读取核对。';
  const messages: Record<string, string> = {
    directory_membership_missing: '当前没有可用的部门成员身份。',
    directory_membership_ambiguous: '部门成员身份不唯一，暂时无法办理。',
    organization_directory_missing: '组织目录尚未就绪。', organization_directory_stale: '组织目录已过期，请重新读取。',
    organization_directory_unavailable: '组织目录暂时不可用。', work_object_action_forbidden: '当前身份不能执行此操作。',
    work_object_transition_invalid: '事项状态已改变，请重新读取。', work_object_not_found: '事项不存在或当前不可见。',
    work_object_version_conflict: '事项已更新，请核对最新状态后再操作。', authentication_required: '请重新登录。',
    idempotency_key_reused: '请求标识已用于其他内容，请核对原结果。',
    work_object_legacy_conversion_blocked: '旧事项缺少可验证的承办身份，暂不能转换办理，请联系维护人员。',
    work_object_lifecycle_request_invalid: '请核对办理说明、目标和请求内容。',
    dispatch_department_head_missing: '目标部门没有唯一可解析负责人，本次未创建。',
    dispatch_department_head_ambiguous: '目标部门负责人不唯一，本次未创建。',
  };
  return messages[error.code] ?? (error.status === 412 ? '内容已更新，请重新读取后操作。' : error.status === 422 ? '请核对输入内容。' : error.status === 403 || error.status === 404 ? '当前事项不可访问，已清除原展示。' : '服务暂时无法确认结果，请重试原请求或重新读取。');
}
