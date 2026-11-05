import { ApiError } from '../../api/mutator';
import {
  grantPersonalAgentMaterial, listPersonalAgentGrants, listPersonalAgentMaterials,
  listPersonalAgentRecipients, listPersonalAgentRequests, listPersonalAgents,
  publishPersonalAgentMaterial, revokePersonalAgentGrant, withdrawPersonalAgentMaterial,
} from '../../generated/personal-agents/personal-agents';

export const readPersonalAgents = listPersonalAgents;
export const readAgentMaterials = listPersonalAgentMaterials;
export const readAgentGrants = listPersonalAgentGrants;
export const readAgentRecipients = listPersonalAgentRecipients;
export const readAgentRequests = listPersonalAgentRequests;
export const publishAgentMaterial = publishPersonalAgentMaterial;
export const grantAgentMaterial = grantPersonalAgentMaterial;
export const revokeAgentGrant = revokePersonalAgentGrant;
export const withdrawAgentMaterial = withdrawPersonalAgentMaterial;

export type PersonalAgent = Awaited<ReturnType<typeof readPersonalAgents>>['agents'][number];
export type AgentMaterial = Awaited<ReturnType<typeof readAgentMaterials>>['materials'][number];
export type AgentGrant = Awaited<ReturnType<typeof readAgentGrants>>['grants'][number];
export type AgentRecipient = Awaited<ReturnType<typeof readAgentRecipients>>['recipients'][number];
export type AgentRequest = Awaited<ReturnType<typeof readAgentRequests>>['requests'][number];
export type PublishMaterial = Parameters<typeof publishAgentMaterial>[1];
export type GrantMaterial = Parameters<typeof grantAgentMaterial>[1];

const ERRORS: Record<string, string> = {
  personal_agent_unavailable: '个人助手尚未开通，请联系维护人员核对。',
  personal_agent_not_found: '未找到当前账号可管理的个人助手。',
  personal_agent_expiry_invalid: '请核对截止时间：必须晚于现在，授权不得超过资料到期时间。',
  personal_agent_grantee_unavailable: '该同事的 IM 身份目前不可用，请重新选择或联系维护人员核对。',
  personal_agent_grant_not_found: '未找到该授权，请刷新原记录核对。',
  personal_agent_material_not_found: '未找到该资料版本，请刷新原记录核对。',
  personal_agent_mapping_changed: 'IM 身份映射已变化，请联系维护人员重新核验。',
};

export function personalAgentError(error: unknown): string {
  if (error instanceof ApiError) {
    return error.status === 401 ? '登录已失效，请重新登录。' : ERRORS[error.code] ?? error.message;
  }
  return '连接中断，操作结果尚未确认，请刷新原记录核对。';
}

export const REQUEST_STATE_LABELS: Record<AgentRequest['state'], string> = {
  queued: '已接收，等待处理', running: '正在代答', completed: '已处理，等待投递结果',
  refused: '未获有效授权，无法代答', failed: '处理失败，请重新提问',
};
