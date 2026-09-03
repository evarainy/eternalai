import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/mutator';
import { useAuthStore } from '../../stores/authStore';
import LoginPage from '../LoginPage';

const authMocks = vi.hoisted(() => ({
  login: vi.fn(),
}));

vi.mock('../../generated/auth/auth', () => ({
  loginApiV1AuthLoginPost: authMocks.login,
}));

function renderLogin(state?: { from: string }) {
  const client = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
  const rendered = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[{ pathname: '/login', state }]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/admin/tasks" element={<div>受保护目标</div>} />
          <Route path="/admin/registry" element={<div>默认目标</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { client, ...rendered };
}

function storageText(storage: Storage): string {
  const values: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key !== null) {
      values.push(`${key}:${storage.getItem(key) ?? ''}`);
    }
  }
  return values.join('|');
}

describe('LoginPage', () => {
  let restoreConsoleSpies: Array<() => void> = [];

  beforeEach(() => {
    authMocks.login.mockReset();
    localStorage.clear();
    sessionStorage.clear();
    useAuthStore.setState({ generation: 0, status: 'unauthenticated' });
  });

  afterEach(() => {
    restoreConsoleSpies.forEach((restore) => restore());
    restoreConsoleSpies = [];
  });

  it(
    'shows the account in the clear, masks the password, and enters the requested protected route only after success',
    async () => {
      let resolveLogin!: (value: { authenticated: boolean }) => void;
      authMocks.login.mockReturnValue(
        new Promise((resolve) => {
          resolveLogin = resolve;
        }),
      );
      const { client } = renderLogin({ from: '/admin/tasks' });
      client.setQueryData(['private', 'principal-a'], {
        value: 'cached private response',
      });

      /*
       * 雨爷 2026-09-03 实机走查：账号框此前也用 `Input.Password`，输入的账号被打成圆点。账号改明文，
       * **密码继续打码**——下面两条一起钉死，避免哪一条被单独改掉。
       */
      const loginId = screen.getByLabelText('账号');
      const password = screen.getByLabelText('密码');
      expect(loginId).toHaveAttribute('type', 'text');
      expect(password).toHaveAttribute('type', 'password');

      fireEvent.change(loginId, { target: { value: 'LOGIN_ID_SENTINEL' } });
      fireEvent.change(password, { target: { value: 'PASSWORD_SENTINEL' } });
      fireEvent.click(screen.getByRole('button', { name: /登\s*录/ }));

      await waitFor(() => {
        expect(authMocks.login).toHaveBeenCalledWith({
          loginid: 'LOGIN_ID_SENTINEL',
          userpassword: 'PASSWORD_SENTINEL',
        });
      });
      expect(screen.getByLabelText('账号')).toHaveValue('');
      expect(screen.getByLabelText('密码')).toHaveValue('');
      expect(useAuthStore.getState().status).toBe('unauthenticated');
      act(() => resolveLogin({ authenticated: true }));
      expect(await screen.findByText('受保护目标')).toBeInTheDocument();
      expect(useAuthStore.getState().status).toBe('authenticated');
      expect(client.getQueryCache().getAll()).toHaveLength(0);
    },
    30_000,
  );

  it('fails closed, clears submitted fields, and does not persist or log credentials', async () => {
    const consoleSpies = [
      vi.spyOn(console, 'debug').mockImplementation(() => undefined),
      vi.spyOn(console, 'error').mockImplementation(() => undefined),
      vi.spyOn(console, 'info').mockImplementation(() => undefined),
      vi.spyOn(console, 'log').mockImplementation(() => undefined),
      vi.spyOn(console, 'warn').mockImplementation(() => undefined),
    ];
    restoreConsoleSpies = consoleSpies.map((spy) => () => spy.mockRestore());
    authMocks.login.mockRejectedValue(
      new ApiError(401, 'authentication_failed', 'backend detail must stay hidden'),
    );
    const { client } = renderLogin();
    const loginId = screen.getByLabelText('账号');
    const password = screen.getByLabelText('密码');

    fireEvent.change(loginId, { target: { value: 'LOGIN_ID_SENTINEL' } });
    fireEvent.change(password, { target: { value: 'PASSWORD_SENTINEL' } });
    fireEvent.click(screen.getByRole('button', { name: /登\s*录/ }));

    expect(
      await screen.findByText('登录失败，请检查登录信息后重试。'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/backend detail|authentication_failed/i)).not.toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByLabelText('账号')).toHaveValue('');
      expect(screen.getByLabelText('密码')).toHaveValue('');
    });
    expect(useAuthStore.getState().status).toBe('unauthenticated');
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    expect(client.getMutationCache().getAll()).toHaveLength(0);

    const observableState = [
      storageText(localStorage),
      storageText(sessionStorage),
      JSON.stringify(useAuthStore.getState()),
      window.location.href,
      ...consoleSpies.flatMap((spy) => spy.mock.calls.flat().map(String)),
    ].join('|');
    expect(observableState).not.toContain('LOGIN_ID_SENTINEL');
    expect(observableState).not.toContain('PASSWORD_SENTINEL');
  });

  /*
   * 登录页挂在 `ProtectedRoute` 之外、不经过 `AppShell`，玻璃换皮曾整个漏掉它。下面钉死它自己那一屏
   * 的形态：品牌标识 + 欢迎语 + 一句副标题，照定稿画板 `Login.dc.html`；标签去掉 OA 前缀（雨爷
   * 2026-09-03 明确要求，优先于画板上的「OA 账号」「OA 密码」）；不出现 2026-09-02 已全站移除的内线
   * 电话字样。
   */
  it('renders the decided login-card copy without the OA prefix or the removed hotline line', () => {
    renderLogin();

    expect(screen.getByRole('heading', { level: 1, name: '欢迎回来' })).toBeInTheDocument();
    expect(screen.getByText('EternalAI')).toBeInTheDocument();
    expect(screen.getByText('办事工作台')).toBeInTheDocument();
    expect(screen.getByText('OA 里要你办的事，都在这儿。')).toBeInTheDocument();
    expect(screen.queryByLabelText('OA 登录标识')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('OA 密码')).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain('内线');
    expect(document.body.textContent).not.toContain('8012');
  });

  it('draws the brand mark as an inline stroke SVG instead of a text glyph', () => {
    const { container } = renderLogin();

    const icon = container.querySelector('svg');
    expect(icon).not.toBeNull();
    expect(icon?.getAttribute('stroke')).toBe('currentColor');
    expect(icon?.getAttribute('fill')).toBe('none');
    expect(icon?.getAttribute('aria-hidden')).toBe('true');
  });

  it('asks for the account and the password in plain words', async () => {
    authMocks.login.mockResolvedValue({ authenticated: false });
    renderLogin();

    fireEvent.click(screen.getByRole('button', { name: /登\s*录/ }));

    expect(await screen.findByText('请输入账号。')).toBeInTheDocument();
    expect(screen.getByText('请输入密码。')).toBeInTheDocument();
    expect(authMocks.login).not.toHaveBeenCalled();
  });

  it('does not authenticate when the generated client returns authenticated false', async () => {
    authMocks.login.mockResolvedValue({ authenticated: false });
    renderLogin();

    fireEvent.change(screen.getByLabelText('账号'), {
      target: { value: 'LOGIN_ID_SENTINEL' },
    });
    fireEvent.change(screen.getByLabelText('密码'), {
      target: { value: 'PASSWORD_SENTINEL' },
    });
    fireEvent.click(screen.getByRole('button', { name: /登\s*录/ }));

    expect(
      await screen.findByText('登录失败，请检查登录信息后重试。'),
    ).toBeInTheDocument();
    expect(useAuthStore.getState().status).toBe('unauthenticated');
  });
});
