import { assertLogoutCache, trackAuthGenerations } from '../test/logoutCache';
import { lazy } from 'react';
import type { ComponentType } from 'react';
import { renderHook } from '@testing-library/react';
import { useDraftSession } from '../stores/sessionDraftStore';
import { loadDraft, saveDraft, parseDraft } from '../features/work-dispatch/dispatchDraft';
import { loadNewSoftwareDraft, saveNewSoftwareDraft, parseNewSoftwareDraft } from '../features/apps/newSoftwareDraft';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest';
import App, {
  AuthenticationEffects,
  LoginRoute,
  ProtectedRoute,
} from '../App';
import { ApiError, customInstance } from '../api/mutator';
import type { MeResponse } from '../generated/me/me.schemas';
import { useAIDockStore } from '../stores/aiDockStore';
import { useAuthStore } from '../stores/authStore';
import { useNavigationStore } from '../stores/navigationStore';
import { lazyRouteComponents } from '../app/lazyRoutes';
import type { LazyRouteComponents } from '../app/lazyRoutes';
import { AuthenticatedAppShell } from '../app/AppShell';
import MessagesPage from '../features/messages/MessagesPage';
import WorkDispatchPage from '../features/work-dispatch/WorkDispatchPage';

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

async function settleLazyModules(): Promise<void> {
  await act(async () => {
    await vi.dynamicImportSettled();
  });
}

async function renderProductionApplication() {
  const mounted = render(<App />);
  await settleLazyModules();
  return mounted;
}

function StaticChatPage() {
  return <h1>AI 助手</h1>;
}

function staticAppRoutes(
  overrides: Partial<LazyRouteComponents> = {},
): LazyRouteComponents {
  return {
    ...lazyRouteComponents,
    LazyAuthenticatedAppShell: AuthenticatedAppShell,
    LazyChatPage: StaticChatPage,
    ...overrides,
  };
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
      await renderProductionApplication();

      expect(
        await screen.findByRole('heading', { name: '欢迎回来' }),
      ).toBeInTheDocument();
      expect(screen.queryByText('Registry 管理')).not.toBeInTheDocument();
    },
    30_000,
  );

  it('keeps chat protected and returns there after authentication', async () => {
    function RouteOutlet() {
      return <Outlet />;
    }
    const routes = {
      ...lazyRouteComponents,
      LazyAuthenticatedAppShell: RouteOutlet,
      LazyChatPage: () => <h1>AI 助手</h1>,
      LazyLoginPage: () => <h1>欢迎回来</h1>,
    };
    window.history.pushState({}, '', '/chat');
    render(<App routes={routes} />);

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
          <Route
            path="/login"
            element={<LoginRoute LoginPageComponent={() => <h1>欢迎回来</h1>} />}
          />
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
            <Route
              path="/login"
              element={<LoginRoute LoginPageComponent={() => <h1>欢迎回来</h1>} />}
            />
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
    const generations = trackAuthGenerations(useAuthStore);
    onTestFinished(generations.stop);
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
    await waitFor(() => {
      for (const query of client.getQueryCache().getAll()) expect(query.state.data).toBeUndefined();
      expect(client.getQueryCache().findAll({ queryKey: ['private'] })).toEqual([]);
      expect(client.getQueryCache().findAll({ queryKey: ['me', 1] })).toEqual([]);
      assertLogoutCache(client, { generations, phase: 'unauthenticated', currentGeneration: useAuthStore.getState().generation });
    });
    expect(useAIDockStore.getState().sessionId).toBeNull();
    expect(useAIDockStore.getState().transcript).toHaveLength(0);
  });

  it('clears_both_drafts_through_shell_logout_and_reauthentication', async () => {
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    window.history.pushState({}, '', '/');
    render(
      <App
        routes={staticAppRoutes({ LazyWorkDispatchPage: WorkDispatchPage })}
      />,
    );

    const a = draftToken();
    saveDraft(parseDraft({ title: 'A-private' }), a);
    saveNewSoftwareDraft(parseNewSoftwareDraft({ name: 'A-private' }), a);
    // 2026-09-02 定稿把「退出登录」从左导航底部移进顶栏头像的用户菜单（画板 `TopPops.dc.html`）。
    fireEvent.click(screen.getByTestId('topbar-avatar'));
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ authenticated: false }), { status: 200 }));
    fireEvent.click(screen.getByRole('button', { name: /退出登录/ }));
    await waitFor(() => expect(useAuthStore.getState().status).toBe('unauthenticated'));
    fetchSpy.mockRestore();
    expect(loadDraft(a).title).toBe('');
    expect(loadNewSoftwareDraft(a).name).toBe('');
    act(() => useAuthStore.getState().markAuthenticated());
    const b = draftToken();
    expect(loadDraft(b).title).toBe('');
    expect(loadNewSoftwareDraft(b).name).toBe('');
    fireEvent.click(await screen.findByRole('link', { name: '任务交办' }));
    expect(await screen.findByLabelText('标题')).toHaveValue('');
  });

  it.each([403, 503])('logout_preserves_draft_isolation_contract after %s', async (status) => {
    useAuthStore.getState().markAuthenticated();
    window.history.pushState({}, '', '/');
    const mounted = render(<App routes={staticAppRoutes()} />);
    const token = draftToken();
    expect(saveDraft(parseDraft({ title: 'Synthetic saved task' }), token)).toBe(true);
    expect(saveNewSoftwareDraft(parseNewSoftwareDraft({ name: 'Synthetic saved software' }), token)).toBe(true);
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(
      JSON.stringify({ detail: { code: 'logout_unavailable', message: 'Synthetic failure' } }), { status },
    )).mockResolvedValueOnce(new Response(JSON.stringify({ authenticated: false })));
    try {
      fireEvent.click(screen.getByTestId('topbar-avatar'));
      fireEvent.click(screen.getByRole('button', { name: /退出登录/ }));
      expect(await screen.findByText('退出未完成，请重试')).toBeInTheDocument();
      expect(useAuthStore.getState().status).toBe('authenticated');
      expect(loadDraft(token).title).toBe('Synthetic saved task');
      expect(loadNewSoftwareDraft(token).name).toBe('Synthetic saved software');
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      fireEvent.click(screen.getByRole('button', { name: /退出登录/ }));
      await waitFor(() => expect(useAuthStore.getState().status).toBe('unauthenticated'));
      expect(loadDraft(token).title).toBe('');
      expect(loadNewSoftwareDraft(token).name).toBe('');
      expect(saveDraft(parseDraft({ title: 'Late old task' }), token)).toBe(false);
      expect(saveNewSoftwareDraft(parseNewSoftwareDraft({ name: 'Late old software' }), token)).toBe(false);
    } finally {
      mounted.unmount();
      fetchSpy.mockRestore();
    }
  });

  it('clears_drafts_through_a_current_generation_fetch_401', async () => {
    useAuthStore.getState().markAuthenticated();
    window.history.pushState({}, '', '/work-dispatch');
    const mounted = render(
      <App
        routes={staticAppRoutes({ LazyWorkDispatchPage: WorkDispatchPage })}
      />,
    );
    const a = draftToken();
    expect(saveDraft(parseDraft({ title: 'A-private' }), a)).toBe(true);
    expect(saveNewSoftwareDraft(parseNewSoftwareDraft({ name: 'A-private' }), a)).toBe(true);
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({ ok: false, status: 401 } as Response);
    try {
      await act(async () => {
        await expect(customInstance({ url: '/api/v1/me', method: 'GET' })).rejects.toMatchObject({ code: 'authentication_required', status: 401 });
      });
      expect(window.location.pathname).toBe('/login');
      expect(useAuthStore.getState().status).toBe('unauthenticated');
      expect(loadDraft(a).title).toBe('');
      expect(loadNewSoftwareDraft(a).name).toBe('');
      expect(screen.queryByLabelText('标题')).toBeNull();
      act(() => useAuthStore.getState().markAuthenticated());
      const b = draftToken();
      expect(loadDraft(b).title).toBe('');
      expect(loadNewSoftwareDraft(b).name).toBe('');
    } finally {
      mounted.unmount();
      fetchSpy.mockRestore();
    }
  });

  it('shows a local route fallback until an authenticated lazy page resolves', async () => {
    let loaderCalls = 0;
    let releaseChat!: (module: { default: ComponentType }) => void;
    const ControlledChatPage = lazy(
      () =>
        new Promise<{ default: ComponentType }>((resolve) => {
          loaderCalls += 1;
          releaseChat = resolve;
        }),
    );
    function RouteOutlet() {
      return <Outlet />;
    }
    const routes = {
      ...lazyRouteComponents,
      LazyAuthenticatedAppShell: RouteOutlet,
      LazyChatPage: ControlledChatPage,
    };

    useAuthStore.getState().markAuthenticated();
    window.history.pushState({}, '', '/chat');
    render(<App routes={routes} />);

    await waitFor(() => expect(loaderCalls).toBe(1));
    expect(screen.getByTestId('lazy-page-loading')).toHaveTextContent('正在打开页面');

    await act(async () => {
      releaseChat({ default: () => <h1>AI 助手</h1> });
      await Promise.resolve();
    });

    expect(
      await screen.findByRole('heading', { level: 1, name: 'AI 助手' }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('lazy-page-loading')).not.toBeInTheDocument();
    expect(loaderCalls).toBe(1);
  });

  it('does not start a protected lazy loader before session confirmation', () => {
    let loaderCalls = 0;
    const ProtectedPage = lazy(() => {
      loaderCalls += 1;
      return Promise.resolve({ default: () => <h1>受保护的延迟页面</h1> });
    });
    function RouteOutlet() {
      return <Outlet />;
    }
    const routes = {
      ...lazyRouteComponents,
      LazyAuthenticatedAppShell: RouteOutlet,
      LazyChatPage: ProtectedPage,
    };

    apiMocks.readMe.mockReturnValue(new Promise(() => {}));
    useAuthStore.setState({ generation: 0, status: 'unknown' });
    window.history.pushState({}, '', '/chat');
    render(<App routes={routes} />);

    expect(screen.getByTestId('boot-gate')).toBeInTheDocument();
    expect(screen.queryByTestId('lazy-page-loading')).not.toBeInTheDocument();
    expect(loaderCalls).toBe(0);
  });

  it('clears state and never revives a protected lazy page when its loader resolves after a 401', async () => {
    let loaderCalls = 0;
    let releaseChat!: (module: { default: ComponentType }) => void;
    const LateChatPage = lazy(
      () =>
        new Promise<{ default: ComponentType }>((resolve) => {
          loaderCalls += 1;
          releaseChat = resolve;
        }),
    );
    function RouteOutlet() {
      return <Outlet />;
    }
    const routes = {
      ...lazyRouteComponents,
      LazyAuthenticatedAppShell: RouteOutlet,
      LazyChatPage: LateChatPage,
    };

    useAuthStore.getState().markAuthenticated();
    useAIDockStore.setState({
      draft: '上一位用户尚未发送的内容',
      sessionId: '11111111-1111-4111-8111-111111111111',
      transcript: [{ role: 'user', text: '上一位用户的私有会话' }],
    });
    window.history.pushState({}, '', '/chat');
    const mounted = render(<App routes={routes} />);
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 401,
    } as Response);
    try {
      await waitFor(() => expect(loaderCalls).toBe(1));
      expect(screen.getByTestId('lazy-page-loading')).toHaveTextContent('正在打开页面');

      await act(async () => {
        await expect(
          customInstance({ url: '/api/v1/me', method: 'GET' }),
        ).rejects.toMatchObject({ code: 'authentication_required', status: 401 });
      });

      await waitFor(() => {
        expect(useAuthStore.getState().status).toBe('unauthenticated');
        expect(window.location.pathname).toBe('/login');
      });
      expect(useAIDockStore.getState().draft).toBe('');
      expect(useAIDockStore.getState().sessionId).toBeNull();
      expect(useAIDockStore.getState().transcript).toHaveLength(0);

      await act(async () => {
        releaseChat({ default: () => <h1>迟到的受保护页面</h1> });
        await Promise.resolve();
      });

      expect(loaderCalls).toBe(1);
      expect(
        screen.queryByRole('heading', { name: '迟到的受保护页面' }),
      ).not.toBeInTheDocument();
    } finally {
      mounted.unmount();
      fetchSpy.mockRestore();
    }
  });

  it('shows one local refresh exit instead of retrying a failed route download', async () => {
    let loaderCalls = 0;
    const FailedPage = lazy(() => {
      loaderCalls += 1;
      return Promise.reject(new Error('Synthetic route module failure'));
    });
    function RouteOutlet() {
      return <Outlet />;
    }
    const routes = {
      ...lazyRouteComponents,
      LazyAuthenticatedAppShell: RouteOutlet,
      LazyChatPage: FailedPage,
    };
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    useAuthStore.getState().markAuthenticated();
    window.history.pushState({}, '', '/chat');
    const mounted = render(<App routes={routes} />);
    try {
      const failure = await screen.findByRole('alert');
      expect(failure).toHaveTextContent('正在打开页面加载失败。');
      expect(within(failure).getByRole('button', { name: '刷新' })).toBeInTheDocument();
      expect(loaderCalls).toBe(1);
    } finally {
      mounted.unmount();
      consoleError.mockRestore();
    }
  });

  it('sends the bare root to the AI assistant route and keeps one shell for every authenticated route', async () => {
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    window.history.pushState({}, '', '/');
    render(<App routes={staticAppRoutes()} />);

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
    render(<App routes={staticAppRoutes({ LazyMessagesPage: MessagesPage })} />);

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
    render(
      <App
        routes={staticAppRoutes({ LazyWorkDispatchPage: WorkDispatchPage })}
      />,
    );

    expect(
      await screen.findByRole('heading', { level: 1, name: '任务交办' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText('刷新前如已点过发布：结果待确认，请先到工作事项核对。草稿仅在本次登录期间暂存，刷新或关闭页面会丢失。'),
    ).toBeInTheDocument();
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

function draftToken() { const hook = renderHook(useDraftSession); const value = hook.result.current; hook.unmount(); return value; }


function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

class SyntheticAuthChannel {
  static instances: SyntheticAuthChannel[] = [];
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
  closed = false;
  constructor(public name: string) { SyntheticAuthChannel.instances.push(this); }
  postMessage() {}
  close() { this.closed = true; }
  static notify() {
    for (const channel of this.instances) {
      if (!channel.closed) channel.onmessage?.({ data: 'recheck-identity' } as MessageEvent);
    }
  }
}

function seededPrivateKeys(g: number) {
  return [['private'], ['private', 'unobserved'], ['me', g],
    ['work-objects', g, 'list', 'active', 'active'],
    ['work-objects', g, 'list', 'unconfirmed', ''],
    ['work-objects', g, 'list', 'active', 'completed'],
    ['work-objects', g, 'detail', 'synthetic-a'],
    ['work-objects', g, 'search', 'all', 'synthetic'],
    ['credential-binding', g, 'oa'], ['admin', 'registry']];
}

describe('logout cache isolation with real transport', () => {
  beforeEach(async () => {
    vi.restoreAllMocks();
    SyntheticAuthChannel.instances = [];
    vi.stubGlobal('BroadcastChannel', SyntheticAuthChannel);
    useAuthStore.setState({ generation: 40, status: 'authenticated' });
    const actual = await vi.importActual<typeof import('../generated/me/me')>('../generated/me/me');
    apiMocks.readMe.mockReset();
    apiMocks.readMe.mockImplementation(actual.readMeApiV1MeGet);
  });

  it('logout_cache_has_no_previous_identity_data_after_401_and_late_results', async () => {
    const generations = trackAuthGenerations(useAuthStore);
    onTestFinished(generations.stop);
    const late = deferred<Response>();
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockReturnValueOnce(late.promise)
      .mockResolvedValueOnce(new Response('', { status: 401 }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    for (const key of seededPrivateKeys(40).filter((k) => k[0] !== 'me')) client.setQueryData(key, { owner: 'A' });
    const events: { type: string; key: readonly unknown[] }[] = [];
    const unsubscribe = client.getQueryCache().subscribe((event) => events.push({ type: event.type, key: event.query.queryKey }));
    const mounted = render(<QueryClientProvider client={client}><AuthenticationEffects /></QueryClientProvider>);
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    await act(async () => { SyntheticAuthChannel.notify(); });
    await waitFor(() => expect(useAuthStore.getState().status).toBe('unauthenticated'));
    assertLogoutCache(client, { generations, phase: 'unauthenticated', currentGeneration: useAuthStore.getState().generation });
    const current = client.getQueryCache().find({ queryKey: ['me', 41], exact: true });
    expect(current).toBeDefined();
    expect(current?.isActive()).toBe(false);
    await act(async () => { late.resolve(new Response(JSON.stringify(meResponse()))); });
    assertLogoutCache(client, { generations, phase: 'unauthenticated', currentGeneration: useAuthStore.getState().generation });
    mounted.rerender(<QueryClientProvider client={client}><AuthenticationEffects /></QueryClientProvider>);
    assertLogoutCache(client, { generations, phase: 'unauthenticated', currentGeneration: useAuthStore.getState().generation });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(events.some((e) => e.type === 'removed' && e.key[0] === 'private')).toBe(true);
    expect(events.some((e) => e.type === 'added' && e.key[0] === 'me' && e.key[1] === 41)).toBe(true);
    unsubscribe(); mounted.unmount(); fetchSpy.mockRestore();
  });

  it('new_identity_empty_query_is_allowed_but_old_identity_data_is_rejected', async () => {
    const generations = trackAuthGenerations(useAuthStore);
    onTestFinished(generations.stop);
    const old = deferred<Response>();
    const fresh = deferred<Response>();
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(new Response('', { status: 401 })).mockReturnValueOnce(fresh.promise);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    for (const key of seededPrivateKeys(40).filter((k) => k[0] !== 'me')) client.setQueryData(key, { owner: 'A' });
    const mounted = render(<QueryClientProvider client={client}><AuthenticationEffects /></QueryClientProvider>);
    await act(async () => { SyntheticAuthChannel.notify(); });
    await waitFor(() => expect(useAuthStore.getState().status).toBe('unauthenticated'));
    assertLogoutCache(client, { generations, phase: 'unauthenticated', currentGeneration: useAuthStore.getState().generation });
    act(() => useAuthStore.getState().markAuthenticated());
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(3));
    expect(client.getQueryCache().find({ queryKey: ['me', 42], exact: true })).toBeDefined();
    assertLogoutCache(client, { generations, phase: 'identity-pending', currentGeneration: useAuthStore.getState().generation });
    const b = { ...meResponse(), display_name: 'Synthetic B' };
    await act(async () => { fresh.resolve(new Response(JSON.stringify(b))); });
    await waitFor(() => expect(client.getQueryData(['me', 42])).toEqual(b));
    assertLogoutCache(client, { generations, phase: 'identity-ready', currentGeneration: useAuthStore.getState().generation });
    for (const query of client.getQueryCache().getAll()) {
      if (query.state.data !== undefined) {
        expect(query.queryKey).toEqual(['me', 42]);
        expect(query.state.data).toEqual(b);
      }
    }
    await act(async () => { old.resolve(new Response(JSON.stringify(meResponse()))); });
    assertLogoutCache(client, { generations, phase: 'identity-ready', currentGeneration: useAuthStore.getState().generation });
    for (const query of client.getQueryCache().getAll()) {
      if (query.state.data !== undefined) {
        expect(query.queryKey).toEqual(['me', 42]);
        expect(query.state.data).toEqual(b);
      }
    }
    mounted.unmount(); fetchSpy.mockRestore();
  });

  it('late_revalidation_failure_does_not_pollute_new_identity', async () => {
    const generations = trackAuthGenerations(useAuthStore);
    onTestFinished(generations.stop);
    const late = deferred<Response>();
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(meResponse())))
      .mockReturnValueOnce(late.promise)
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...meResponse(), display_name: 'Synthetic B' })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const mounted = render(<QueryClientProvider client={client}><AuthenticationEffects /></QueryClientProvider>);
    await waitFor(() => expect(client.getQueryData(['me', 40])).toBeDefined());
    act(() => SyntheticAuthChannel.notify());
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    act(() => { useAuthStore.getState().markUnauthenticated(); useAuthStore.getState().markAuthenticated(); });
    await waitFor(() => expect(client.getQueryData(['me', 42])).toMatchObject({ display_name: 'Synthetic B' }));
    await act(async () => { late.resolve(new Response(JSON.stringify({ detail: { code: 'authentication_unavailable', message: 'Synthetic failure' } }), { status: 503 })); });
    expect(screen.queryByText('暂时无法确认登录状态')).not.toBeInTheDocument();
    expect(useAuthStore.getState().status).toBe('authenticated');
    assertLogoutCache(client, { generations, phase: 'identity-ready', currentGeneration: useAuthStore.getState().generation });
    for (const query of client.getQueryCache().getAll()) {
      if (query.state.data !== undefined) {
        expect(query.queryKey).toEqual(['me', 42]);
        expect(query.state.data).toEqual({ ...meResponse(), display_name: 'Synthetic B' });
      }
    }
    mounted.unmount();
    act(() => SyntheticAuthChannel.notify());
    expect(fetchSpy).toHaveBeenCalledTimes(3);
    fetchSpy.mockRestore();
  });

  it('batched_logout_and_login_do_not_reuse_previous_generation', async () => {
    const generations = trackAuthGenerations(useAuthStore);
    onTestFinished(generations.stop);
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(meResponse())));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    for (const key of seededPrivateKeys(40)) client.setQueryData(key, { owner: 'A' });
    const mounted = render(<QueryClientProvider client={client}><AuthenticationEffects /></QueryClientProvider>);
    act(() => { useAuthStore.getState().markUnauthenticated(); useAuthStore.getState().markAuthenticated(); });
    await waitFor(() => expect(client.getQueryData(['me', 42])).toEqual(meResponse()));
    assertLogoutCache(client, { generations, phase: 'identity-ready', currentGeneration: useAuthStore.getState().generation });
    for (const query of client.getQueryCache().getAll()) {
      if (query.state.data !== undefined) {
        expect(query.queryKey).toEqual(['me', 42]);
        expect(query.state.data).toEqual(meResponse());
      }
    }
    mounted.unmount(); fetchSpy.mockRestore();
  });

  it('logout_refresh_and_other_tab_revalidate_with_server', async () => {
    const generations = trackAuthGenerations(useAuthStore);
    onTestFinished(generations.stop);
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(JSON.stringify(meResponse())));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    for (const key of seededPrivateKeys(40)) client.setQueryData(key, meResponse());
    const mounted = render(<QueryClientProvider client={client}><AuthenticationEffects /></QueryClientProvider>);
    await act(async () => { SyntheticAuthChannel.notify(); });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy).toHaveBeenLastCalledWith('/api/v1/me', expect.objectContaining({ method: 'GET' }));
    expect(useAuthStore.getState().status).toBe('authenticated');
    fetchSpy.mockResolvedValueOnce(new Response('', { status: 401 }));
    await act(async () => { SyntheticAuthChannel.notify(); });
    expect(useAuthStore.getState().status).toBe('unauthenticated');
    assertLogoutCache(client, { generations, phase: 'unauthenticated', currentGeneration: useAuthStore.getState().generation });
    mounted.unmount();
    const callsAfterUnmount = fetchSpy.mock.calls.length;
    act(() => { SyntheticAuthChannel.notify(); window.dispatchEvent(new Event('focus')); });
    expect(fetchSpy).toHaveBeenCalledTimes(callsAfterUnmount);
    useAuthStore.setState({ status: 'unknown' });
    fetchSpy.mockResolvedValueOnce(new Response('', { status: 401 }));
    const refreshed = render(<QueryClientProvider client={client}><AuthenticationEffects /></QueryClientProvider>);
    await waitFor(() => expect(useAuthStore.getState().status).toBe('unauthenticated'));
    expect(fetchSpy).toHaveBeenCalledTimes(callsAfterUnmount + 1);
    assertLogoutCache(client, { generations, phase: 'unauthenticated', currentGeneration: useAuthStore.getState().generation });
    refreshed.unmount(); fetchSpy.mockRestore();
  });
});
