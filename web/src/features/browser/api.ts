import { ApiError, customInstance, RUNTIME_SESSION_BUSY_CODE } from '../../api/mutator';
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

export function isBrowserUnconfirmed(envelope: unknown, clientRequestId: string): boolean {
  if (typeof envelope !== 'object' || envelope === null || !('status' in envelope)
      || envelope.status !== 'running' || !('schema_version' in envelope)
      || envelope.schema_version !== 'phase0.sdui.v1' || !('data' in envelope)) return false;
  const data = exactRecord(envelope.data, [
    'kind', 'client_request_id', 'error_code', 'browser_stage', 'browser_reason',
  ]);
  return data !== null && data.kind === 'unconfirmed' && data.client_request_id === clientRequestId
    && (data.error_code === 'adapter_error' || data.error_code === 'adapter_timeout')
    && data.browser_stage === 'admission'
    && (data.browser_reason === 'admission_timeout' || data.browser_reason === 'admission_uncertain');
}

export interface BrowserRunRead {
  readonly run: ParsedBrowserRunView;
  readonly value: Record<string, unknown> | null;
  readonly writePreview?: Record<string, unknown> | null;
  readonly fillStop?: FillStopReport | null;
  /** Present only on D runs the worker has sealed for the user's own submission (§8.1). */
  readonly handoff?: BrowserHandoff | null;
}

export interface BrowserHandoff {
  readonly schema_version: 'browser.handoff.v1';
  readonly sealed_at: string;
  readonly deadline: string;
  /** The owner's own three values; null when the server withholds them. */
  readonly fields: {
    readonly category: string;
    readonly position: string;
    readonly description: string;
  } | null;
}

/** A sealed run that has not reached a terminal result yet. */
export function isActiveHandoff(read: BrowserRunRead): boolean {
  return read.handoff != null && read.run.result === null;
}

export interface FillStopReport {
  readonly execution_status: 'filled_stopped' | 'failed' | 'cancelled' | 'unknown';
  readonly verification: 'verified' | 'mismatch' | 'incomplete' | 'unknown';
  readonly field_checks: Record<'category' | 'position' | 'description', boolean | null>;
  readonly submit_actions: number;
  readonly seconds: number;
  readonly error_code: string;
  readonly cleanup_confirmed: boolean;
}

const fillStopCodes = new Set([
  'OK', 'CONFIG_FAILED', 'SESSION_BUSY', 'LOGIN_REQUIRED', 'PAGE_CHANGED', 'UNEXPECTED_DIALOG',
  'DOWNLOAD_BLOCKED', 'EGRESS_BLOCKED', 'SOURCE_URL_PARSE', 'SOURCE_URL_CREDENTIALS',
  'SOURCE_URL_SCHEME', 'SOURCE_URL_HOST', 'SOURCE_URL_PORT', 'SOURCE_ORIGIN_MISMATCH',
  'SOURCE_SET_CHANGED', 'CONTROL_MISSING', 'CONTROL_AMBIGUOUS', 'CONTROL_CHANGED',
  'PAGE_STATE_MISMATCH', 'ACTION_TIMEOUT', 'OBSERVE_TIMEOUT', 'CONNECT_TIMEOUT', 'RESULT_MISMATCH',
  'REPORTER_MISSING', 'REPORTER_AMBIGUOUS', 'REPORTER_INVALID', 'CANCELLED', 'CLEANUP_FAILED',
  'REQUEST_GUARD_FAILED', 'PREP_FAILED', 'PAGE_CONTEXT_EVENT', 'PAGE_MAIN_FRAME_NAV',
  'PAGE_CHILD_FRAME_NAV', 'PAGE_TARGET_CLOSED', 'PAGE_CONTEXT_SET_CHANGED', 'PAGE_FRAME_SET_CHANGED',
  'WINDOW_PREP_FAILED', 'WINDOW_LOOKUP_FAILED', 'WINDOW_SET_FAILED', 'WINDOW_VERIFY_FAILED',
  'AUTHORITY_LOST', 'RECEIPT_INVALID', 'EXECUTION_TIMEOUT', 'FORM_SUBMIT_BLOCKED',
]);

function parseFillStop(value: unknown): FillStopReport | null {
  const item = exactRecord(value, ['mode', 'execution_status', 'verification', 'field_checks',
    'actions_attempted', 'actions_completed', 'submit_actions', 'model_requests', 'seconds',
    'error_code', 'cleanup_confirmed']);
  if (item === null || item.mode !== 'FILL_STOP'
      || !['filled_stopped', 'failed', 'cancelled', 'unknown'].includes(String(item.execution_status))
      || !['verified', 'mismatch', 'incomplete', 'unknown'].includes(String(item.verification))
      || typeof item.submit_actions !== 'number' || !Number.isSafeInteger(item.submit_actions)
      || item.submit_actions < 0 || item.model_requests !== 0
      || typeof item.seconds !== 'number' || !Number.isFinite(item.seconds)
      || item.seconds < 0 || item.seconds > 7200 || typeof item.error_code !== 'string'
      || !fillStopCodes.has(item.error_code) || typeof item.cleanup_confirmed !== 'boolean') return null;
  const checks = exactRecord(item.field_checks, ['category', 'position', 'description']);
  if (checks === null || Object.values(checks).some((v) => v !== null && typeof v !== 'boolean')) return null;
  if (typeof item.actions_attempted !== 'number' || !Number.isInteger(item.actions_attempted)
      || item.actions_attempted < 0 || item.actions_attempted > 4
      || typeof item.actions_completed !== 'number' || !Number.isInteger(item.actions_completed)
      || item.actions_completed < 0 || item.actions_completed > item.actions_attempted) return null;
  if (item.execution_status === 'filled_stopped' && (item.submit_actions !== 0
      || item.verification !== 'verified'
      || item.error_code !== 'OK' || !item.cleanup_confirmed || item.actions_completed < 3
      || Object.values(checks).some((v) => v !== true))) return null;
  return { execution_status: item.execution_status as FillStopReport['execution_status'],
    verification: item.verification as FillStopReport['verification'],
    field_checks: checks as FillStopReport['field_checks'], seconds: item.seconds,
    submit_actions: item.submit_actions,
    error_code: item.error_code, cleanup_confirmed: item.cleanup_confirmed };
}

function boundedText(value: unknown, maxLength: number): value is string {
  return typeof value === 'string' && value.length <= maxLength;
}

function parseHandoff(value: unknown): BrowserHandoff | null {
  const item = exactRecord(value, ['schema_version', 'sealed_at', 'deadline', 'fields']);
  if (item === null || item.schema_version !== 'browser.handoff.v1'
      || typeof item.sealed_at !== 'string' || !Number.isFinite(Date.parse(item.sealed_at))
      || typeof item.deadline !== 'string' || !Number.isFinite(Date.parse(item.deadline))) return null;
  if (item.fields === null) {
    return { schema_version: 'browser.handoff.v1', sealed_at: item.sealed_at,
      deadline: item.deadline, fields: null };
  }
  const fields = exactRecord(item.fields, ['category', 'position', 'description']);
  if (fields === null || !boundedText(fields.category, 100) || !boundedText(fields.position, 500)
      || !boundedText(fields.description, 500)) return null;
  return { schema_version: 'browser.handoff.v1', sealed_at: item.sealed_at, deadline: item.deadline,
    fields: { category: fields.category, position: fields.position,
      description: fields.description } };
}

function parseWritePreview(value: unknown): Record<string, unknown> | null {
  const item = exactRecord(value, ['request_id', 'action_digest', 'request_digest', 'skill_id',
    'skill_version', 'publication_digest', 'site_id', 'object_type', 'business_key_digest',
    'parameter_set_digest', 'binding_revision', 'write_steps_digest', 'expires_at']);
  if (item === null || item.skill_id !== 'd_form_fill_stop' || item.object_type !== 'bound_form') return null;
  for (const key of ['request_id', 'skill_id', 'skill_version', 'site_id', 'object_type']) {
    if (!isSafeBrowserId(item[key])) return null;
  }
  for (const key of ['action_digest', 'request_digest', 'publication_digest', 'business_key_digest',
    'parameter_set_digest', 'write_steps_digest']) {
    if (typeof item[key] !== 'string' || !/^[a-f0-9]{64}$/.test(item[key])) return null;
  }
  if (typeof item.binding_revision !== 'number' || !Number.isSafeInteger(item.binding_revision)
      || item.binding_revision < 1 || typeof item.expires_at !== 'string'
      || !Number.isFinite(Date.parse(item.expires_at))) return null;
  return item;
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

export const SESSION_BUSY_NOTICE = '上一条仍在处理，请稍后再发';

/** The 409 for a send the server refused before any work because the session is busy. */
export function isRuntimeSessionBusy(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409
    && error.code === RUNTIME_SESSION_BUSY_CODE;
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
    const wrapper = exactRecord(value, ['run', 'value'])
      ?? exactRecord(value, ['run', 'value', 'write_preview'])
      ?? exactRecord(value, ['run', 'value', 'write_preview', 'fill_stop'])
      ?? exactRecord(value, ['run', 'value', 'write_preview', 'fill_stop', 'handoff']);
    if (wrapper === null) return null;
    const handoff = wrapper.handoff == null ? null : parseHandoff(wrapper.handoff);
    if (wrapper.handoff != null && handoff === null) return null;
    const writePreview = wrapper.write_preview == null ? null : parseWritePreview(wrapper.write_preview);
    if (wrapper.write_preview != null && writePreview === null) return null;
    const fillStop = wrapper.fill_stop == null ? null : parseFillStop(wrapper.fill_stop);
    if (wrapper.fill_stop != null && fillStop === null) return null;
    const run = parseBrowserRunProjection(wrapper.run);
    if (run === null) return null;
    if (writePreview !== null && (run.progress?.phase !== 'queued' || run.result !== null || run.cancel.requested)) return null;
    if (wrapper.value === null) return { run, value: null, writePreview, fillStop, handoff };
    if (run.status !== 'completed' || run.result?.verification !== 'verified') return null;
    const projected = jsonValue(wrapper.value);
    if (typeof projected !== 'object' || projected === null || Array.isArray(projected)) {
      return null;
    }
    const serialized = JSON.stringify(projected);
    if (new TextEncoder().encode(serialized).length > MAX_RESULT_BYTES) return null;
    return { run, value: projected as Record<string, unknown>, writePreview, fillStop, handoff };
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

export async function submitOrdinaryBrowserCapableMessage(
  message: string, sessionId: string, clientRequestId: string, signal?: AbortSignal,
  collection?: { readonly task_id: string; readonly version: number } | null,
): Promise<unknown> {
  if (!isClientConversationId(sessionId) || !isClientConversationId(clientRequestId)
      || !message.trim()) throw new Error('browser_request_invalid');
  return customInstance<unknown>({
    url: '/api/v1/runtime/handle', method: 'POST', signal,
    data: { channel: 'web', session_id: sessionId, message, client_request_id: clientRequestId,
      client_capabilities: {}, ...(collection == null ? {} : { d_form_collection: collection }) },
  });
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
  const controller = new AbortController();
  const abort = () => { controller.abort(); };
  if (signal?.aborted) controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(abort, 5000);
  try {
    const response = await customInstance<unknown>({
      url: runPath(taskId, runId), method: 'GET', params: { session_id: sessionId }, signal: controller.signal,
    });
    const parsed = parseBrowserRunRead(response);
    if (parsed === null || parsed.run.task_id !== taskId || parsed.run.run_id !== runId) {
      throw new Error('browser_run_response_invalid');
    }
    return parsed;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
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

export async function decideBrowserFillStop(
  taskId: string, runId: string, sessionId: string, decision: 'confirmed' | 'rejected', signal?: AbortSignal,
): Promise<BrowserRunRead> {
  if (!isClientConversationId(sessionId)) throw new Error('browser_session_invalid');
  const response = await customInstance<unknown>({
    url: `${runPath(taskId, runId)}/decision`, method: 'POST', signal,
    data: { session_id: sessionId, decision },
  });
  const parsed = parseBrowserRunRead(response);
  if (parsed === null || parsed.run.task_id !== taskId || parsed.run.run_id !== runId) {
    throw new Error('browser_decision_response_invalid');
  }
  return parsed;
}
