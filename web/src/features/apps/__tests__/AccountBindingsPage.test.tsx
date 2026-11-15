import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAuthStore } from '../../../stores/authStore';
import AccountBindingsPage from '../AccountBindingsPage';

/*
 * 本轮账号绑定页改为五个系统的具名分节，账号设置直接展开；业务服务连接在综合业务平台内，原操作在末尾。
 * 原有分节顺序与两个 MCP 面板的挂载意图保留，改查实际的 section 可访问名称。
 */
const apiMocks = vi.hoisted(() => ({ getBinding: vi.fn(), browserBinding: vi.fn(), mcpServices: vi.fn(), mcpConnections: vi.fn(), mcpOperations: vi.fn() }));
vi.mock('../../../api/mcp', () => ({
  listMcpServices: apiMocks.mcpServices, listMcpConnections: apiMocks.mcpConnections,
  listMcpOperations: apiMocks.mcpOperations,
  safeMcpLink: () => undefined,
}));

vi.mock('../../../generated/credential-bindings/credential-bindings', () => ({
  getBindingApiV1CredentialBindingsTargetSystemGet: apiMocks.getBinding,
}));
vi.mock('../../../api/browserBinding', async (original) => ({
  ...await original<typeof import('../../../api/browserBinding')>(),
  getBrowserBindingStatus: apiMocks.browserBinding,
}));

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.mcpServices.mockResolvedValue([]);
  apiMocks.mcpOperations.mockResolvedValue([]);
  apiMocks.mcpConnections.mockResolvedValue([]);
  apiMocks.getBinding.mockReset().mockImplementation((target: string) => Promise.resolve({
    bound: true, poll_failure_count: 0, poll_status: 'active', target_system: target, updated_at: null,
  }));
  apiMocks.browserBinding.mockResolvedValue({
    target_system: 'oa', binding_id: null, binding_revision: null, binding_state: 'unbound', verified_at: null,
  });
  useAuthStore.getState().markAuthenticated();
});

describe('AccountBindingsPage', () => {
  it('locks the ordered binding sections, including both business-service panels', async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
    render(
      <ConfigProvider>
        <AntApp>
          <QueryClientProvider client={client}>
            <MemoryRouter initialEntries={['/settings/bindings']}>
              <AccountBindingsPage />
            </MemoryRouter>
          </QueryClientProvider>
        </AntApp>
      </ConfigProvider>,
    );

    expect(screen.getByRole('heading', { level: 1, name: '账号绑定' })).toBeInTheDocument();
    expect(screen.getAllByRole('region').map((node) => node.getAttribute('aria-label')))
      .toEqual(['OA 办公系统', '财务软件', '综合业务平台', '业务服务连接', '数据中台', '执法办案平台', '查看原操作']);
    expect(within(screen.getByRole('region', { name: '综合业务平台' })).getByRole('region', { name: '业务服务连接' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '查看原操作' })).toBeInTheDocument();
    await waitFor(() => expect(client.isFetching()).toBe(0));
  });
});
