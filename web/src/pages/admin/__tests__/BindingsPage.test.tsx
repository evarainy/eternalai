import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { ApiError } from '../../../api/mutator';
import type {
  AdminBindingMutationResponse,
  AdminBindingView,
} from '../../../generated/admin/admin.schemas';
import BindingsPage from '../BindingsPage';

vi.mock('antd', async (importOriginal) => {
  const actual = await importOriginal<typeof import('antd')>();
  const { LightweightTable } = await import('../../../test/LightweightTable');
  return { ...actual, Table: LightweightTable };
});

const apiMocks = vi.hoisted(() => ({
  listBindings: vi.fn(),
  revokeBinding: vi.fn(),
  resetBinding: vi.fn(),
}));

vi.mock('../../../generated/admin/admin', () => apiMocks);

/*
 * 2026-09-03：OA 密码凭证卡从工作事项页搬到本页，所以本页的测试也要备好它的凭证接口替身，
 * 否则这张卡会去打真实请求。
 */
const credentialMocks = vi.hoisted(() => ({
  bindPassword: vi.fn(),
  getBinding: vi.fn(),
  unbindPassword: vi.fn(),
}));

vi.mock('../../../generated/credential-bindings/credential-bindings', () => ({
  bindPasswordApiV1CredentialBindingsTargetSystemPut: credentialMocks.bindPassword,
  getBindingApiV1CredentialBindingsTargetSystemGet: credentialMocks.getBinding,
  unbindPasswordApiV1CredentialBindingsTargetSystemDelete:
    credentialMocks.unbindPassword,
}));

const bindings: AdminBindingView[] = [
  {
    binding_id: 'binding-1',
    target_system: 'u8',
    execution_identity: 'user_delegated',
    bind_status: 'active',
    binding_scope: 'company-a',
    account_set_id: 'account-set-1',
    device_domain_id: null,
    reason_code: null,
  },
];

const revokedBinding = {
  binding_id: 'binding-1',
  target_system: 'oa',
  execution_identity: 'user_delegated',
  bind_status: 'revoked',
  binding_scope: null,
  account_set_id: null,
  device_domain_id: null,
  reason_code: 'identity_revoked',
} as const;

const revokeResponse: AdminBindingMutationResponse = {
  action: 'revoke',
  binding: revokedBinding,
  changed: true,
  next_action: 'none',
};

const resetResponse: AdminBindingMutationResponse = {
  action: 'reset',
  binding: revokedBinding,
  changed: true,
  next_action: 'reauthenticate',
};

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
          <BindingsPage />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
}

function submitAiUser(aiUserId = 'user-1') {
  fireEvent.change(screen.getByLabelText('ai_user_id'), {
    target: { value: aiUserId },
  });
  fireEvent.click(screen.getByRole('button', { name: /查\s*询/ }));
}

describe('BindingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.listBindings.mockResolvedValue({ ai_user_id: 'user-1', items: bindings });
    apiMocks.revokeBinding.mockResolvedValue(revokeResponse);
    apiMocks.resetBinding.mockResolvedValue(resetResponse);
    credentialMocks.getBinding.mockResolvedValue({
      bound: false,
      poll_failure_count: 0,
      poll_status: 'unbound',
      target_system: 'oa',
      updated_at: null,
    });
  });

  /*
   * 工作事项页删掉页面内凭证卡后，OA 密码的绑定入口必须仍然存在——顶栏系统状态面板的
   * 「重新绑定」就指向本页。把凭证卡搬回工作事项页或整块删掉，这条都会变红。
   */
  it('carries the OA password binding entry that the work-objects page no longer shows', async () => {
    renderPage();

    expect(await screen.findByText('后台同步凭证')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '绑定 OA 密码' }),
    ).toBeInTheDocument();
  });

  it('does not call listBindings when ai_user_id is empty', async () => {
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /查\s*询/ }));

    expect(await screen.findByText('ai_user_id 必填')).toBeInTheDocument();
    expect(apiMocks.listBindings).not.toHaveBeenCalled();
  });

  it('renders Binding rows, response AI user, and key view fields', async () => {
    renderPage();
    submitAiUser();

    expect(await screen.findByText('binding-1')).toBeInTheDocument();
    expect(screen.getByText('绑定：user-1')).toBeInTheDocument();
    expect(screen.getByText('u8')).toBeInTheDocument();
    expect(screen.getByText('user_delegated')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('company-a')).toBeInTheDocument();
  });

  it('calls listBindings with the exact sparse filter shape', async () => {
    renderPage();
    fireEvent.change(screen.getByLabelText('ai_user_id'), {
      target: { value: '  user-exact  ' },
    });
    fireEvent.mouseDown(screen.getByLabelText('target_system'));
    fireEvent.click(
      await screen.findByText('u8', { selector: '.ant-select-item-option-content' }),
    );
    await waitFor(() => {
      expect(
        screen.getByLabelText('target_system').closest('.ant-select-content-has-value'),
      ).toHaveTextContent('u8');
    });
    fireEvent.click(screen.getByRole('button', { name: /查\s*询/ }));

    await waitFor(() => {
      expect(apiMocks.listBindings).toHaveBeenCalledWith({
        ai_user_id: 'user-exact',
        target_system: 'u8',
      });
    });
  });

  it.each([
    ['撤销', '确认撤销此 Binding？', '确认撤销', 'revokeBinding'],
    ['重置', '确认重置此 Binding？', '确认重置', 'resetBinding'],
  ] as const)(
    'confirms %s, calls the generated client, and refreshes the list',
    async (action, title, confirmAction, mutationName) => {
      const otherMutationName =
        mutationName === 'revokeBinding' ? 'resetBinding' : 'revokeBinding';
      apiMocks.listBindings
        .mockResolvedValueOnce({ ai_user_id: 'user-1', items: bindings })
        .mockResolvedValue({
          ai_user_id: 'user-1',
          items: [
            {
              ...bindings[0],
              bind_status: 'revoked',
              reason_code: 'identity_revoked',
            },
          ],
        });
      renderPage();
      submitAiUser();
      await screen.findByText('binding-1');

      fireEvent.click(
        screen.getByRole('button', { name: new RegExp(action.split('').join('\\s*')) }),
      );
      expect(await screen.findByText(title)).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: confirmAction }));

      await waitFor(() => {
        expect(apiMocks[mutationName]).toHaveBeenCalledWith('binding-1');
        expect(apiMocks[otherMutationName]).not.toHaveBeenCalled();
        expect(apiMocks.listBindings).toHaveBeenCalledTimes(2);
        expect(screen.getByText('revoked')).toBeInTheDocument();
        expect(screen.getByText('identity_revoked')).toBeInTheDocument();
      });
    },
  );

  it.each([
    ['撤销', '确认撤销此 Binding？'],
    ['重置', '确认重置此 Binding？'],
  ])('does not call either mutation client when %s confirmation is cancelled', async (action, title) => {
    renderPage();
    submitAiUser();
    await screen.findByText('binding-1');

    fireEvent.click(
      screen.getByRole('button', { name: new RegExp(action.split('').join('\\s*')) }),
    );
    expect(await screen.findByText(title)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /取\s*消/ }));

    expect(apiMocks.revokeBinding).not.toHaveBeenCalled();
    expect(apiMocks.resetBinding).not.toHaveBeenCalled();
  });

  it.each([
    ['撤销', '确认撤销', 404, 'binding_not_found', 'Binding was not found.'],
    [
      '重置',
      '确认重置',
      503,
      'binding_mutation_unavailable',
      'Binding mutation provider is unavailable.',
    ],
  ])(
    'shows the backend code and message when %s fails',
    async (action, confirmAction, status, code, message) => {
      const mutationMock = action === '撤销' ? apiMocks.revokeBinding : apiMocks.resetBinding;
      mutationMock.mockRejectedValueOnce(new ApiError(status, code, message));
      renderPage();
      submitAiUser();
      await screen.findByText('binding-1');

      fireEvent.click(
        screen.getByRole('button', { name: new RegExp(action.split('').join('\\s*')) }),
      );
      fireEvent.click(await screen.findByRole('button', { name: confirmAction }));

      expect(await screen.findByText(`${code}: ${message}`)).toBeInTheDocument();
      expect(mutationMock).toHaveBeenCalledWith('binding-1');
      expect(apiMocks.listBindings).toHaveBeenCalledTimes(1);
    },
  );

  it('disables both mutation actions when binding_id is null', async () => {
    apiMocks.listBindings.mockResolvedValueOnce({
      ai_user_id: 'user-1',
      items: [{ ...bindings[0], binding_id: null }],
    });
    renderPage();
    submitAiUser();
    await screen.findByText('绑定：user-1');

    expect(screen.getByRole('button', { name: /撤\s*销/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /重\s*置/ })).toBeDisabled();
    expect(apiMocks.revokeBinding).not.toHaveBeenCalled();
    expect(apiMocks.resetBinding).not.toHaveBeenCalled();
  });

  it.each([
    [403, 'role_not_allowed', 'Management role is required.'],
    [422, 'binding_query_invalid', 'Binding query parameters are invalid.'],
  ])('shows backend error %s with code %s', async (status, code, message) => {
    apiMocks.listBindings.mockRejectedValueOnce(new ApiError(status, code, message));
    renderPage();
    submitAiUser();

    expect(await screen.findByText(`${code}: ${message}`)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /查\s*询/ }));

    expect(await screen.findByText('binding-1')).toBeInTheDocument();
    expect(apiMocks.listBindings).toHaveBeenCalledTimes(2);
    expect(apiMocks.listBindings).toHaveBeenNthCalledWith(2, { ai_user_id: 'user-1' });
  });
});
