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
  /** 可设「列表不能为空」「最多几条」的列表表格；旧卡没有此字段。 */
  check_offers?: { offer_id: string; table_title: string; step_index: number; step_label: string }[];
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
  run_id?: string;
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
  /** 未通过用户自己设的结果检查（不是系统故障）。 */
  check_failed?: boolean;
  trial_id?: string;
}

/** 聊天里用 `$` 指定的本人功能：id 加标签上的名称；名称已变服务端即拒绝，否则按本人当前版本执行。 */
export interface UserSkillRef {
  skill_id: string;
  name: string;
}
export type UserSkillPayload =
  | UserSkillProposal | UserSkillNeedsInput | UserSkillChoice | UserSkillResult;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
const isString = (value: unknown): value is string => typeof value === 'string';
const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const isBool = (value: unknown): value is boolean => typeof value === 'boolean';
const optional = (value: unknown, check: (v: unknown) => boolean) => value === undefined || check(value);
const listOf = (value: unknown, check: (item: Record<string, unknown>) => boolean) =>
  Array.isArray(value) && value.every((item) => isRecord(item) && check(item));
const ROW_STATUSES = new Set(['ok', 'not_found', 'failed', 'not_executed']);
const CANDIDATE_KINDS = new Set(['param', 'fixed', 'omit', 'step_output', 'loop_item']);

const isForm = (form: unknown) => isRecord(form) && isRecord(form.schema) && isRecord(form.labels)
  && Object.values(form.labels).every(isString)
  && optional(form.schema.properties,
    (v) => isRecord(v) && Object.values(v).every((property) => isRecord(property)))
  && optional(form.schema.required, (v) => Array.isArray(v) && v.every(isString));

const isTable = (table: Record<string, unknown>) => isString(table.table_id) && isString(table.title)
  && listOf(table.columns, (c) => isString(c.label))
  && listOf(table.rows, (row) => Array.isArray(row.cells) && row.cells.every(isString)
    && ROW_STATUSES.has(row.status as string) && optional(row.reason_label, isString))
  && isRecord(table.counts) && [...ROW_STATUSES].every((key) => isNumber((table.counts as Record<string, unknown>)[key]))
  && optional(table.completeness_label, isString);

function isProposal(value: Record<string, unknown>): boolean {
  return isString(value.proposal_id)
    && listOf(value.steps, (step) => isString(step.step_label) && isString(step.system_label))
    && listOf(value.params, (param) => isString(param.binding_id) && isString(param.label)
      && isRecord(param.schema))
    && listOf(value.bindings, (binding) => isString(binding.binding_id)
      && isNumber(binding.step_index) && isString(binding.step_label) && isString(binding.arg_label)
      && (binding.prefill === null || isString(binding.prefill))
      && listOf(binding.candidates, (c) => isString(c.candidate_id) && isString(c.label)
        && CANDIDATE_KINDS.has(c.kind as string)))
    && listOf(value.for_each_offers, (offer) => isString(offer.offer_id) && isNumber(offer.step_index))
    && listOf(value.column_options, (c) => isString(c.column_id) && isString(c.label))
    && isRecord(value.draft) && isString(value.draft.name) && isString(value.draft.purpose)
    && isString(value.draft.rules)
    && listOf(value.update_targets, (t) => isString(t.skill_id) && isString(t.name)
      && isNumber(t.current_version))
    && listOf(value.rejections, (r) => isString(r.code) && isString(r.label))
    && optional(value.check_offers, (offers) => listOf(offers, (o) => isString(o.offer_id)
      && isString(o.table_title) && isNumber(o.step_index) && isString(o.step_label)));
}

function isResult(value: Record<string, unknown>): boolean {
  // A refusal before any run (flow disabled or changed) carries no run_id.
  return optional(value.run_id, isString) && isString(value.name)
    && (value.skill_id === null || isString(value.skill_id))
    && (value.version === null || isNumber(value.version))
    && (value.status === 'completed' || value.status === 'partial' || value.status === 'failed')
    && listOf(value.inputs, (item) => isString(item.label) && isString(item.value))
    && optional(value.input, isRecord) && optional(value.reason_label, isString)
    && optional(value.notice_label, isString) && optional(value.exportable, isBool)
    && optional(value.takeover_allowed, isBool) && optional(value.check_failed, isBool)
    && listOf(value.tables, isTable);
}

/** Exact on the discriminator and on every field the cards read; anything else is rejected. */
export function parseUserSkillPayload(value: unknown): UserSkillPayload | null {
  if (!isRecord(value)) return null;
  switch (value.kind) {
    case 'proposal':
      return isProposal(value) ? (value as unknown as UserSkillProposal) : null;
    case 'needs_input':
      return isString(value.skill_id) && isNumber(value.version) && isString(value.name)
        && isForm(value.form) && Array.isArray(value.missing)
        ? (value as unknown as UserSkillNeedsInput) : null;
    case 'choice':
      return listOf(value.options, (item) => isString(item.skill_id) && isNumber(item.version)
        && isString(item.name) && isString(item.purpose))
        ? (value as unknown as UserSkillChoice) : null;
    case 'result':
      return isResult(value) ? (value as unknown as UserSkillResult) : null;
    default:
      return null;
  }
}
