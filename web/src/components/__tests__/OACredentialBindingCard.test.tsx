import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CredentialBindingView } from '../../generated/credential-bindings/credential-bindings.schemas';
import { useAuthStore } from '../../stores/authStore';
import OACredentialBindingCard from '../OACredentialBindingCard';

/*
 * 2026-09-03：工作事项页按画板去掉了页面内的凭证卡，这张卡改挂在「账号绑定」页上。
 * 原来写在 `pages/__tests__/WorkObjectsPage.test.tsx` 里的两条凭证断言原样搬到这里——
 * 明文清除与终态提示是这张卡自己的合同，不该跟着宿主页面走。
 */
const apiMocks = vi.hoisted(() => ({
  bindPassword: vi.fn(),
  getBinding: vi.fn(),
  unbindPassword: vi.fn(),
}));

vi.mock('../../generated/credential-bindings/credential-bindings', () => ({
  bindPasswordApiV1CredentialBindingsTargetSystemPut: apiMocks.bindPassword,
  getBindingApiV1CredentialBindingsTargetSystemGet: apiMocks.getBinding,
  unbindPasswordApiV1CredentialBindingsTargetSystemDelete:
    apiMocks.unbindPassword,
}));

const UNBOUND_CREDENTIAL: CredentialBindingView = {
  bound: false,
  poll_failure_count: 0,
  poll_status: 'unbound',
  target_system: 'oa',
  updated_at: null,
};

function renderCard() {
  const queryClient = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
  return render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <OACredentialBindingCard />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
}

describe('OACredentialBindingCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    apiMocks.getBinding.mockResolvedValue(UNBOUND_CREDENTIAL);
    apiMocks.bindPassword.mockResolvedValue({
      ...UNBOUND_CREDENTIAL,
      bound: true,
      poll_status: 'active',
      updated_at: '2026-08-21T03:00:00Z',
    });
    apiMocks.unbindPassword.mockResolvedValue(UNBOUND_CREDENTIAL);
  });

  it('clears plaintext fields immediately while an OA binding request is pending', async () => {
    let resolveBinding!: (binding: CredentialBindingView) => void;
    const pendingBinding = new Promise<CredentialBindingView>((resolve) => {
      resolveBinding = resolve;
    });
    apiMocks.bindPassword.mockReset().mockReturnValueOnce(pendingBinding);
    renderCard();

    expect(await screen.findByText('尚未绑定')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '绑定 OA 密码' }));
    const loginId = screen.getByLabelText('OA 登录标识');
    const password = screen.getByLabelText('OA 密码');
    fireEvent.change(loginId, { target: { value: 'LOGIN-ID-CANARY' } });
    fireEvent.change(password, { target: { value: 'PASSWORD-CANARY' } });
    fireEvent.click(screen.getByRole('button', { name: '验证并保存' }));

    await waitFor(() => {
      expect(apiMocks.bindPassword).toHaveBeenCalledWith('oa', {
        login_id: 'LOGIN-ID-CANARY',
        password: 'PASSWORD-CANARY',
      });
    });
    await waitFor(() => {
      expect(screen.getByLabelText('OA 登录标识')).toHaveValue('');
      expect(screen.getByLabelText('OA 密码')).toHaveValue('');
    });
    expect(screen.queryByText('PASSWORD-CANARY')).not.toBeInTheDocument();

    await act(async () => {
      resolveBinding({
        ...UNBOUND_CREDENTIAL,
        bound: true,
        poll_status: 'active',
        updated_at: '2026-08-21T03:00:00Z',
      });
      await pendingBinding;
    });
    expect(await screen.findByText('后台轮询已启用')).toBeInTheDocument();
  });

  it.each([
    ['invalid', '密码已失效，需重新绑定'],
    ['captcha_required', 'OA 要求验证码，轮询已停止'],
  ] as const)('shows the terminal %s state as a rebind warning', async (status, label) => {
    apiMocks.getBinding.mockResolvedValueOnce({
      ...UNBOUND_CREDENTIAL,
      bound: true,
      poll_status: status,
      updated_at: '2026-08-21T03:00:00Z',
    });

    renderCard();

    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新绑定' })).toBeInTheDocument();
  });
});
