import { customInstance } from '../../api/mutator';
import {
  isSafeBrowserId,
  parseBrowserRunProjection,
  type ParsedBrowserRunView,
} from '../../contracts/browserRunProjection';

const MAX_RESULT_BYTES = 1_048_576;
const MAX_JSON_DEPTH = 32;
const clientSessionPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;

export interface BrowserAccepted {
  readonly kind: 'accepted';
  readonly task_id: string;
  readonly run_id: string;
  readonly state_revision: number;
}

export interface BrowserPending {
  readonly kind: 'pending';
  readonly task_id: string;
}

export interface BrowserFailed {
  readonly kind: 'failed';
  readonly error_code: string;
}

export interface BrowserRunRead {
  readonly run: ParsedBrowserRunView;
  readonly value: Record<string, unknown> | null;
}

function exactRecord(value: unknown, keys: readonly string[]): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const prototype: unknown = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return null;
  const actual = Reflect.ownKeys(value);
  if (actual.length !== keys.length || actual.some((key) =>
    typeof key !== 'string' || !keys.includes(key))) return null;
  const result: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const key of keys) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor === undefined || !('value' in descriptor)) return null;
    result[key] = descriptor.value;
  }
  return result;
}

function jsonValue(value: unknown, depth = 0): unknown {
  if (depth > MAX_JSON_DEPTH) throw new Error('browser_result_invalid');
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (Array.isArray(value)) return value.map((item) => jsonValue(item, depth + 1));
  if (typeof value !== 'object') throw new Error('browser_result_invalid');
  const prototype: unknown = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    throw new Error('browser_result_invalid');
  }
  const result: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key !== 'string') throw new Error('browser_result_invalid');
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor === undefined || !('value' in descriptor)) {
      throw new Error('browser_result_invalid');
    }
    result[key] = jsonValue(descriptor.value, depth + 1);
  }
  return result;
}

export function isClientConversationId(value: unknown): value is string {
  return typeof value === 'string' && clientSessionPattern.test(value);
}

/** Admission metadata is inert until an owner-checked GET returns a parsed run. */
export function parseBrowserAccepted(envelope: unknown): BrowserAccepted | null {
  try {
    if (typeof envelope !== 'object' || envelope === null || !('data' in envelope)
        || !('status' in envelope) || envelope.status !== 'running'
        || !('schema_version' in envelope)
        || envelope.schema_version !== 'phase0.sdui.v1') return null;
    const data = exactRecord(envelope.data, ['kind', 'task_id', 'run_id', 'state_revision']);
    if (data === null || data.kind !== 'accepted' || !isSafeBrowserId(data.task_id)
        || !isSafeBrowserId(data.run_id) || typeof data.state_revision !== 'number'
        || !Number.isSafeInteger(data.state_revision) || data.state_revision < 0) return null;
    return {
      kind: 'accepted', task_id: data.task_id, run_id: data.run_id,
      state_revision: data.state_revision,
    };
  } catch {
    return null;
  }
}

export function parseBrowserPending(envelope: unknown): BrowserPending | null {
  try {
    if (typeof envelope !== 'object' || envelope === null || !('data' in envelope)
        || !('status' in envelope) || envelope.status !== 'running'
        || !('schema_version' in envelope)
        || envelope.schema_version !== 'phase0.sdui.v1') return null;
    const data = exactRecord(envelope.data, ['kind', 'task_id']);
    if (data === null || data.kind !== 'pending' || !isSafeBrowserId(data.task_id)) return null;
    return { kind: 'pending', task_id: data.task_id };
  } catch {
    return null;
  }
}

export function parseBrowserFailed(envelope: unknown): BrowserFailed | null {
  try {
    if (typeof envelope !== 'object' || envelope === null || !('data' in envelope)
        || !('status' in envelope) || envelope.status !== 'failed'
        || !('schema_version' in envelope)
        || envelope.schema_version !== 'phase0.sdui.v1') return null;
    const data = exactRecord(envelope.data, ['kind', 'error_code']);
    if (data === null || data.kind !== 'failed' || !isSafeBrowserId(data.error_code)) return null;
    return { kind: 'failed', error_code: data.error_code };
  } catch {
    return null;
  }
}

export function parseBrowserRunRead(value: unknown): BrowserRunRead | null {
  try {
    const wrapper = exactRecord(value, ['run', 'value']);
    if (wrapper === null) return null;
    const run = parseBrowserRunProjection(wrapper.run);
    if (run === null) return null;
    if (wrapper.value === null) return { run, value: null };
    if (run.status !== 'completed' || run.result?.verification !== 'verified') return null;
    const projected = jsonValue(wrapper.value);
    if (typeof projected !== 'object' || projected === null || Array.isArray(projected)) {
      return null;
    }
    const serialized = JSON.stringify(projected);
    if (new TextEncoder().encode(serialized).length > MAX_RESULT_BYTES) return null;
    return { run, value: projected as Record<string, unknown> };
  } catch {
    return null;
  }
}

function runPath(taskId: string, runId: string): string {
  if (!isSafeBrowserId(taskId) || !isSafeBrowserId(runId)) {
    throw new Error('browser_target_invalid');
  }
  return `/api/v1/browser-runs/${taskId}/${runId}`;
}

export async function submitBrowserMessage(
  message: string,
  sessionId: string,
  browserSkillId: string,
  clientRequestId: string,
  signal?: AbortSignal,
): Promise<unknown> {
  if (!isClientConversationId(sessionId) || !isSafeBrowserId(browserSkillId)
      || !isClientConversationId(clientRequestId) || !message.trim()) {
    throw new Error('browser_request_invalid');
  }
  return customInstance<unknown>({
    url: '/api/v1/runtime/handle', method: 'POST', signal,
    data: {
      channel: 'web', session_id: sessionId, message, client_request_id: clientRequestId,
      client_capabilities: { browser_async_v1: true, browser_skill_id: browserSkillId },
    },
  });
}

export async function readBrowserRun(
  taskId: string, runId: string, sessionId: string, signal?: AbortSignal,
): Promise<BrowserRunRead> {
  if (!isClientConversationId(sessionId)) throw new Error('browser_session_invalid');
  const response = await customInstance<unknown>({
    url: runPath(taskId, runId), method: 'GET', params: { session_id: sessionId }, signal,
  });
  const parsed = parseBrowserRunRead(response);
  if (parsed === null || parsed.run.task_id !== taskId || parsed.run.run_id !== runId) {
    throw new Error('browser_run_response_invalid');
  }
  return parsed;
}

export async function cancelBrowserRun(
  taskId: string, runId: string, sessionId: string, signal?: AbortSignal,
): Promise<BrowserRunRead> {
  if (!isClientConversationId(sessionId)) throw new Error('browser_session_invalid');
  const response = await customInstance<unknown>({
    url: `${runPath(taskId, runId)}/cancel`, method: 'POST', signal,
    data: { session_id: sessionId },
  });
  const parsed = parseBrowserRunRead(response);
  if (parsed === null || parsed.run.task_id !== taskId || parsed.run.run_id !== runId
      || !parsed.run.cancel.requested || parsed.run.cancel.acknowledged
      || parsed.value !== null) {
    throw new Error('browser_cancel_response_invalid');
  }
  return parsed;
}
