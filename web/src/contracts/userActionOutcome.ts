export const USER_ACTION_OUTCOMES = [
  'accepted',
  'action_gate_unavailable',
  'no_pending_action',
  'action_binding_incomplete',
  'action_reference_mismatch',
  'action_pending_changed',
  'action_already_claimed',
  'action_stale',
  'action_version_conflict',
] as const;

export type UserActionOutcome = (typeof USER_ACTION_OUTCOMES)[number];

const userActionOutcomeSet = new Set<string>(USER_ACTION_OUTCOMES);

export function isUserActionOutcome(value: unknown): value is UserActionOutcome {
  return typeof value === 'string' && userActionOutcomeSet.has(value);
}

export const userActionOutcomeMessages: Record<UserActionOutcome, string> = {
  accepted: '操作已受理，已进入本次执行流程。',
  action_gate_unavailable:
    '确认通道暂不可用，无法确认本次操作结果。请先核对业务状态，避免重复提交。',
  no_pending_action: '未找到可继续的待确认操作，本次操作未执行。',
  action_binding_incomplete: '操作绑定信息不完整，本次操作未执行。',
  action_reference_mismatch: '确认引用与当前待办不匹配，本次操作未执行。',
  action_pending_changed: '待确认内容已发生变化，本次操作未执行。',
  action_already_claimed: '这项操作已经处理过，未重复执行；本次未执行新的操作。',
  action_stale: '确认已过期，本次操作未执行。',
  action_version_conflict: '操作版本已变化，本次操作未执行。',
};
