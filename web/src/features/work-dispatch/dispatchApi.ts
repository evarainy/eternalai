import { customInstance } from '../../api/mutator';
import { listDispatchOptionsApiV1WorkObjectsDispatchOptionsGet } from '../../generated/work-objects/work-objects';
import type { DepartmentDispatchOption, UserDispatchOption, DispatchOptionsResponse, DispatchWorkObjectsRequest, DispatchWorkObjectsResponse } from '../../generated/work-objects/work-objects.schemas';

export type DispatchOption = DepartmentDispatchOption | UserDispatchOption;
export type OptionScope = { kind: 'department' } | { kind: 'user'; department_id: string };
const record = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null;
const id = (v: unknown): v is string => typeof v === 'string' && v.length > 0 && v.length <= 128;

export function validOptions(value: unknown, scope: OptionScope, limit: number): value is DispatchOptionsResponse {
  if (!record(value) || value.kind !== scope.kind || !Array.isArray(value.items) || value.items.length > limit
    || !Number.isInteger(value.snapshot_version) || (value.snapshot_version as number) < 1
    || !Number.isInteger(value.unselectable_count) || (value.unselectable_count as number) < 0
    || (scope.kind === 'department' && value.unselectable_count !== 0)
    || !(value.next_cursor === null || (typeof value.next_cursor === 'string' && value.next_cursor.length > 0 && value.next_cursor.length <= 2048))
    || value.has_more !== (value.next_cursor !== null)) return false;
  return value.items.every((item: unknown) => record(item) && item.kind === scope.kind
    && id(item.department_id) && typeof item.department_display_name === 'string'
    && (scope.kind === 'department' || (item.department_id === scope.department_id
      && id(item.directory_user_id) && typeof item.display_name === 'string')));
}

export async function readOptions(scope: OptionScope, cursor?: string): Promise<DispatchOptionsResponse> {
  const params = { ...scope, limit: 50, ...(cursor === undefined ? {} : { cursor }) };
  const value: unknown = await listDispatchOptionsApiV1WorkObjectsDispatchOptionsGet(params);
  if (!validOptions(value, scope, params.limit)) throw new Error('invalid_dispatch_options');
  return value;
}

export function postDispatch(body: DispatchWorkObjectsRequest, key: string): Promise<unknown> {
  return customInstance({ url: '/api/v1/work-objects/dispatch', method: 'POST',
    headers: { 'Idempotency-Key': key }, data: body });
}

export function validReceipt(value: unknown, targetCount: number): value is DispatchWorkObjectsResponse {
  if (!record(value) || !Array.isArray(value.items) || value.items.length === 0
    || !Number.isInteger(value.created_count) || value.created_count !== targetCount
    || value.items.length !== targetCount || typeof value.replayed !== 'boolean') return false;
  const ids = new Set<string>();
  return value.items.every((item: unknown) => {
    if (!record(item) || typeof item.work_object_id !== 'string' || item.work_object_id.length === 0
      || ids.has(item.work_object_id) || item.state_authority !== 'internal' || item.handling_action !== 'view_only') return false;
    ids.add(item.work_object_id);
    return true;
  });
}

export function optionKey(option: DispatchOption): string {
  return JSON.stringify(option.kind === 'user'
    ? [option.kind, option.department_id, option.directory_user_id] : [option.kind, option.department_id]);
}

export function targetOf(option: DispatchOption): DispatchWorkObjectsRequest['targets'][number] {
  return option.kind === 'user'
    ? { kind: 'user', department_id: option.department_id, directory_user_id: option.directory_user_id }
    : { kind: 'department', department_id: option.department_id };
}

// The backend SEARCH_WHITESPACE contract includes these control separators.
export function trimDispatchText(value: string): string {
  // eslint-disable-next-line no-control-regex
  return value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+$/gu, '');
}
