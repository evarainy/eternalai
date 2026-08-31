import { describe, expect, it } from 'vitest';

import {
  projectConfirmCard,
  projectRecords,
  type OaNavigationConfig,
} from './runtimeProjection';

function confirmCard(
  payloadOverrides: Record<string, unknown> = {},
  uiOverrides: Record<string, unknown> = {},
) {
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
    ...uiOverrides,
  };
}

const navigationConfig: OaNavigationConfig = {
  baseUrl: 'http://oa.synthetic.invalid',
  pathPrefixes: ['/oa', '/workflow'],
};

function systemMessages(
  link: string | null,
  mobileLink: string | null = null,
  overrides: Record<string, unknown> = {},
) {
  return {
    messages: [
      {
        message_id: 'message-001',
        title: '流程提醒',
        content: '流程已到达审批节点。',
        source_name: 'OA 消息中心',
        occurred_at: '2026-08-30T08:30:00Z',
        business_state: 'unread',
        link,
        mobile_link: mobileLink,
      },
    ],
    returned_count: 1,
    is_complete: true,
    ...overrides,
  };
}

function pendingWorkflows(overrides: Record<string, unknown> = {}) {
  return {
    workflows: [
      {
        todo_id: 'todo-001',
        title: '采购申请审批',
        status: 'pending',
        received_at: '2026-08-30T08:00:00Z',
        created_at: '2026-08-30T07:30:00Z',
        workflow_type_id: 'purchase-approval',
      },
    ],
    returned_count: 1,
    authoritative_count: 1,
    is_complete: true,
    ...overrides,
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

  it.each(['oa', 'u8', 'hikvision_ivms'] as const)(
    'accepts generated target-system value %s',
    (targetSystem) => {
      expect(
        projectConfirmCard(
          confirmCard({ target_system: targetSystem }, { target_system: targetSystem }),
        )?.targetSystem,
      ).toBe(targetSystem);
    },
  );

  it('rejects an unknown payload target system', () => {
    expect(projectConfirmCard(confirmCard({ target_system: 'sap' }))).toBeNull();
  });

  it('rejects a non-empty UI and payload target conflict', () => {
    expect(
      projectConfirmCard(confirmCard({ target_system: 'u8' }, { target_system: 'oa' })),
    ).toBeNull();
  });
});

describe('OA message navigation projection', () => {
  it.each([
    [
      'absolute URL',
      'http://oa.synthetic.invalid/oa/messages/001?source=notice',
      'http://oa.synthetic.invalid/oa/messages/001?source=notice',
    ],
    [
      'root-relative URL',
      '/workflow/messages/001',
      'http://oa.synthetic.invalid/workflow/messages/001',
    ],
  ])('allows an allowlisted %s', (_label, link, expectedHref) => {
    const records = projectRecords(systemMessages(link), navigationConfig);

    expect(records?.kind).toBe('system_messages');
    if (records?.kind !== 'system_messages') throw new Error('wrong record kind');
    expect(records.items[0]?.navigation).toEqual({
      kind: 'allowed',
      href: expectedHref,
    });
  });

  it.each([
    ['scheme-relative host', '//evil.synthetic.invalid/oa/messages/001'],
    ['slash-backslash host', '/\\evil.synthetic.invalid/oa/messages/001'],
    ['backslash host', '\\evil.synthetic.invalid\\oa\\messages\\001'],
    ['non-HTTP(S) protocol', 'javascript:alert(1)'],
    ['malformed HTTP absolute URL', 'http:/oa/messages/001'],
    ['opaque HTTP URL', 'http:oa/messages/001'],
    ['path traversal', '/oa/../admin/messages/001'],
    ['triply encoded path traversal', '/oa/%25252e%25252e/admin'],
    ['encoded control character', '/oa/%00messages/001'],
    ['triply encoded control character', '/oa/%252500messages/001'],
    ['literal control character', '/oa/messages/\u0001'],
    ['path-prefix suffix graft', '/oaevil/messages/001'],
    [
      'host suffix graft',
      'http://oa.synthetic.invalid.evil/oa/messages/001',
    ],
  ])('rejects %s while retaining the record', (_label, link) => {
    const records = projectRecords(systemMessages(link), navigationConfig);

    expect(records?.kind).toBe('system_messages');
    if (records?.kind !== 'system_messages') throw new Error('wrong record kind');
    expect(records.items).toHaveLength(1);
    expect(records.items[0]?.title).toBe('流程提醒');
    expect(records.items[0]?.navigation).toEqual({ kind: 'untrusted' });
    expect(JSON.stringify(records)).not.toContain(link);
  });

  it('requires exact configured origin including scheme', () => {
    const records = projectRecords(
      systemMessages('https://oa.synthetic.invalid/oa/messages/001'),
      navigationConfig,
    );

    expect(records?.kind).toBe('system_messages');
    if (records?.kind !== 'system_messages') throw new Error('wrong record kind');
    expect(records.items[0]?.navigation).toEqual({ kind: 'untrusted' });
  });

  it('distinguishes missing deployment config, missing link, and untrusted link', () => {
    const noConfig = projectRecords(
      systemMessages('/oa/messages/001'),
      null,
    );
    const noLink = projectRecords(systemMessages(null), navigationConfig);
    const untrusted = projectRecords(
      systemMessages('/outside/messages/001'),
      navigationConfig,
    );

    expect(noConfig?.kind).toBe('system_messages');
    expect(noLink?.kind).toBe('system_messages');
    expect(untrusted?.kind).toBe('system_messages');
    if (
      noConfig?.kind !== 'system_messages' ||
      noLink?.kind !== 'system_messages' ||
      untrusted?.kind !== 'system_messages'
    ) {
      throw new Error('wrong record kind');
    }
    expect(noConfig.items[0]?.navigation).toEqual({
      kind: 'deployment_unconfigured',
    });
    expect(noLink.items[0]?.navigation).toEqual({ kind: 'missing' });
    expect(untrusted.items[0]?.navigation).toEqual({ kind: 'untrusted' });
  });

  it.each([
    'http:/oa.synthetic.invalid',
    'http:oa.synthetic.invalid',
    'http://user@oa.synthetic.invalid',
    'http://@oa.synthetic.invalid',
    'http://oa.synthetic.invalid?source=invalid',
    'http://oa.synthetic.invalid#invalid',
    'http://oa.synthetic.invalid/?',
    'http://oa.synthetic.invalid/#',
  ])('fails closed for invalid deployment base %s', (baseUrl) => {
    const records = projectRecords(systemMessages('/oa/messages/001'), {
      baseUrl,
      pathPrefixes: ['/oa'],
    });

    expect(records?.kind).toBe('system_messages');
    if (records?.kind !== 'system_messages') throw new Error('wrong record kind');
    expect(records.items[0]?.navigation).toEqual({
      kind: 'deployment_unconfigured',
    });
  });

  it.each(['/.', '/%2e', '/%252e'])(
    'fails closed when deployment prefix %s normalizes to root',
    (pathPrefix) => {
      const records = projectRecords(systemMessages('/admin/messages/001'), {
        baseUrl: navigationConfig.baseUrl,
        pathPrefixes: [pathPrefix],
      });

      expect(records?.kind).toBe('system_messages');
      if (records?.kind !== 'system_messages') throw new Error('wrong record kind');
      expect(records.items[0]?.navigation).toEqual({
        kind: 'deployment_unconfigured',
      });
    },
  );

  it('does not fall back to mobile_link', () => {
    const mobileLink = '/oa/mobile/messages/001';
    const records = projectRecords(
      systemMessages(null, mobileLink),
      navigationConfig,
    );

    expect(records?.kind).toBe('system_messages');
    if (records?.kind !== 'system_messages') throw new Error('wrong record kind');
    expect(records.items[0]?.navigation).toEqual({ kind: 'missing' });
    expect(JSON.stringify(records)).not.toContain(mobileLink);
  });
});

describe('partial record-list projection', () => {
  it('marks a missing count incomplete without discarding valid rows', () => {
    const records = projectRecords(
      pendingWorkflows({ returned_count: undefined }),
      navigationConfig,
    );

    expect(records?.kind).toBe('pending_workflows');
    if (records?.kind !== 'pending_workflows') throw new Error('wrong record kind');
    expect(records.items).toHaveLength(1);
    expect(records.incomplete).toBe(true);
    expect(records.incompleteReasons).toContain('returned_count_missing');
  });

  it('marks count mismatches incomplete without discarding valid rows', () => {
    const records = projectRecords(
      pendingWorkflows({ returned_count: 2, authoritative_count: 2 }),
      navigationConfig,
    );

    expect(records?.kind).toBe('pending_workflows');
    if (records?.kind !== 'pending_workflows') throw new Error('wrong record kind');
    expect(records.items).toHaveLength(1);
    expect(records.incompleteReasons).toContain('returned_count_mismatch');
    expect(records.incompleteReasons).toContain('authoritative_count_mismatch');
  });

  it('does not use is_complete as an independent incompleteness signal', () => {
    const records = projectRecords(
      pendingWorkflows({ is_complete: false }),
      navigationConfig,
    );

    expect(records?.kind).toBe('pending_workflows');
    if (records?.kind !== 'pending_workflows') throw new Error('wrong record kind');
    expect(records.items).toHaveLength(1);
    expect(records.incomplete).toBe(false);
    expect(records.incompleteReasons).toEqual([]);
  });
});
