import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAuthStore } from '../../../stores/authStore';
import AccountBindingsPage from '../AccountBindingsPage';

/*
 * 2026-11-14：OA 账号卡与 MCP 两个面板从软件中心搬到「设置 › 账号绑定」。原来在 `AppsPage.test.tsx`
 * 里钉的分节顺序（「业务服务连接」「查看原操作」两块确实挂在页面上、且按此顺序）随面板一起迁到这里；
 * 各面板自己的合同仍由各自的测试文件承担。
 */
const apiMocks = vi.hoisted(() => ({ getBinding: vi.fn(), mcpServices: vi.fn(), mcpConnections: vi.fn(), mcpOperations: vi.fn() }));
vi.mock('../../../api/mcp', () => ({
  listMcpServices: apiMocks.mcpServices, listMcpConnections: apiMocks.mcpConnections,
  listMcpOperations: apiMocks.mcpOperations,
  safeMcpLink: () => undefined,
}));

vi.mock('../../../generated/credential-bindings/credential-bindings', () => ({
  getBindingApiV1CredentialBindingsTargetSystemGet: apiMocks.getBinding,
}));

beforeEach(() => {
  apiMocks.mcpServices.mockResolvedValue([]);
  apiMocks.mcpOperations.mockResolvedValue([]);
  apiMocks.mcpConnections.mockResolvedValue([]);
  apiMocks.getBinding.mockReset().mockResolvedValue({
    bound: true, poll_failure_count: 0, poll_status: 'active', target_system: 'oa', updated_at: null,
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
    expect(screen.getAllByRole('heading', { level: 2 }).map((node) => node.textContent))
      .toEqual(['OA 办公系统', '业务服务连接', '查看原操作']);
    expect(await screen.findByRole('region', { name: '业务服务连接' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '查看原操作' })).toBeInTheDocument();
  });
});
