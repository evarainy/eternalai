import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { ApiError } from '../../../api/mutator';
import type { AdminCapabilityView } from '../../../generated/admin/admin.schemas';
import RegistryPage from '../RegistryPage';
import { normalizeIntentTags, normalizePromptSafeText } from '../registryValidation';

vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  const { LightweightTable } = await import('../../../test/LightweightTable');
  return { ...actual, Table: LightweightTable };
});

const apiMocks = vi.hoisted(() => ({
  listRegistry: vi.fn(),
  createCapability: vi.fn(),
  enableCapability: vi.fn(),
  disableCapability: vi.fn(),
}));

vi.mock('../../../generated/admin/admin', () => apiMocks);

const capabilities: AdminCapabilityView[] = [
  {
    capability_id: 'cap.query',
    name: 'Query Capability',
    type: 'query',
    intent_tags: ['lookup'],
    input_schema_digest: 'sha256:input',
    output_schema_digest: 'sha256:output',
    risk_level: 'low',
    owner: 'platform',
    version: '1.0.0',
    status: 'disabled',
    short_description: 'Query data',
    target_system: 'oa',
    execution_identity: 'user_delegated',
    binding_required: true,
  },
  {
    capability_id: 'cap.action',
    name: 'Action Capability',
    type: 'action',
    intent_tags: ['write'],
    input_schema_digest: 'sha256:action-input',
    output_schema_digest: 'sha256:action-output',
    risk_level: 'medium',
    owner: 'operations',
    version: '2.0.0',
    status: 'active',
    short_description: 'Perform action',
    target_system: 'u8',
    execution_identity: 'admin_approved_proxy',
    binding_required: false,
  },
];

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <RegistryPage />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
}

function openAndFillCreateForm() {
  fireEvent.click(screen.getByRole('button', { name: '新建能力' }));
  fireEvent.change(screen.getByLabelText('Capability ID'), {
    target: { value: 'cap.created' },
  });
  fireEvent.change(screen.getByLabelText('名称'), {
    target: { value: 'Created Capability' },
  });
  fireEvent.change(screen.getByLabelText('Input Schema Digest'), {
    target: { value: 'sha256:new-input' },
  });
  fireEvent.change(screen.getByLabelText('Output Schema Digest'), {
    target: { value: 'sha256:new-output' },
  });
  fireEvent.change(screen.getByLabelText('Owner'), {
    target: { value: 'admin-lite' },
  });
  fireEvent.change(screen.getByLabelText('版本'), {
    target: { value: '1.0.0' },
  });
  fireEvent.change(screen.getByLabelText('简短描述'), {
    target: { value: 'Created from Admin Lite' },
  });
}

function renderCreateForm() {
  apiMocks.listRegistry.mockResolvedValueOnce({ items: [] });
  renderPage();
  openAndFillCreateForm();
}

function addIntentTag(value: string) {
  const input = screen.getByLabelText('Intent Tags');
  fireEvent.mouseDown(input);
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13 });
  expect(
    input.closest('.ant-select')?.querySelector('.ant-select-selection-item'),
  ).toHaveTextContent(value);
}

describe('RegistryPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.listRegistry.mockResolvedValue({ items: capabilities });
    apiMocks.createCapability.mockResolvedValue(capabilities[0]);
    apiMocks.enableCapability.mockResolvedValue({ ...capabilities[0], status: 'active' });
    apiMocks.disableCapability.mockResolvedValue({ ...capabilities[1], status: 'disabled' });
  });

  it('renders Registry rows and key columns from the generated view', async () => {
    renderPage();

    expect(await screen.findByText('cap.query')).toBeInTheDocument();
    expect(screen.getByText('Query Capability')).toBeInTheDocument();
    expect(screen.getByText('disabled')).toBeInTheDocument();
    expect(screen.getByText('oa')).toBeInTheDocument();
    expect(screen.getByText('user_delegated')).toBeInTheDocument();
    expect(screen.getByText('platform')).toBeInTheDocument();
    expect(screen.getByText('1.0.0')).toBeInTheDocument();
  });

  it(
    'normalizes prompt-safe text and intent tags in the exact create body',
    async () => {
      renderCreateForm();

      fireEvent.change(screen.getByLabelText('名称'), {
        target: { value: '  Ｃreated Capability  ' },
      });
      fireEvent.change(screen.getByLabelText('Owner'), {
        target: { value: '  ａｄｍｉｎ－ｌｉｔｅ  ' },
      });
      fireEvent.change(screen.getByLabelText('简短描述'), {
        target: { value: '  Ｃreated from Admin Lite  ' },
      });
      addIntentTag('ＳＨＡＲＥＤ－ＩＮＴＥＮＴ');

      fireEvent.click(screen.getByRole('button', { name: '创建 draft' }));

      await waitFor(() => {
        expect(apiMocks.createCapability).toHaveBeenCalledWith({
          capability_id: 'cap.created',
          name: 'Created Capability',
          type: 'query',
          intent_tags: ['shared-intent'],
          input_schema: {},
          output_schema: {},
          input_schema_digest: 'sha256:new-input',
          output_schema_digest: 'sha256:new-output',
          risk_level: 'low',
          owner: 'admin-lite',
          version: '1.0.0',
          short_description: 'Created from Admin Lite',
          target_system: null,
          execution_identity: 'user_delegated',
          binding_required: false,
          policy_digest: null,
        });
      });
      expect(apiMocks.createCapability.mock.calls[0]?.[0]).not.toHaveProperty('status');
    },
    10_000,
  );

  it.each([
    ['超长自由文本', '名称', 'n'.repeat(121), 120, '名称 最多 120 个字符'],
    [
      '换行',
      '简短描述',
      'first line\nsecond line',
      500,
      '简短描述 不能包含换行、控制字符或不可打印字符',
    ],
    [
      '控制字符',
      'Owner',
      'operations\u0007',
      120,
      'Owner 不能包含换行、控制字符或不可打印字符',
    ],
    [
      'prompt 结构字符',
      'Owner',
      'operations|admin',
      120,
      'Owner 不能包含 prompt 结构字符',
    ],
  ])(
    'rejects %s in the mirrored validator',
    (_, fieldLabel, value, maxLength, expectedError) => {
      expect(() => normalizePromptSafeText(value, fieldLabel, maxLength)).toThrow(
        expectedError,
      );
    },
  );

  it('rejects an invalid intent tag shape in the mirrored validator', () => {
    expect(() => normalizeIntentTags(['bad tag'])).toThrow(
      'Intent Tag 必须匹配 a-z、0-9 及单个 ._- 分隔的 slug 形态',
    );
  });

  it('calls the enable and disable endpoints for the matching row actions', async () => {
    renderPage();
    await screen.findByText('cap.query');

    fireEvent.click(screen.getByRole('button', { name: /启\s*用/ }));
    fireEvent.click(screen.getByRole('button', { name: /停\s*用/ }));

    await waitFor(() => {
      expect(apiMocks.enableCapability).toHaveBeenCalledWith('cap.query');
      expect(apiMocks.disableCapability).toHaveBeenCalledWith('cap.action');
    });
  });

  it('shows the backend 403 code and message when enable is rejected', async () => {
    apiMocks.enableCapability.mockRejectedValueOnce(
      new ApiError(403, 'role_not_allowed', 'Management role is required.'),
    );
    renderPage();
    await screen.findByText('cap.query');

    fireEvent.click(screen.getByRole('button', { name: /启\s*用/ }));

    await waitFor(() => {
      expect(
        screen.getAllByText('role_not_allowed: Management role is required.').length,
      ).toBeGreaterThan(0);
    });
  });
});
