import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import App, {
  AuthenticationEffects,
  LoginRoute,
  ProtectedRoute,
} from '../App';
import { useAIDockStore } from '../stores/aiDockStore';
import { useAuthStore } from '../stores/authStore';

describe('application authentication boundary', () => {
  beforeEach(() => {
    useAuthStore.setState({ generation: 0, status: 'unauthenticated' });
    useAIDockStore.setState({
      draft: '',
      lastOpenMode: 'drawer',
      mode: 'closed',
      sessionId: null,
      transcript: [],
    });
    window.history.pushState({}, '', '/health');
  });

  it(
    'redirects an unauthenticated admin route to login without mounting its page',
    async () => {
      window.history.pushState({}, '', '/admin/registry');
      render(<App />);

      expect(
        await screen.findByRole('heading', { name: '登录 EternalAI' }),
      ).toBeInTheDocument();
      expect(screen.queryByText('Registry 管理')).not.toBeInTheDocument();
    },
    30_000,
  );

  it('keeps chat protected and returns there after authentication', async () => {
    window.history.pushState({}, '', '/chat');
    render(<App />);

    expect(
      await screen.findByRole('heading', { name: '登录 EternalAI' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('heading', { name: '把要办的事说清楚' }),
    ).not.toBeInTheDocument();

    act(() => useAuthStore.getState().markAuthenticated());

    expect(
      await screen.findByRole('heading', { name: '把要办的事说清楚' }),
    ).toBeInTheDocument();
    expect(window.location.pathname).toBe('/');
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
      screen.queryByRole('heading', { name: '登录 EternalAI' }),
    ).not.toBeInTheDocument();
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

    fireEvent.click(screen.getByRole('button', { name: '退出登录（本地）' }));

    expect(useAuthStore.getState().status).toBe('unauthenticated');
  });

  it('uses the landing-page logo and keeps all existing authenticated routes in one shell', () => {
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    window.history.pushState({}, '', '/');
    render(<App />);

    expect(screen.getByRole('link', { name: /EternalAI/ })).toHaveAttribute(
      'href',
      '/',
    );
    expect(screen.getByRole('link', { name: '工作事项' })).toHaveAttribute(
      'href',
      '/work-objects',
    );
    expect(screen.getByRole('link', { name: '功能管理' })).toHaveAttribute(
      'href',
      '/admin/registry',
    );
    expect(screen.getByRole('link', { name: '任务证据' })).toHaveAttribute(
      'href',
      '/admin/tasks',
    );
    expect(screen.getByRole('link', { name: '账号绑定' })).toHaveAttribute(
      'href',
      '/admin/bindings',
    );
    expect(screen.getByText('当前位置')).toBeInTheDocument();
    expect(screen.getByText('开始新工作', { selector: 'strong' })).toBeInTheDocument();
  });
});
