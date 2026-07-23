import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { ApiError } from '../../../api/mutator';
import type { AdminCapabilityView } from '../../../generated/admin/admin.schemas';
import RegistryPage from '../RegistryPage';

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
    <ConfigProvider>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <RegistryPage />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
}

async function openAndFillCreateForm() {
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
    'submits the exact create body without a status field',
    async () => {
      renderPage();
      await screen.findByText('cap.query');
      await openAndFillCreateForm();

      fireEvent.click(screen.getByRole('button', { name: '创建 draft' }));

      await waitFor(() => {
        expect(apiMocks.createCapability).toHaveBeenCalledWith({
          capability_id: 'cap.created',
          name: 'Created Capability',
          type: 'query',
          intent_tags: [],
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
