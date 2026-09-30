import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { McpConnectionsPanel } from '../McpConnectionsPanel';
import { useAuthStore } from '../../../stores/authStore';

const api = vi.hoisted(() => ({ services: vi.fn(), connections: vi.fn(), authorize: vi.fn(), disconnect: vi.fn() }));
vi.mock('../../../api/mcp', async (original) => ({
  ...await original<typeof import('../../../api/mcp')>(), listMcpServices: api.services,
  listMcpConnections: api.connections, authorizeMcp: api.authorize, disconnectMcp: api.disconnect,
}));
beforeEach(() => {
  vi.clearAllMocks(); useAuthStore.setState({ generation: 1, status: 'authenticated' });
  api.services.mockResolvedValue([{ service_config_id: 'a', display_name: '业务甲', enabled: true },
    { service_config_id: 'b', display_name: '业务乙', enabled: true }]);
  api.connections.mockResolvedValue([]);
});
describe('MCP connections', () => {
  it.each([undefined, {}, [null], [{ service_config_id: 'a', display_name: '业务甲', enabled: 'true' }]])('fails closed for malformed service response %j', async (response) => {
    api.services.mockResolvedValue(response);
    render(<McpConnectionsPanel />);
    expect(await screen.findByRole('alert')).toHaveTextContent('暂时读不到连接状态');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByText('暂未配置业务服务，请等待管理员提供接入配置。')).not.toBeInTheDocument();
    expect(api.authorize).not.toHaveBeenCalled();
  });
  it('fails closed for malformed connection responses', async () => {
    api.connections.mockResolvedValue([{ connection_id: 'synthetic', service_config_id: 'a', state: 'ACTIVE' }]);
    render(<McpConnectionsPanel />);
    expect(await screen.findByRole('alert')).toHaveTextContent('暂时读不到连接状态');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
  it('keeps service scope and requires an explicit external authorization click', async () => {
    api.authorize.mockResolvedValue({ authorization_url: 'https://provider.invalid/authorize?state=synthetic' });
    render(<McpConnectionsPanel />);
    const buttons = await screen.findAllByRole('button', { name: '连接账号' });
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[1]!);
    expect(api.authorize).toHaveBeenCalledWith('b');
    const link = await screen.findByRole('link', { name: '继续前往授权' });
    expect(link).toHaveAttribute('href', 'https://provider.invalid/authorize?state=synthetic');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });
  it('suppresses late authorization after logout and blocks double clicks', async () => {
    let resolve!: (value: { authorization_url: string }) => void;
    api.authorize.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<McpConnectionsPanel />);
    const button = (await screen.findAllByRole('button', { name: '连接账号' }))[0]!;
    fireEvent.click(button); fireEvent.click(button);
    expect(api.authorize).toHaveBeenCalledTimes(1);
    await act(async () => { useAuthStore.getState().markUnauthenticated(); resolve({ authorization_url: 'https://provider.invalid/late' }); });
    expect(screen.queryByRole('link', { name: '继续前往授权' })).not.toBeInTheDocument();
  });
  it('does not render unsafe links or mistake unavailable data for disconnected', async () => {
    api.authorize.mockResolvedValue({ authorization_url: 'javascript:alert(1)' });
    render(<McpConnectionsPanel />);
    fireEvent.click((await screen.findAllByRole('button', { name: '连接账号' }))[0]!);
    expect(await screen.findByRole('alert')).toHaveTextContent('操作未确认完成');
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
});
