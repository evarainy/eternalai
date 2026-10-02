/** Unwired browser.run.v1 view. Parsing and local ordering never authorize a read. */

const MAX_SAFE_REVISION = Number.MAX_SAFE_INTEGER;
const MAX_WIRE_BYTES = 65_536;
const MAX_ARTIFACTS = 64;
const MAX_PARAMETERS = 64;
const opaqueIdPattern = /^[A-Za-z0-9_-]{1,96}$/;
const digestPattern = /^[a-f0-9]{64}$/;
const awareTimePattern = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/;

const phases = ['queued', 'acquiring', 'running', 'verifying', 'waiting_user'] as const;
const statuses = ['running', 'waiting_user', 'completed', 'failed', 'cancelled'] as const;
const businessStates = ['completed', 'failed', 'cancelled'] as const;
const effects = ['not_sent', 'acknowledged', 'unknown'] as const;
const verifications = ['verified', 'mismatch', 'incomplete', 'unsupported'] as const;
const cleanups = ['pending', 'released', 'terminated', 'quarantined', 'failed'] as const;
const errorCodes = [
  'browser_async_required',
  'browser_request_id_required',
  'request_key_conflict',
  'browser_target_stale',
  'browser_effect_unknown',
  'browser_resource_quarantined',
  'browser_decision_unavailable',
  'browser_decision_overloaded',
  'browser_decision_timeout',
  'browser_decision_cancelled',
  'browser_decision_invalid_response',
  'browser_decision_model_mismatch',
  'browser_decision_input_unsupported',
  'browser_verification_failed',
  'browser_not_sent',
  'browser_cancelled',
] as const;
const dispatchFailureCodes = [
  'unavailable', 'overloaded', 'invalid_request', 'resource_not_found',
  'unsupported', 'denied', 'stale', 'subject_mismatch', 'invalid_response',
  'timeout', 'cancelled', 'effect_unknown', 'quarantined',
] as const;
const artifactKinds = ['result', 'report', 'download', 'draft'] as const;
const mediaTypes = [
  'application/json', 'application/pdf', 'text/plain', 'text/csv', 'image/png',
] as const;
const availabilities = ['available', 'expired', 'unavailable'] as const;
const draftStates = ['draft', 'validated', 'rejected'] as const;
const rejections = ['invalid_pattern', 'unsafe_reference', 'dependency_mismatch'] as const;

export interface BrowserProgressView {
  readonly phase: typeof phases[number];
  readonly completed_steps: number | null;
  readonly total_steps: number | null;
}

export interface BrowserCancelView {
  readonly requested: boolean;
  readonly acknowledged: boolean;
}

export interface BrowserResultView {
  readonly business: typeof businessStates[number];
  readonly effect: typeof effects[number];
  readonly verification: typeof verifications[number] | null;
  readonly cleanup: typeof cleanups[number];
  readonly error_code: typeof errorCodes[number] | null;
  readonly dispatch_failure_code: typeof dispatchFailureCodes[number] | null;
  readonly terminal_revision: number;
  readonly automatic_replay: false;
}

export interface BrowserArtifactView {
  readonly artifact_id: string;
  readonly kind: typeof artifactKinds[number];
  readonly media_type: typeof mediaTypes[number];
  readonly size_bytes: number;
  readonly expires_at: string;
  readonly availability: typeof availabilities[number];
}

export interface BrowserDraftView {
  readonly draft_id: string;
  readonly draft_revision: number;
  readonly base_revision: number;
  readonly base_publication_digest: string;
  readonly draft_digest: string;
  readonly state: typeof draftStates[number];
  readonly rejection: typeof rejections[number] | null;
  readonly parameter_names: readonly string[];
  readonly executable: false;
}

export interface BrowserRunView {
  readonly schema_version: 'browser.run.v1';
  readonly task_id: string;
  readonly run_id: string;
  readonly state_revision: number;
  readonly status: typeof statuses[number];
  readonly progress: BrowserProgressView | null;
  readonly cancel: BrowserCancelView;
  readonly result: BrowserResultView | null;
  readonly artifacts: readonly BrowserArtifactView[];
  readonly draft: BrowserDraftView | null;
}

declare const parsedBrowserRunBrand: unique symbol;
export type ParsedBrowserRunView = BrowserRunView & {
  readonly [parsedBrowserRunBrand]: true;
};

const parsedViews = new WeakSet<object>();

function enumValue<const T extends readonly string[]>(
  value: unknown,
  choices: T,
): value is T[number] {
  return typeof value === 'string' && choices.some((choice) => choice === value);
}

function revision(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value)
    && value >= 0 && value <= MAX_SAFE_REVISION;
}

export function isSafeBrowserId(value: unknown): value is string {
  return typeof value === 'string' && opaqueIdPattern.test(value);
}

function digest(value: unknown): value is string {
  return typeof value === 'string' && digestPattern.test(value);
}

/** Copy only own data properties from an exact, ordinary JSON object. */
function closedRecord(value: unknown, keys: readonly string[]): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const prototype: unknown = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return null;
  const actual = Reflect.ownKeys(value);
  if (actual.length !== keys.length || actual.some((key) =>
    typeof key !== 'string' || !keys.includes(key))) return null;
  const copy: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const key of keys) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor === undefined || !('value' in descriptor)) return null;
    copy[key] = descriptor.value;
  }
  return copy;
}

function awareExpiry(value: unknown): value is string {
  if (typeof value !== 'string' || value.length > 64) return false;
  const match = awareTimePattern.exec(value);
  if (match === null) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, zone] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  if (year < 1 || month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) {
    return false;
  }
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (day < 1 || day > (days[month - 1] ?? 0)) return false;
  if (zone !== 'Z') {
    const offsetHours = Number(zone?.slice(1, 3));
    const offsetMinutes = Number(zone?.slice(4, 6));
    if (offsetHours > 23 || offsetMinutes > 59) return false;
  }
  return Number.isFinite(Date.parse(value));
}

function parseProgress(value: unknown): BrowserProgressView | null {
  const record = closedRecord(value, ['phase', 'completed_steps', 'total_steps']);
  if (record === null || !enumValue(record.phase, phases)) return null;
  const completed = record.completed_steps;
  const total = record.total_steps;
  if ((completed === null) !== (total === null)) return null;
  if (completed === null) {
    return { phase: record.phase, completed_steps: null, total_steps: null };
  }
  if (!revision(completed) || !revision(total) || completed > total) return null;
  return { phase: record.phase, completed_steps: completed, total_steps: total };
}

function parseCancel(value: unknown): BrowserCancelView | null {
  const record = closedRecord(value, ['requested', 'acknowledged']);
  if (record === null || typeof record.requested !== 'boolean'
      || typeof record.acknowledged !== 'boolean'
      || (record.acknowledged && !record.requested)) return null;
  return { requested: record.requested, acknowledged: record.acknowledged };
}

function parseResult(value: unknown): BrowserResultView | null {
  const record = closedRecord(value, [
    'business', 'effect', 'verification', 'cleanup', 'error_code',
    'dispatch_failure_code', 'terminal_revision', 'automatic_replay',
  ]);
  if (record === null || !enumValue(record.business, businessStates)
      || !enumValue(record.effect, effects)
      || (record.verification !== null && !enumValue(record.verification, verifications))
      || !enumValue(record.cleanup, cleanups)
      || (record.error_code !== null && !enumValue(record.error_code, errorCodes))
      || (record.dispatch_failure_code !== null
        && !enumValue(record.dispatch_failure_code, dispatchFailureCodes))
      || !revision(record.terminal_revision) || record.automatic_replay !== false) return null;
  const result: BrowserResultView = {
    business: record.business,
    effect: record.effect,
    verification: record.verification,
    cleanup: record.cleanup,
    error_code: record.error_code,
    dispatch_failure_code: record.dispatch_failure_code,
    terminal_revision: record.terminal_revision,
    automatic_replay: false,
  };
  if ((result.business === 'completed') !== (result.verification === 'verified')
      || (result.business === 'completed' && result.error_code !== null)
      || (result.business === 'failed' && result.error_code === null)
      || (result.business === 'cancelled' && result.error_code !== 'browser_cancelled')
      || (result.effect === 'unknown' && result.business !== 'completed'
        && (result.business !== 'failed'
          || result.error_code !== 'browser_effect_unknown'))) return null;
  return result;
}

function parseArtifact(value: unknown): BrowserArtifactView | null {
  const record = closedRecord(value, [
    'artifact_id', 'kind', 'media_type', 'size_bytes', 'expires_at', 'availability',
  ]);
  if (record === null || !isSafeBrowserId(record.artifact_id)
      || !enumValue(record.kind, artifactKinds)
      || !enumValue(record.media_type, mediaTypes)
      || !revision(record.size_bytes) || !awareExpiry(record.expires_at)
      || !enumValue(record.availability, availabilities)) return null;
  return {
    artifact_id: record.artifact_id,
    kind: record.kind,
    media_type: record.media_type,
    size_bytes: record.size_bytes,
    expires_at: record.expires_at,
    availability: record.availability,
  };
}

function parseDraft(value: unknown): BrowserDraftView | null {
  const record = closedRecord(value, [
    'draft_id', 'draft_revision', 'base_revision', 'base_publication_digest',
    'draft_digest', 'state', 'rejection', 'parameter_names', 'executable',
  ]);
  if (record === null || !isSafeBrowserId(record.draft_id)
      || !revision(record.draft_revision) || record.draft_revision < 1
      || !revision(record.base_revision) || !digest(record.base_publication_digest)
      || !digest(record.draft_digest) || !enumValue(record.state, draftStates)
      || (record.rejection !== null && !enumValue(record.rejection, rejections))
      || !Array.isArray(record.parameter_names)
      || record.parameter_names.length > MAX_PARAMETERS
      || !record.parameter_names.every(isSafeBrowserId)
      || new Set(record.parameter_names).size !== record.parameter_names.length
      || record.executable !== false) return null;
  if ((record.state === 'rejected') !== (record.rejection !== null)) return null;
  return {
    draft_id: record.draft_id,
    draft_revision: record.draft_revision,
    base_revision: record.base_revision,
    base_publication_digest: record.base_publication_digest,
    draft_digest: record.draft_digest,
    state: record.state,
    rejection: record.rejection,
    parameter_names: [...record.parameter_names],
    executable: false,
  };
}

function frozenView(view: BrowserRunView): ParsedBrowserRunView {
  if (view.progress !== null) Object.freeze(view.progress);
  Object.freeze(view.cancel);
  if (view.result !== null) Object.freeze(view.result);
  for (const artifact of view.artifacts) Object.freeze(artifact);
  Object.freeze(view.artifacts);
  if (view.draft !== null) {
    Object.freeze(view.draft.parameter_names);
    Object.freeze(view.draft);
  }
  Object.freeze(view);
  parsedViews.add(view);
  return view as ParsedBrowserRunView;
}

/** Reject incompatible input without echoing any rejected field or value. */
export function parseBrowserRunProjection(input: unknown): ParsedBrowserRunView | null {
  try {
    const record = closedRecord(input, [
      'schema_version', 'task_id', 'run_id', 'state_revision', 'status',
      'progress', 'cancel', 'result', 'artifacts', 'draft',
    ]);
    if (record === null || record.schema_version !== 'browser.run.v1'
        || !isSafeBrowserId(record.task_id) || !isSafeBrowserId(record.run_id)
        || !revision(record.state_revision) || !enumValue(record.status, statuses)
        || !Array.isArray(record.artifacts)
        || record.artifacts.length > MAX_ARTIFACTS) return null;
    const progress = record.progress === null ? null : parseProgress(record.progress);
    const cancel = parseCancel(record.cancel);
    const result = record.result === null ? null : parseResult(record.result);
    const draft = record.draft === null ? null : parseDraft(record.draft);
    if ((record.progress !== null && progress === null) || cancel === null
        || (record.result !== null && result === null)
        || (record.draft !== null && draft === null)) return null;
    const artifacts: BrowserArtifactView[] = [];
    for (const raw of record.artifacts) {
      const artifact = parseArtifact(raw);
      if (artifact === null) return null;
      artifacts.push(artifact);
    }
    if (new Set(artifacts.map((item) => item.artifact_id)).size !== artifacts.length) {
      return null;
    }
    const terminal = ['completed', 'failed', 'cancelled'].includes(record.status);
    if (terminal) {
      if (result === null || progress !== null || result.business !== record.status
          || result.terminal_revision > record.state_revision) return null;
    } else if (result !== null || progress === null
        || (record.status === 'waiting_user') !== (progress.phase === 'waiting_user')) {
      return null;
    }
    if (record.status === 'cancelled' && !cancel.acknowledged) return null;
    const view: BrowserRunView = {
      schema_version: 'browser.run.v1',
      task_id: record.task_id,
      run_id: record.run_id,
      state_revision: record.state_revision,
      status: record.status,
      progress,
      cancel,
      result,
      artifacts,
      draft,
    };
    if (new TextEncoder().encode(JSON.stringify(view)).length > MAX_WIRE_BYTES) return null;
    return frozenView(view);
  } catch {
    return null;
  }
}

export function isParsedBrowserRunView(value: unknown): value is ParsedBrowserRunView {
  return typeof value === 'object' && value !== null && parsedViews.has(value);
}

export type BrowserRunUpdateDecision = 'apply' | 'drop' | 'noop' | 'conflict';

function sameTerminalFact(before: BrowserResultView, after: BrowserResultView): boolean {
  return before.business === after.business && before.effect === after.effect
    && before.verification === after.verification && before.error_code === after.error_code
    && before.dispatch_failure_code === after.dispatch_failure_code
    && before.terminal_revision === after.terminal_revision;
}

/** Local stale-response ordering only; generation and IDs are not server authorization. */
export function compareBrowserRunUpdate(
  current: ParsedBrowserRunView | null,
  incoming: ParsedBrowserRunView,
  target: {
    readonly taskId: string;
    readonly runId: string;
    readonly requestGeneration: number;
    readonly currentGeneration: number;
  },
): BrowserRunUpdateDecision {
  if (!isParsedBrowserRunView(incoming)
      || (current !== null && !isParsedBrowserRunView(current))) {
    throw new Error('browser_view_not_parsed');
  }
  if (!revision(target.requestGeneration) || !revision(target.currentGeneration)) {
    throw new Error('browser_client_generation_invalid');
  }
  if (!isSafeBrowserId(target.taskId) || !isSafeBrowserId(target.runId)) {
    throw new Error('browser_client_target_invalid');
  }
  if (target.requestGeneration !== target.currentGeneration
      || incoming.task_id !== target.taskId || incoming.run_id !== target.runId) return 'drop';
  if (current === null) return 'apply';
  if (current.task_id !== target.taskId || current.run_id !== target.runId) return 'conflict';
  if (incoming.state_revision < current.state_revision) return 'drop';
  if (incoming.state_revision === current.state_revision) {
    return JSON.stringify(incoming) === JSON.stringify(current) ? 'noop' : 'conflict';
  }
  if ((current.cancel.requested && !incoming.cancel.requested)
      || (current.cancel.acknowledged && !incoming.cancel.acknowledged)) return 'conflict';
  if (current.result !== null) {
    if (incoming.result === null) return 'drop';
    if (!sameTerminalFact(current.result, incoming.result)) return 'conflict';
  }
  if (current.progress !== null && incoming.progress !== null
      && current.progress.total_steps !== null
      && (current.progress.total_steps !== incoming.progress.total_steps
        || incoming.progress.completed_steps === null
        || (current.progress.completed_steps !== null
          && incoming.progress.completed_steps < current.progress.completed_steps))) {
    return 'conflict';
  }
  return 'apply';
}
