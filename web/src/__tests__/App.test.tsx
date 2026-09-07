import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App, {
  AuthenticationEffects,
  LoginRoute,
  ProtectedRoute,
} from '../App';
import { ApiError } from '../api/mutator';
import type { MeResponse } from '../generated/me/me.schemas';
import { useAIDockStore } from '../stores/aiDockStore';
import { useAuthStore } from '../stores/authStore';
import { useNavigationStore } from '../stores/navigationStore';

const apiMocks = vi.hoisted(() => ({
  getBinding: vi.fn(),
  readMe: vi.fn(),
}));

vi.mock('../generated/credential-bindings/credential-bindings', () => ({
  getBindingApiV1CredentialBindingsTargetSystemGet: apiMocks.getBinding,
}));

vi.mock('../generated/me/me', () => ({
  readMeApiV1MeGet: apiMocks.readMe,
}));

const DISPLAY_NAME = '甲用户';

function meResponse(): MeResponse {
  return {
    authenticated: true,
    display_name: DISPLAY_NAME,
    org: { department_name: '部门乙', department_id: '22' },
    org_status: 'ok',
    avatar_path: '/api/v1/me/avatar',
  };
}

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
    apiMocks.readMe.mockReset();
    apiMocks.readMe.mockResolvedValue(meResponse());
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

  /*
   * 刷新页面时前端还不知道自己登不登录着——会话票据在一个 httpOnly cookie 里，JS 读不到。这一组钉的
   * 就是「先问后端，再决定」：确认返回前既不放行也不踢到登录页，确认失败也不把「连不上后端」翻译成
   * 「你没登录」。
   */
  describe('start-up session confirmation', () => {
    beforeEach(() => {
      useAuthStore.setState({ generation: 0, status: 'unknown' });
    });

    it('neither renders protected content nor redirects while the answer is pending', () => {
      apiMocks.readMe.mockReturnValue(new Promise(() => {}));
      render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter initialEntries={['/admin/example']}>
            <AuthenticationEffects />
            <Routes>
              <Route path="/login" element={<div>登录页</div>} />
              <Route element={<ProtectedRoute />}>
                <Route path="/admin/example" element={<div>受保护内容</div>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

      expect(screen.getByTestId('boot-gate')).toBeInTheDocument();
      expect(screen.queryByText('受保护内容')).not.toBeInTheDocument();
      expect(screen.queryByText('登录页')).not.toBeInTheDocument();
      // 确认进行中一个字都不写：没有 spinner 文案，也没有「正在加载」。
      expect(screen.getByTestId('boot-gate').textContent).toBe('');
    });

    it('does not flash the login form for a session that is still valid', async () => {
      render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter
            initialEntries={[{ pathname: '/login', state: { from: '/chat' } }]}
          >
            <AuthenticationEffects />
            <Routes>
              <Route path="/login" element={<LoginRoute />} />
              <Route path="/chat" element={<div>受保护目标</div>} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

      expect(screen.queryByRole('heading', { name: '欢迎回来' })).not.toBeInTheDocument();
      expect(await screen.findByText('受保护目标')).toBeInTheDocument();
    });

    it('restores the session from the backend answer rather than from stored state', async () => {
      const setItem = vi.spyOn(Storage.prototype, 'setItem');
      render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter initialEntries={['/admin/example']}>
            <AuthenticationEffects />
            <Routes>
              <Route path="/login" element={<div>登录页</div>} />
              <Route element={<ProtectedRoute />}>
                <Route path="/admin/example" element={<div>受保护内容</div>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

      expect(await screen.findByText('受保护内容')).toBeInTheDocument();
      expect(useAuthStore.getState().status).toBe('authenticated');
      // 客户端不保存任何身份断言：唯一被保存的是浏览器里那份读不到也伪造不了的 cookie。
      for (const [key, value] of setItem.mock.calls) {
        expect(`${key}${String(value)}`).not.toContain(DISPLAY_NAME);
        expect(`${key}${String(value)}`).not.toContain('authenticated');
      }
      setItem.mockRestore();
    });

    it('sends the user to login when the backend says the session is gone', async () => {
      apiMocks.readMe.mockImplementation(async () => {
        useAuthStore.getState().markUnauthenticated(0);
        throw new ApiError(401, 'authentication_required', 'Authentication is required.');
      });
      render(
        <QueryClientProvider
          client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
        >
          <MemoryRouter initialEntries={['/admin/example']}>
            <AuthenticationEffects />
            <Routes>
              <Route path="/login" element={<div>登录页</div>} />
              <Route element={<ProtectedRoute />}>
                <Route path="/admin/example" element={<div>受保护内容</div>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

      expect(await screen.findByText('登录页')).toBeInTheDocument();
      expect(screen.queryByText('受保护内容')).not.toBeInTheDocument();
    });

    it('stays put when the backend cannot be reached instead of asking for the password again', async () => {
      apiMocks.readMe.mockRejectedValue(new TypeError('Failed to fetch'));
      render(
        <QueryClientProvider
          client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
        >
          <MemoryRouter initialEntries={['/admin/example']}>
            <AuthenticationEffects />
            <Routes>
              <Route path="/login" element={<div>登录页</div>} />
              <Route element={<ProtectedRoute />}>
                <Route path="/admin/example" element={<div>受保护内容</div>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

      /*
       * 把「连不上后端」翻译成「你没登录」会在每次网络抖动时把用户推到登录页，训练他们在异常状态下
       * 反复输入密码——那是钓鱼形状的习惯。
       */
      expect(await screen.findByTestId('boot-gate-unreachable')).toBeInTheDocument();
      expect(screen.queryByText('登录页')).not.toBeInTheDocument();
      expect(screen.queryByText('受保护内容')).not.toBeInTheDocument();
      expect(useAuthStore.getState().status).toBe('unknown');
    });

    it('offers one line and one retry button when the backend is unreachable', async () => {
      apiMocks.readMe.mockRejectedValue(new TypeError('Failed to fetch'));
      render(
        <QueryClientProvider
          client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
        >
          <MemoryRouter initialEntries={['/admin/example']}>
            <AuthenticationEffects />
            <Routes>
              <Route element={<ProtectedRoute />}>
                <Route path="/admin/example" element={<div>受保护内容</div>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

      const gate = await screen.findByTestId('boot-gate-unreachable');
      expect(gate).toHaveTextContent('连不上服务器');
      // 一行加一个按钮，不解释原因、不给排查步骤、不给联系方式。
      expect(within(gate).getAllByRole('button')).toHaveLength(1);
      expect(gate.textContent).toBe('连不上服务器重试');

      apiMocks.readMe.mockResolvedValue(meResponse());
      fireEvent.click(within(gate).getByRole('button', { name: '重试' }));

      expect(await screen.findByText('受保护内容')).toBeInTheDocument();
    });
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
      screen.queryByRole('heading', { level: 1, name: 'AI 助手' }),
    ).not.toBeInTheDocument();

    act(() => useAuthStore.getState().markAuthenticated());

    expect(
      await screen.findByRole('heading', { level: 1, name: 'AI 助手' }),
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

    // 2026-09-02 定稿把「退出登录」从左导航底部移进顶栏头像的用户菜单（画板 `TopPops.dc.html`）。
    fireEvent.click(screen.getByTestId('topbar-avatar'));
    fireEvent.click(screen.getByRole('button', { name: /退出登录/ }));

    expect(useAuthStore.getState().status).toBe('unauthenticated');
  });

  it('sends the bare root to the AI assistant route and keeps one shell for every authenticated route', async () => {
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    window.history.pushState({}, '', '/');
    render(<App />);

    expect(
      await screen.findByRole('heading', { level: 1, name: 'AI 助手' }),
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

  /*
   * 任务交办与软件中心不再是占位页：前者是九类字段的交办草稿表单，后者是业务系统卡片列表。这里只钉
   * 「路由挂的是那一页、且仍在同一个外壳里」，页面自身的合同各由自己的测试文件承担。
   */
  it('mounts the dispatch draft form at /work-dispatch instead of a placeholder', async () => {
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    window.history.pushState({}, '', '/work-dispatch');
    render(<App />);

    expect(
      await screen.findByRole('heading', { level: 1, name: '任务交办' }),
    ).toBeInTheDocument();
    expect(screen.getByText('草稿尚未发布')).toBeInTheDocument();
    expect(screen.getByLabelText('截止时间')).toHaveAttribute(
      'type',
      'datetime-local',
    );
    expect(
      screen.queryByText('任务交办还没有开发，这里派不了活，也存不了草稿。'),
    ).not.toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '工作区' })).toBeInTheDocument();
  });
});
