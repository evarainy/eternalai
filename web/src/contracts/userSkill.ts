/**
 * Personal saved flows (`data.user_skill`): one of four server-built payloads.
 * proposal → save review card; needs_input → form; choice → pick a flow; result → table.
 * Parsing is exact on the discriminator and required fields; anything else is rejected.
 */

export type UserSkillRowStatus = 'ok' | 'not_found' | 'failed' | 'not_executed';

export type UserSkillCandidateKind = 'param' | 'fixed' | 'omit' | 'step_output' | 'loop_item';
export interface UserSkillBindingCandidate {
  candidate_id: string;
  kind: UserSkillCandidateKind;
  label: string;
}
export interface UserSkillBinding {
  binding_id: string;
  step_index: number;
  step_label: string;
  arg_label: string;
  source_kind: string;
  source_label: string;
  candidates: UserSkillBindingCandidate[];
  prefill: string | null;
}
export interface UserSkillParam {
  binding_id: string;
  label: string;
  schema: Record<string, unknown>;
  required: boolean;
}
export interface UserSkillProposal {
  kind: 'proposal';
  proposal_id: string;
  steps: { step_label: string; system_label: string }[];
  params: UserSkillParam[];
  bindings: UserSkillBinding[];
  for_each_offers: {
    offer_id: string; step_index: number; step_label: string; list_label: string; prefilled: boolean;
  }[];
  column_options: { column_id: string; table_id: string; label: string }[];
  draft: { name: string; purpose: string; rules: string };
  update_targets: { skill_id: string; name: string; current_version: number }[];
  rejections: { code: string; label: string }[];
}
export interface UserSkillForm {
  schema: { properties?: Record<string, Record<string, unknown>>; required?: string[] };
  labels: Record<string, string>;
  prefill?: Record<string, unknown>;
}
export interface UserSkillNeedsInput {
  kind: 'needs_input';
  skill_id: string;
  version: number;
  name: string;
  form: UserSkillForm;
  missing: string[];
}
export interface UserSkillChoice {
  kind: 'choice';
  options: { skill_id: string; version: number; name: string; purpose: string }[];
}
export interface UserSkillTable {
  table_id: string;
  title: string;
  columns: { label: string }[];
  rows: { cells: string[]; status: UserSkillRowStatus; reason_label?: string }[];
  counts: Record<UserSkillRowStatus, number>;
  completeness_label?: string;
}
export interface UserSkillResult {
  kind: 'result';
  run_id: string;
  skill_id: string | null;
  version: number | null;
  name: string;
  inputs: { label: string; value: string }[];
  input?: Record<string, unknown>;
  status: 'completed' | 'partial' | 'failed';
  reason_label?: string;
  notice_label?: string;
  tables: UserSkillTable[];
  queried_at?: string;
  exportable?: boolean;
  takeover_allowed?: boolean;
  trial_id?: string;
}
export type UserSkillPayload =
  | UserSkillProposal | UserSkillNeedsInput | UserSkillChoice | UserSkillResult;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
const isString = (value: unknown): value is string => typeof value === 'string';
const isArray = Array.isArray;

export function parseUserSkillPayload(value: unknown): UserSkillPayload | null {
  if (!isRecord(value)) return null;
  switch (value.kind) {
    case 'proposal':
      return isString(value.proposal_id) && isArray(value.steps) && isArray(value.params)
        && isArray(value.bindings) && isArray(value.column_options) && isRecord(value.draft)
        && isArray(value.update_targets) && isArray(value.for_each_offers)
        && isArray(value.rejections)
        ? (value as unknown as UserSkillProposal) : null;
    case 'needs_input':
      return isString(value.skill_id) && typeof value.version === 'number'
        && isString(value.name) && isRecord(value.form) && isArray(value.missing)
        ? (value as unknown as UserSkillNeedsInput) : null;
    case 'choice':
      return isArray(value.options) && value.options.every(
        (item) => isRecord(item) && isString(item.skill_id) && typeof item.version === 'number'
          && isString(item.name),
      ) ? (value as unknown as UserSkillChoice) : null;
    case 'result':
      return isString(value.name) && isArray(value.tables)
        && (value.status === 'completed' || value.status === 'partial' || value.status === 'failed')
        && value.tables.every((table) => isRecord(table) && isArray(table.columns)
          && isArray(table.rows))
        ? (value as unknown as UserSkillResult) : null;
    default:
      return null;
  }
}
