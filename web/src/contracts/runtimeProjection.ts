import type {
  ConfirmCardPayloadTargetSystem,
  ResponseEnvelopeStatus,
} from '../generated/runtime/runtime.schemas';
import {
  isUserActionOutcome,
  type UserActionOutcome,
} from './userActionOutcome';

export const SAFE_INCOMPATIBLE_TEXT = '当前响应无法安全显示，请稍后重试。';

declare const __ETERNALAI_OA_BASE_URL__: string;
declare const __ETERNALAI_OA_ALLOWED_PATH_PREFIXES__: string;

const SUPPORTED_SCHEMA_VERSION = 'phase0.sdui.v1';
const responseStatuses = new Set<ResponseEnvelopeStatus>([
  'completed',
  'blocked',
  'waiting_user',
  'failed',
  'no_capability_found',
]);
const responseActions = new Set([
  'confirm',
  'bind_required',
  'clarify_scope',
  'none',
  null,
]);
type GeneratedTargetSystem = Exclude<ConfirmCardPayloadTargetSystem, null>;
const generatedTargetSystemMembership = {
  oa: true,
  u8: true,
  hikvision_ivms: true,
} as const satisfies Record<GeneratedTargetSystem, true>;
const confirmPayloadKeys = new Set([
  'capability_id',
  'operation_summary',
  'target_system',
  'field_names',
  'displayed_argument_values',
]);

export type PresentationKind =
  | 'completed'
  | 'clarification'
  | 'confirmation'
  | 'binding'
  | 'denied'
  | 'unavailable'
  | 'failed'
  | 'incompatible'
  | 'csrf'
  | 'session'
  | 'validation'
  | 'service'
  | 'network'
  | 'request_error';

export interface ConfirmCardView {
  capabilityId: string;
  operationSummary: string;
  targetSystem: ConfirmCardPayloadTargetSystem;
  fieldNames: string[];
  displayedArgumentValues: Record<string, string>;
}

export interface PendingWorkflowView {
  todoId: string;
  title: string;
  status: string;
  receivedAt: string;
  createdAt: string;
  workflowTypeId: string;
}

export interface SystemMessageView {
  messageId: string;
  title: string;
  content: string;
  sourceName: string;
  occurredAt: string;
  businessState: string;
  navigation:
    | { kind: 'allowed'; href: string }
    | { kind: 'deployment_unconfigured' | 'missing' | 'untrusted' };
}

export type RecordsIncompleteReason =
  | 'authoritative_count_missing'
  | 'authoritative_count_mismatch'
  | 'returned_count_missing'
  | 'returned_count_mismatch';

export type RecordsView =
  | {
      kind: 'pending_workflows';
      items: PendingWorkflowView[];
      returnedCount: number | null;
      authoritativeCount: number | null;
      incomplete: boolean;
      incompleteReasons: RecordsIncompleteReason[];
    }
  | {
      kind: 'system_messages';
      items: SystemMessageView[];
      returnedCount: number | null;
      incomplete: boolean;
      incompleteReasons: RecordsIncompleteReason[];
    };

export interface ProjectedResponse {
  role: 'assistant';
  text: string;
  status?: ResponseEnvelopeStatus;
  presentationKind: PresentationKind;
  targetSystem?: GeneratedTargetSystem;
  responseId: string | null;
  confirm: ConfirmCardView | null;
  records: RecordsView | null;
  actionOutcome: UserActionOutcome | null;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isGeneratedTargetSystem(value: unknown): value is GeneratedTargetSystem {
  return (
    typeof value === 'string' &&
    Object.prototype.hasOwnProperty.call(generatedTargetSystemMembership, value)
  );
}

export interface OaNavigationConfig {
  baseUrl: string;
  pathPrefixes: readonly string[];
}

interface NormalizedOaNavigationConfig {
  baseUrl: string;
  origin: string;
  pathPrefixes: readonly string[];
}

function deployedOaNavigationConfig(): OaNavigationConfig | null {
  let pathPrefixes: unknown;
  try {
    pathPrefixes = JSON.parse(__ETERNALAI_OA_ALLOWED_PATH_PREFIXES__);
  } catch {
    return null;
  }
  if (!Array.isArray(pathPrefixes)) {
    return null;
  }
  return {
    baseUrl: __ETERNALAI_OA_BASE_URL__,
    pathPrefixes,
  };
}

function hasControlCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0);
    return codePoint !== undefined && (codePoint <= 0x1f || codePoint === 0x7f);
  });
}

function decodePathForSafety(path: string): string | null {
  let decoded = path;
  let stabilized = false;
  for (let remainingPasses = path.length + 1; remainingPasses > 0; remainingPasses -= 1) {
    let next: string;
    try {
      next = decodeURIComponent(decoded);
    } catch {
      return null;
    }
    if (next === decoded) {
      stabilized = true;
      break;
    }
    decoded = next;
  }
  if (!stabilized || hasControlCharacter(decoded) || decoded.includes('\\')) {
    return null;
  }
  if (decoded.split('/').some((segment) => segment === '.' || segment === '..')) {
    return null;
  }
  return decoded;
}

function normalizeOaNavigationConfig(
  config: OaNavigationConfig | null,
): NormalizedOaNavigationConfig | null {
  if (config === null || !config.baseUrl.trim() || config.pathPrefixes.length === 0) {
    return null;
  }
  const rawBaseUrl = config.baseUrl.trim().replace(/\/+$/, '');
  if (
    !/^https?:\/\/[^/\\?#]+(?:\/.*)?$/i.test(rawBaseUrl) ||
    hasControlCharacter(rawBaseUrl) ||
    rawBaseUrl.includes('\\') ||
    rawBaseUrl.includes('?') ||
    rawBaseUrl.includes('#')
  ) {
    return null;
  }
  const authority =
    rawBaseUrl.replace(/^https?:\/\//i, '').split('/', 1)[0] ?? '';
  let baseUrl: URL;
  try {
    baseUrl = new URL(rawBaseUrl);
  } catch {
    return null;
  }
  if (
    (baseUrl.protocol !== 'http:' && baseUrl.protocol !== 'https:') ||
    !baseUrl.hostname ||
    authority.includes('@') ||
    baseUrl.username !== '' ||
    baseUrl.password !== '' ||
    baseUrl.search !== '' ||
    baseUrl.hash !== ''
  ) {
    return null;
  }

  const pathPrefixes: string[] = [];
  for (const rawPrefix of config.pathPrefixes) {
    if (
      typeof rawPrefix !== 'string' ||
      !rawPrefix.startsWith('/') ||
      rawPrefix.startsWith('//') ||
      rawPrefix.includes('\\') ||
      rawPrefix.includes('?') ||
      rawPrefix.includes('#') ||
      decodePathForSafety(rawPrefix) === null
    ) {
      return null;
    }
    let parsedPrefix: URL;
    try {
      parsedPrefix = new URL(rawPrefix, baseUrl.origin);
    } catch {
      return null;
    }
    if (parsedPrefix.origin !== baseUrl.origin) {
      return null;
    }
    pathPrefixes.push(
      parsedPrefix.pathname === '/'
        ? '/'
        : parsedPrefix.pathname.replace(/\/+$/, ''),
    );
  }
  return {
    baseUrl: baseUrl.origin,
    origin: baseUrl.origin,
    pathPrefixes,
  };
}

function projectOaNavigation(
  link: string | null,
  config: NormalizedOaNavigationConfig | null,
): SystemMessageView['navigation'] {
  if (link === null || !link.trim()) {
    return { kind: 'missing' };
  }
  if (config === null) {
    return { kind: 'deployment_unconfigured' };
  }
  if (hasControlCharacter(link)) {
    return { kind: 'untrusted' };
  }

  const candidateText = link.trim();
  const pathText = candidateText.split(/[?#]/, 1)[0] ?? '';
  const isAbsoluteHttpUrl = /^https?:\/\//i.test(candidateText);
  const isRootRelativePath =
    candidateText.startsWith('/') && !candidateText.startsWith('//');
  if (
    candidateText.includes('\\') ||
    (!isAbsoluteHttpUrl && !isRootRelativePath) ||
    decodePathForSafety(pathText) === null
  ) {
    return { kind: 'untrusted' };
  }

  let candidate: URL;
  try {
    candidate = new URL(candidateText, config.baseUrl);
  } catch {
    return { kind: 'untrusted' };
  }
  if (
    (candidate.protocol !== 'http:' && candidate.protocol !== 'https:') ||
    candidate.username !== '' ||
    candidate.password !== '' ||
    candidate.origin !== config.origin
  ) {
    return { kind: 'untrusted' };
  }
  const pathAllowed = config.pathPrefixes.some(
    (prefix) =>
      prefix === '/' ||
      candidate.pathname === prefix ||
      candidate.pathname.startsWith(`${prefix}/`),
  );
  return pathAllowed
    ? { kind: 'allowed', href: candidate.href }
    : { kind: 'untrusted' };
}

function optionalCount(value: unknown): number | null {
  return typeof value === 'number' ? value : null;
}

function recordsIncompleteReasons(
  returnedCount: number | null,
  itemCount: number,
  authoritativeCount?: number | null,
): RecordsIncompleteReason[] {
  const reasons: RecordsIncompleteReason[] = [];
  if (returnedCount === null) {
    reasons.push('returned_count_missing');
  } else if (returnedCount !== itemCount) {
    reasons.push('returned_count_mismatch');
  }
  if (authoritativeCount === null) {
    reasons.push('authoritative_count_missing');
  } else if (authoritativeCount !== undefined && authoritativeCount !== itemCount) {
    reasons.push('authoritative_count_mismatch');
  }
  return reasons;
}

export function projectTextResponse(
  text: string,
  presentationKind: PresentationKind,
): ProjectedResponse {
  return {
    role: 'assistant',
    text,
    presentationKind,
    responseId: null,
    confirm: null,
    records: null,
    actionOutcome: null,
  };
}

export function incompatibleResponse(): ProjectedResponse {
  return projectTextResponse(SAFE_INCOMPATIBLE_TEXT, 'incompatible');
}

export function projectConfirmCard(value: unknown): ConfirmCardView | null {
  if (!isRecord(value) || value.component_type !== 'confirm_card') {
    return null;
  }
  const payload = value.payload;
  const payloadKeys = isRecord(payload) ? Object.keys(payload) : [];
  if (
    !isRecord(payload) ||
    payloadKeys.length !== confirmPayloadKeys.size ||
    !payloadKeys.every((key) => confirmPayloadKeys.has(key)) ||
    typeof payload.capability_id !== 'string' ||
    !payload.capability_id.trim() ||
    typeof payload.operation_summary !== 'string' ||
    !(
      payload.target_system === null ||
      isGeneratedTargetSystem(payload.target_system)
    ) ||
    !Array.isArray(payload.field_names) ||
    !payload.field_names.every((fieldName) => typeof fieldName === 'string') ||
    !isRecord(payload.displayed_argument_values)
  ) {
    return null;
  }
  const uiTargetSystem = value.target_system;
  if (
    !(
      uiTargetSystem === undefined ||
      uiTargetSystem === null ||
      isGeneratedTargetSystem(uiTargetSystem)
    ) ||
    (uiTargetSystem !== undefined &&
      uiTargetSystem !== null &&
      uiTargetSystem !== payload.target_system)
  ) {
    return null;
  }

  const displayedArgumentValues: Record<string, string> = {};
  for (const [fieldName, fieldValue] of Object.entries(
    payload.displayed_argument_values,
  )) {
    if (typeof fieldValue !== 'string') {
      return null;
    }
    displayedArgumentValues[fieldName] = fieldValue;
  }

  return {
    capabilityId: payload.capability_id,
    operationSummary: payload.operation_summary,
    targetSystem: payload.target_system,
    fieldNames: [...payload.field_names],
    displayedArgumentValues,
  };
}

function projectPendingWorkflows(data: unknown): RecordsView | null {
  if (!isRecord(data) || !Array.isArray(data.workflows)) {
    return null;
  }

  const items: PendingWorkflowView[] = [];
  for (const workflow of data.workflows) {
    if (
      !isRecord(workflow) ||
      typeof workflow.todo_id !== 'string' ||
      typeof workflow.title !== 'string' ||
      typeof workflow.status !== 'string' ||
      typeof workflow.received_at !== 'string' ||
      typeof workflow.created_at !== 'string' ||
      typeof workflow.workflow_type_id !== 'string'
    ) {
      return null;
    }
    items.push({
      todoId: workflow.todo_id,
      title: workflow.title,
      status: workflow.status,
      receivedAt: workflow.received_at,
      createdAt: workflow.created_at,
      workflowTypeId: workflow.workflow_type_id,
    });
  }

  const returnedCount = optionalCount(data.returned_count);
  const authoritativeCount = optionalCount(data.authoritative_count);
  const incompleteReasons = recordsIncompleteReasons(
    returnedCount,
    items.length,
    authoritativeCount,
  );
  return {
    kind: 'pending_workflows',
    items,
    returnedCount,
    authoritativeCount,
    incomplete: incompleteReasons.length > 0,
    incompleteReasons,
  };
}

function projectSystemMessages(
  data: unknown,
  navigationConfig: NormalizedOaNavigationConfig | null,
): RecordsView | null {
  if (!isRecord(data) || !Array.isArray(data.messages)) {
    return null;
  }

  const items: SystemMessageView[] = [];
  for (const message of data.messages) {
    if (
      !isRecord(message) ||
      typeof message.message_id !== 'string' ||
      typeof message.title !== 'string' ||
      typeof message.content !== 'string' ||
      typeof message.source_name !== 'string' ||
      typeof message.occurred_at !== 'string' ||
      typeof message.business_state !== 'string' ||
      !(
        typeof message.link === 'string' ||
        message.link === null
      ) ||
      !(
        typeof message.mobile_link === 'string' ||
        message.mobile_link === null
      )
    ) {
      return null;
    }
    items.push({
      messageId: message.message_id,
      title: message.title,
      content: message.content,
      sourceName: message.source_name,
      occurredAt: message.occurred_at,
      businessState: message.business_state,
      navigation: projectOaNavigation(message.link, navigationConfig),
    });
  }

  const returnedCount = optionalCount(data.returned_count);
  const incompleteReasons = recordsIncompleteReasons(
    returnedCount,
    items.length,
  );
  return {
    kind: 'system_messages',
    items,
    returnedCount,
    incomplete: incompleteReasons.length > 0,
    incompleteReasons,
  };
}

export function projectRecords(
  data: unknown,
  navigationConfig: OaNavigationConfig | null = deployedOaNavigationConfig(),
): RecordsView | null {
  return (
    projectPendingWorkflows(data) ??
    projectSystemMessages(data, normalizeOaNavigationConfig(navigationConfig))
  );
}

function structuredData(
  value: unknown,
  navigationConfig: OaNavigationConfig | null,
): {
  actionOutcome: UserActionOutcome | null;
  records: RecordsView | null;
  incompatible: boolean;
} {
  if (
    isRecord(value) &&
    Object.prototype.hasOwnProperty.call(value, 'action_outcome')
  ) {
    if (!isUserActionOutcome(value.action_outcome)) {
      return { actionOutcome: null, records: null, incompatible: true };
    }
    return {
      actionOutcome: value.action_outcome,
      records: projectRecords(value.result, navigationConfig),
      incompatible: false,
    };
  }
  return {
    actionOutcome: null,
    records: projectRecords(value, navigationConfig),
    incompatible: false,
  };
}

export function projectResponse(
  value: unknown,
  navigationConfig: OaNavigationConfig | null = deployedOaNavigationConfig(),
): ProjectedResponse {
  if (!isRecord(value) || value.schema_version !== SUPPORTED_SCHEMA_VERSION) {
    return incompatibleResponse();
  }
  if (
    typeof value.status !== 'string' ||
    !responseStatuses.has(value.status as ResponseEnvelopeStatus) ||
    typeof value.message !== 'string' ||
    typeof value.fallback_text !== 'string' ||
    !isRecord(value.ui)
  ) {
    return incompatibleResponse();
  }

  const status = value.status as ResponseEnvelopeStatus;
  const action = value.ui.action;
  const targetSystem = value.ui.target_system;
  if (
    !responseActions.has(action as string | null) ||
    !(
      targetSystem === undefined ||
      targetSystem === null ||
      isGeneratedTargetSystem(targetSystem)
    )
  ) {
    return incompatibleResponse();
  }

  const text = value.message.trim() || value.fallback_text.trim();
  if (!text) {
    return incompatibleResponse();
  }

  const data = structuredData(value.data, navigationConfig);
  if (data.incompatible) {
    return incompatibleResponse();
  }
  const responseId =
    typeof value.response_id === 'string' && value.response_id.trim()
      ? value.response_id
      : null;
  const base = {
    role: 'assistant' as const,
    text,
    status,
    responseId,
    confirm: null,
    records: data.records,
    actionOutcome: data.actionOutcome,
  };

  if (status === 'completed' && (action === 'none' || action === null)) {
    return { ...base, presentationKind: 'completed' };
  }
  if (status === 'blocked' && action === 'clarify_scope') {
    return { ...base, presentationKind: 'clarification' };
  }
  if (status === 'waiting_user' && action === 'confirm') {
    return {
      ...base,
      presentationKind: 'confirmation',
      confirm: projectConfirmCard(value.ui),
    };
  }
  if (status === 'blocked' && action === 'bind_required') {
    return {
      ...base,
      presentationKind: 'binding',
      ...(typeof targetSystem === 'string'
        ? { targetSystem: targetSystem as GeneratedTargetSystem }
        : {}),
    };
  }
  if (status === 'blocked' && (action === 'none' || action === null)) {
    return { ...base, presentationKind: 'denied' };
  }
  if (
    status === 'no_capability_found' &&
    (action === 'none' || action === null)
  ) {
    return { ...base, presentationKind: 'unavailable' };
  }
  if (status === 'failed' && (action === 'none' || action === null)) {
    return { ...base, presentationKind: 'failed' };
  }
  return incompatibleResponse();
}
