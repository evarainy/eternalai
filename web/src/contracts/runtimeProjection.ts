import type { ResponseEnvelopeStatus } from '../generated/runtime/runtime.schemas';
import {
  isUserActionOutcome,
  type UserActionOutcome,
} from './userActionOutcome';

export const SAFE_INCOMPATIBLE_TEXT = '当前响应无法安全显示，请稍后重试。';

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
const targetSystems = new Set(['oa', 'u8', 'hikvision_ivms']);
const confirmPayloadKeys = new Set([
  'capability_id',
  'operation_summary',
  'target_system',
  'field_names',
  'displayed_argument_values',
]);

export type TargetSystem = 'oa' | 'u8' | 'hikvision_ivms';
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
  targetSystem: string | null;
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
}

export type RecordsView =
  | {
      kind: 'pending_workflows';
      items: PendingWorkflowView[];
      returnedCount: number;
      authoritativeCount: number;
      incomplete: boolean;
    }
  | {
      kind: 'system_messages';
      items: SystemMessageView[];
      returnedCount: number;
      incomplete: boolean;
    };

export interface ProjectedResponse {
  role: 'assistant';
  text: string;
  status?: ResponseEnvelopeStatus;
  presentationKind: PresentationKind;
  targetSystem?: TargetSystem;
  responseId: string | null;
  confirm: ConfirmCardView | null;
  records: RecordsView | null;
  actionOutcome: UserActionOutcome | null;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
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
      typeof payload.target_system === 'string' ||
      payload.target_system === null
    ) ||
    !Array.isArray(payload.field_names) ||
    !payload.field_names.every((fieldName) => typeof fieldName === 'string') ||
    !isRecord(payload.displayed_argument_values)
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
  if (
    !isRecord(data) ||
    !Array.isArray(data.workflows) ||
    typeof data.returned_count !== 'number' ||
    typeof data.authoritative_count !== 'number' ||
    typeof data.is_complete !== 'boolean'
  ) {
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

  return {
    kind: 'pending_workflows',
    items,
    returnedCount: data.returned_count,
    authoritativeCount: data.authoritative_count,
    incomplete:
      data.is_complete !== true ||
      data.returned_count !== data.authoritative_count ||
      items.length !== data.returned_count,
  };
}

function projectSystemMessages(data: unknown): RecordsView | null {
  if (
    !isRecord(data) ||
    !Array.isArray(data.messages) ||
    typeof data.returned_count !== 'number' ||
    typeof data.is_complete !== 'boolean'
  ) {
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
    });
  }

  return {
    kind: 'system_messages',
    items,
    returnedCount: data.returned_count,
    incomplete:
      data.is_complete !== true || items.length !== data.returned_count,
  };
}

export function projectRecords(data: unknown): RecordsView | null {
  return projectPendingWorkflows(data) ?? projectSystemMessages(data);
}

function structuredData(value: unknown): {
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
      records: projectRecords(value.result),
      incompatible: false,
    };
  }
  return {
    actionOutcome: null,
    records: projectRecords(value),
    incompatible: false,
  };
}

export function projectResponse(value: unknown): ProjectedResponse {
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
      (typeof targetSystem === 'string' && targetSystems.has(targetSystem))
    )
  ) {
    return incompatibleResponse();
  }

  const text = value.message.trim() || value.fallback_text.trim();
  if (!text) {
    return incompatibleResponse();
  }

  const data = structuredData(value.data);
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
        ? { targetSystem: targetSystem as TargetSystem }
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
