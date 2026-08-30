import { describe, expect, it } from 'vitest';

import { projectConfirmCard } from './runtimeProjection';

function confirmCard(payloadOverrides: Record<string, unknown> = {}) {
  return {
    component_type: 'confirm_card',
    action: 'confirm',
    target_system: 'oa',
    payload: {
      capability_id: 'oa.synthetic.approve',
      operation_summary: '提交审批',
      target_system: 'oa',
      field_names: ['decision'],
      displayed_argument_values: { decision: '同意' },
      ...payloadOverrides,
    },
  };
}

describe('projectConfirmCard exact payload contract', () => {
  it('accepts the exact five-key payload', () => {
    expect(projectConfirmCard(confirmCard())).toEqual({
      capabilityId: 'oa.synthetic.approve',
      operationSummary: '提交审批',
      targetSystem: 'oa',
      fieldNames: ['decision'],
      displayedArgumentValues: { decision: '同意' },
    });
  });

  it('rejects a sixth payload key', () => {
    expect(projectConfirmCard(confirmCard({ unexpected: 'blocked' }))).toBeNull();
  });
});
