import { customInstance } from './mutator';
import type { UserSkillPayload, UserSkillResult } from '../contracts/userSkill';

/**
 * 「我的功能」接口：身份只来自会话；试运行、保存、运行都要带当前对话的 session_id，
 * 服务端按会话绑定核对。导出走既有后台作业接口。
 */

const BASE = '/api/v1/my-skills';
export const USER_SKILL_REPORT_KIND = 'report.user_skill_xlsx';

export interface TrialTarget {
  mode: 'new' | 'update';
  skill_id?: string;
  expected_current_version?: number;
}

export interface TrialRequest {
  session_id: string;
  binding_choices: Record<string, string>;
  for_each_choices: Record<string, boolean>;
  column_ids: string[];
  inputs: Record<string, unknown>;
  name: string;
  purpose: string;
  rules: string;
  target: TrialTarget;
}

export interface TrialResponse {
  trial_id: string;
  result: UserSkillResult;
  savable: boolean;
  not_savable_reason: string | null;
}

export interface SkillListItem {
  skill_id: string;
  name: string;
  purpose: string;
  state: 'usable' | 'paused' | 'disabled';
  pause_reason_label: string | null;
  current_version: number;
  updated_at: string | null;
  exportable: boolean;
  form: { schema: { properties?: Record<string, Record<string, unknown>>; required?: string[] };
    labels: Record<string, string> };
}

export function trialSkill(proposalId: string, body: TrialRequest): Promise<TrialResponse> {
  return customInstance({
    url: `${BASE}/proposals/${encodeURIComponent(proposalId)}/trials`, method: 'POST', data: body,
  });
}

export function saveSkill(sessionId: string, trialId: string):
  Promise<{ skill_id: string; version: number; state: string }> {
  return customInstance({ url: BASE, method: 'POST', data: { session_id: sessionId, trial_id: trialId } });
}

export function listSkills(): Promise<{ items: SkillListItem[] }> {
  return customInstance({ url: BASE, method: 'GET' });
}

export function setSkillState(skillId: string, state: 'usable' | 'disabled', version: number):
  Promise<{ skill_id: string; state: string; current_version: number }> {
  return customInstance({
    url: `${BASE}/${encodeURIComponent(skillId)}`, method: 'PATCH',
    data: { state, expected_current_version: version },
  });
}

export function runSkill(sessionId: string, skillId: string, version: number,
  input: Record<string, unknown>): Promise<{ user_skill: UserSkillPayload }> {
  return customInstance({
    url: `${BASE}/${encodeURIComponent(skillId)}/runs`, method: 'POST',
    data: { session_id: sessionId, version, input },
  });
}

export function requestTakeover(sessionId: string, runId: string): Promise<null> {
  return customInstance({
    url: `${BASE}/runs/${encodeURIComponent(runId)}/takeover`, method: 'POST',
    data: { session_id: sessionId },
  });
}

export function submitExport(sessionId: string, idempotencyKey: string, skillId: string,
  version: number, input: Record<string, unknown>): Promise<{ job_id: string; kind: string }> {
  return customInstance({
    url: '/api/v1/background-jobs', method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    data: { kind: USER_SKILL_REPORT_KIND, params: { skill_id: skillId, version, input },
      session_id: sessionId },
  });
}
