import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App, {
  AuthenticationEffects,
  LoginRoute,
  ProtectedRoute,
} from '../App';
import { useAIDockStore } from '../stores/aiDockStore';
import { useAuthStore } from '../stores/authStore';
import { useNavigationStore } from '../stores/navigationStore';

const apiMocks = vi.hoisted(() => ({
  getBinding: vi.fn(),
}));

vi.mock('../generated/credential-bindings/credential-bindings', () => ({
  getBindingApiV1CredentialBindingsTargetSystemGet: apiMocks.getBinding,
}));

function LocationProbe() {
  const location = useLocation();
  return <div>{`${location.pathname}${location.search}`}</div>;
}

describe('application authentication boundary', () => {
  beforeEach(() => {
    useAuthStore.setState({ generation: 0, status: 'unauthenticated' });
    useAIDockStore.setState({
      contextNotice: null,
      draft: '',
      lastOpenMode: 'drawer',
      mode: 'closed',
      pageContextDeclaration: null,
      sessionContextMode: 'page',
      sessionId: null,
      transcript: [],
    });
    useNavigationStore.setState({ collapsed: false });
    apiMocks.getBinding.mockReset();
    apiMocks.getBinding.mockResolvedValue({
      bound: true,
      poll_failure_count: 0,
      poll_status: 'active',
      target_system: 'oa',
      updated_at: null,
    });
    window.localStorage.clear();
    window.history.pushState({}, '', '/health');
  });

  it(
    'redirects an unauthenticated admin route to login without mounting its page',
    async () => {
      window.history.pushState({}, '', '/admin/registry');
      render(<App />);

      expect(
        await screen.findByRole('heading', { name: '欢迎回来' }),
      ).toBeInTheDocument();
      expect(screen.queryByText('Registry 管理')).not.toBeInTheDocument();
    },
    30_000,
  );

  it('keeps chat protected and returns there after authentication', async () => {
    window.history.pushState({}, '', '/chat');
    render(<App />);

    expect(
      await screen.findByRole('heading', { name: '欢迎回来' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('heading', { name: '把要办的事说清楚' }),
    ).not.toBeInTheDocument();

    act(() => useAuthStore.getState().markAuthenticated());

    expect(
      await screen.findByRole('heading', { name: '把要办的事说清楚' }),
    ).toBeInTheDocument();
    expect(window.location.pathname).toBe('/chat');
  });

  it('allows a protected route only while the in-memory session is authenticated', async () => {
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    render(
      <MemoryRouter initialEntries={['/admin/example']}>
        <Routes>
          <Route path="/login" element={<div>重新认证</div>} />
          <Route element={<ProtectedRoute />}>
            <Route path="/admin/example" element={<div>受保护内容</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText('受保护内容')).toBeInTheDocument();
    act(() => useAuthStore.getState().markUnauthenticated());
    expect(await screen.findByText('重新认证')).toBeInTheDocument();
    expect(screen.queryByText('受保护内容')).not.toBeInTheDocument();
  });

  it('does not re-enter login and honors the requested protected route', async () => {
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    render(
      <MemoryRouter
        initialEntries={[{ pathname: '/login', state: { from: '/admin/tasks' } }]}
      >
        <Routes>
          <Route path="/login" element={<LoginRoute />} />
          <Route path="/admin/tasks" element={<div>受保护目标</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText('受保护目标')).toBeInTheDocument();
    expect(
      screen.queryByRole('heading', { name: '欢迎回来' }),
    ).not.toBeInTheDocument();
  });

  it('preserves the search query through the protected-route login return', async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/search?q=OA-WF-001']}>
          <Routes>
            <Route path="/login" element={<LoginRoute />} />
            <Route element={<ProtectedRoute />}>
              <Route path="/search" element={<LocationProbe />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(
      await screen.findByRole('heading', { name: '欢迎回来' }),
    ).toBeInTheDocument();

    act(() => useAuthStore.getState().markAuthenticated());

    expect(await screen.findByText('/search?q=OA-WF-001')).toBeInTheDocument();
  });

  it('clears private query data when reauthentication is required', async () => {
    const client = new QueryClient();
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    client.setQueryData(['private'], { value: 'cached private response' });
    useAIDockStore.setState({
      sessionId: '11111111-1111-4111-8111-111111111111',
      transcript: [{ role: 'user', text: '上一位用户的内容' }],
    });
    render(
      <QueryClientProvider client={client}>
        <AuthenticationEffects />
      </QueryClientProvider>,
    );

    act(() => useAuthStore.getState().markUnauthenticated());
    await waitFor(() => expect(client.getQueryCache().getAll()).toHaveLength(0));
    expect(useAIDockStore.getState().sessionId).toBeNull();
    expect(useAIDockStore.getState().transcript).toHaveLength(0);
  });

  it('performs local logout through the application shell', async () => {
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    window.history.pushState({}, '', '/');
    render(<App />);

    // 2026-09-02 定稿把「退出登录（本地）」从左导航底部移进顶栏头像的用户菜单。
    fireEvent.click(screen.getByTestId('topbar-avatar'));
    fireEvent.click(screen.getByRole('button', { name: '退出登录（本地）' }));

    expect(useAuthStore.getState().status).toBe('unauthenticated');
  });

  it('sends the bare root to the AI assistant route and keeps one shell for every authenticated route', async () => {
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    window.history.pushState({}, '', '/');
    render(<App />);

    expect(
      await screen.findByRole('heading', { name: '把要办的事说清楚' }),
    ).toBeInTheDocument();
    expect(window.location.pathname).toBe('/chat');
    expect(screen.queryByRole('link', { name: /EternalAI/ })).not.toBeInTheDocument();
    expect(screen.getByTestId('app-brand').tagName).toBe('DIV');

    for (const [name, href] of [
      ['AI 助手', '/chat'],
      ['工作事项', '/work-objects'],
      ['任务交办', '/work-dispatch'],
      ['软件中心', '/apps'],
      ['消息', '/messages'],
      ['功能管理', '/admin/registry'],
      ['任务证据', '/admin/tasks'],
      ['账号绑定', '/admin/bindings'],
    ]) {
      expect(screen.getByRole('link', { name })).toHaveAttribute('href', href);
    }
    expect(screen.queryByText('当前位置')).not.toBeInTheDocument();
    expect(screen.queryByText('开始新工作', { selector: 'strong' })).toBeNull();
  });

  /*
   * 落地页返修后是「一句标题 + 一句原因 + 一句下一步 + 一排按钮」，不再是三个 `region` 分段，所以这里
   * 钉的是**为什么为空这句话真的显示出来了**（2026-08-27「空状态统一规范」的实质），而不是原来的分段
   * 结构。
   */
  it.each([
    ['/work-dispatch', '任务交办', '任务交办还没有开发，这里派不了活，也存不了草稿。'],
    ['/apps', '软件中心', '软件中心还没有开发，这里还看不到、也打不开任何软件。'],
    ['/messages', '消息', '消息功能还没有开发，这里收不到也发不出消息。'],
  ])('mounts the %s landing page inside the shell', async (path, heading, reason) => {
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    window.history.pushState({}, '', path);
    render(<App />);

    expect(
      await screen.findByRole('heading', { level: 1, name: heading }),
    ).toBeInTheDocument();
    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '工作区' })).toBeInTheDocument();
  });
});
