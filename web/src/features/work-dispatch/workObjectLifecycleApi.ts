import { ApiError } from '../../api/mutator';
import {
  commandWorkObjectLifecycleApiV1WorkObjectsWorkObjectIdLifecycleCommandsPost,
  listWorkObjectLifecycleEventsApiV1WorkObjectsWorkObjectIdLifecycleEventsGet,
} from '../../generated/work-objects/work-objects';
import type {
  CommandWorkObjectLifecycleApiV1WorkObjectsWorkObjectIdLifecycleCommandsPostBody,
  LifecycleEventView, LifecycleView,
} from '../../generated/work-objects/work-objects.schemas';
import { useAuthStore } from '../../stores/authStore';

export type LifecycleCommand = CommandWorkObjectLifecycleApiV1WorkObjectsWorkObjectIdLifecycleCommandsPostBody;
export interface PendingLifecycleCommand {
  body: LifecycleCommand;
  key: string;
  etag: string;
}
const statuses = ['assigned', 'department_pending', 'in_progress', 'completed'];
const operations = ['accept', 'feedback', 'complete'];
const object = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null;
const timestamp = (value: unknown) => typeof value === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$/.test(value) && Number.isFinite(Date.parse(value));
const invalid = () => new ApiError(502, 'lifecycle_response_invalid', '事项响应无效，请重新读取。');

function isView(value: unknown, id: string): value is LifecycleView {
  return object(value) && value.work_object_id === id && statuses.includes(String(value.status)) &&
    Number.isInteger(value.version) && Number(value.version) >= 1 &&
    (value.accepted_at === null || timestamp(value.accepted_at)) &&
    (value.completed_at === null || timestamp(value.completed_at)) &&
    Array.isArray(value.available_commands) && value.available_commands.every((command) => operations.includes(command)) &&
    (value.unavailable_reason === null || typeof value.unavailable_reason === 'string');
}
function isEvent(value: unknown, id: string): value is LifecycleEventView {
  return object(value) && value.work_object_id === id && value.actor_role === 'assignee' &&
    typeof value.event_id === 'string' && /^[0-9a-f-]{36}$/.test(value.event_id) &&
    statuses.includes(String(value.from_status)) && statuses.includes(String(value.to_status)) &&
    operations.includes(String(value.operation)) && Number.isInteger(value.result_version) && Number(value.result_version) >= 2 &&
    timestamp(value.occurred_at) && (value.operation === 'accept' ? value.text === null : typeof value.text === 'string' && value.text.trim().length > 0);
}

export async function readLifecycle(id: string, signal?: AbortSignal) {
  const generation = useAuthStore.getState().generation;
  const response = await fetch(`/api/v1/work-objects/${encodeURIComponent(id)}/lifecycle`, {
    credentials: 'same-origin', cache: 'no-store', signal,
  });
  if (response.status === 401) {
    useAuthStore.getState().markUnauthenticated(generation);
    throw new ApiError(401, 'authentication_required', '请重新登录。');
  }
  const payload: unknown = await response.json();
  if (!response.ok) {
    if (object(payload) && object(payload.detail) && typeof payload.detail.code === 'string') {
      throw new ApiError(response.status, payload.detail.code, '事项读取失败。');
    }
    throw new ApiError(response.status, 'lifecycle_read_failed', '事项读取失败。');
  }
  const etag = response.headers.get('ETag');
  if (!etag || !/^"wolc-[0-9a-f]{64}"$/.test(etag) || !isView(payload, id)) throw invalid();
  return { view: payload, etag };
}

export async function readLifecycleEvents(id: string, afterVersion = 0) {
  const result = await listWorkObjectLifecycleEventsApiV1WorkObjectsWorkObjectIdLifecycleEventsGet(
    encodeURIComponent(id), { after_version: afterVersion, limit: 50 },
  );
  if (!object(result) || !Array.isArray(result.items) || !result.items.every((event) => isEvent(event, id)) ||
    typeof result.has_more !== 'boolean' || (result.has_more
      ? !Number.isInteger(result.next_after_version) || Number(result.next_after_version) <= afterVersion
      : result.next_after_version !== null)) throw invalid();
  return result;
}

export async function sendLifecycleCommand(id: string, command: PendingLifecycleCommand) {
  const result = await commandWorkObjectLifecycleApiV1WorkObjectsWorkObjectIdLifecycleCommandsPost(
    encodeURIComponent(id), command.body, { 'Idempotency-Key': command.key, 'If-Match': command.etag },
  );
  if (!object(result) || !isEvent(result.event, id) || typeof result.replayed !== 'boolean' ||
    result.event.operation !== command.body.operation || result.event.text !== ('text' in command.body ? command.body.text : null)) throw invalid();
  return result;
}

export function lifecycleErrorText(error: unknown): string {
  const messages: Record<string, string> = {
    directory_membership_missing: '当前没有可用的部门成员身份。',
    directory_membership_ambiguous: '部门成员身份不唯一，暂时无法办理。',
    organization_directory_missing: '组织目录尚未就绪。',
    organization_directory_stale: '组织目录已过期，请稍后重新读取。',
    organization_directory_unavailable: '组织目录暂时不可用。',
    work_object_action_forbidden: '当前身份不能执行此操作。',
    work_object_transition_invalid: '事项状态已改变，请重新读取。',
    work_object_not_found: '事项不存在或当前不可见。',
    work_object_version_conflict: '事项已更新，请核对最新状态后再操作。',
    authentication_required: '请重新登录。',
    idempotency_key_reused: '请求标识已用于其他内容，请重新读取。',
    work_object_lifecycle_request_invalid: '请核对办理说明和请求内容。',
  };
  return error instanceof ApiError ? messages[error.code] ?? '办理服务暂时不可用。' : '网络请求失败。';
}
