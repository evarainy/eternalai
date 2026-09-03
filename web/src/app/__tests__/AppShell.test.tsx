import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import {
  Link,
  MemoryRouter,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { PageContextDeclaration } from '../../contracts/pageContext';
import type { CredentialBindingView } from '../../generated/credential-bindings/credential-bindings.schemas';
import { useAIDockStore } from '../../stores/aiDockStore';
import { useAppearanceStore } from '../../stores/appearanceStore';
import { useAuthStore } from '../../stores/authStore';
import { useNavigationStore } from '../../stores/navigationStore';
import { AppShell } from '../AppShell';
import {
  IDENTITY_UNAVAILABLE_NEXT_STEP,
  IDENTITY_UNAVAILABLE_STATEMENT,
} from '../shellLayout';
import { workbenchTheme } from '../theme';
import { usePageContextRegistration } from '../usePageContextRegistration';

/*
 * 样式表原文；用来核对画板给的尺寸真的写进了 CSS，而不是只写在注释里。`new URL()` 在这一屏里解析出的
 * 是 jsdom 的 URL 实例，`fileURLToPath` 不认，所以先取 `.href` 交给它自己解析。
 */
function readSource(relativePath: string): string {
  return readFileSync(
    fileURLToPath(new URL(relativePath, import.meta.url).href),
    'utf-8',
  );
}

const apiMocks = vi.hoisted(() => ({
  getBinding: vi.fn(),
}));

vi.mock('../../generated/credential-bindings/credential-bindings', () => ({
  getBindingApiV1CredentialBindingsTargetSystemGet: apiMocks.getBinding,
}));

function binding(
  overrides: Partial<CredentialBindingView> = {},
): CredentialBindingView {
  return {
    bound: true,
    poll_failure_count: 0,
    poll_status: 'active',
    target_system: 'oa',
    updated_at: null,
    ...overrides,
  };
}

function workObjectsPageContext(): PageContextDeclaration {
  return {
    surface_id: 'work-objects',
    organization_scope: null,
    work_object_refs: [{ work_object_id: 'work-1' }],
    source_refs: [{ source_system: 'oa', source_ref: 'OA-WF-001' }],
    filters: [
      {
        field: 'view',
        operator: 'equals',
        value: 'today',
        source: 'visible_control',
      },
    ],
    selected_metric: null,
    allowed_capabilities: [],
    freshness: { state: 'reported', observed_at: '2026-08-30T09:00:00Z' },
    visibility: 'principal',
  };
}

function AIAssistantPageStub() {
  return (
    <div>
      AI 助手页面
      <Link to="/work-objects">进入工作事项</Link>
    </div>
  );
}

function StaticWorkObjectsPageStub() {
  return (
    <div>
      事项页面
      <Link to="/admin/tasks">去任务证据</Link>
    </div>
  );
}

function RegisteredWorkObjectsPageStub() {
  usePageContextRegistration(workObjectsPageContext());
  return <StaticWorkObjectsPageStub />;
}

function SearchPageStub() {
  const location = useLocation();
  return <div>搜索结果页 {location.search}</div>;
}

function renderShell(
  initialPath = '/work-objects',
  {
    registerWorkObjectsContext = false,
  }: { registerWorkObjectsContext?: boolean } = {},
) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <ConfigProvider theme={workbenchTheme}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/chat" element={<AIAssistantPageStub />} />
              <Route
                path="/work-objects"
                element={
                  registerWorkObjectsContext ? (
                    <RegisteredWorkObjectsPageStub />
                  ) : (
                    <StaticWorkObjectsPageStub />
                  )
                }
              />
              <Route path="/search" element={<SearchPageStub />} />
              <Route path="/work-dispatch" element={<div>交办落地页</div>} />
              <Route path="/apps" element={<div>软件中心落地页</div>} />
              <Route path="/messages" element={<div>消息占位页</div>} />
              <Route path="/admin/tasks" element={<div>证据页面</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ConfigProvider>,
  );
}

function shellElement(): HTMLElement {
  const shell = screen.getByTestId('app-main').parentElement;
  if (shell === null) {
    throw new Error('AppShell root element is missing.');
  }
  return shell;
}

function resetStores(): void {
  apiMocks.getBinding.mockReset();
  apiMocks.getBinding.mockResolvedValue(binding());
  window.localStorage.clear();
  useNavigationStore.setState({ collapsed: false });
  useAppearanceStore.setState({ background: 'bgA' });
  useAuthStore.setState({ generation: 1, status: 'authenticated' });
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
}

describe('AppShell and singleton AI Dock', () => {
  beforeEach(resetStores);

  it('mounts exactly one Dock instance for the whole shell', () => {
    renderShell();

    expect(screen.getAllByTestId('ai-dock')).toHaveLength(1);
  });

  it('keeps the main content width identical in drawer and enlarged floating modes', () => {
    useAIDockStore.setState({ mode: 'drawer', lastOpenMode: 'drawer' });
    renderShell();

    const shell = shellElement();
    const drawerShellClassName = shell.className;
    expect(shell).not.toHaveAttribute('data-dock-offset');
    expect(screen.getByTestId('ai-dock')).toHaveAttribute('data-mode', 'drawer');
    expect(screen.getByTestId('ai-dock')).toHaveAttribute('data-floating', 'true');

    act(() => useAIDockStore.getState().setMode('pinned'));

    expect(shell).not.toHaveAttribute('data-dock-offset');
    expect(shell.className).toBe(drawerShellClassName);
    expect(screen.getByTestId('ai-dock')).toHaveAttribute('data-mode', 'pinned');
    expect(screen.getByTestId('ai-dock')).toHaveAttribute('data-floating', 'true');
  });

  it('moves the floating panel by mouse drag, by arrow keys, and back by reset', () => {
    useAIDockStore.setState({ mode: 'drawer', lastOpenMode: 'drawer' });
    renderShell();

    const dock = screen.getByTestId('ai-dock');
    expect(dock).toHaveAttribute('data-positioned', 'false');

    const handle = screen.getByRole('button', { name: /移动 AI 助手/ });
    fireEvent.mouseDown(handle, { clientX: 100, clientY: 100 });
    fireEvent.mouseMove(window, { clientX: 160, clientY: 180 });
    fireEvent.mouseUp(window);

    expect(dock).toHaveAttribute('data-positioned', 'true');
    expect(dock.style.left).toBe('60px');
    expect(dock.style.top).toBe('80px');

    fireEvent.keyDown(handle, { key: 'ArrowDown' });
    fireEvent.keyDown(handle, { key: 'ArrowRight' });

    expect(dock.style.top).toBe('104px');
    expect(dock.style.left).toBe('84px');

    fireEvent.click(screen.getByRole('button', { name: /复\s*位/ }));

    expect(dock).toHaveAttribute('data-positioned', 'false');
    expect(dock.style.left).toBe('');
    expect(dock.style.top).toBe('');
  });

  it('keeps the same temporary session and conversation across SPA navigation', () => {
    useAIDockStore.setState({
      draft: '尚未发送的补充内容',
      lastOpenMode: 'drawer',
      mode: 'drawer',
      sessionId: '11111111-1111-4111-8111-111111111111',
      transcript: [{ role: 'user', text: '请继续处理这一项' }],
    });
    renderShell();

    expect(screen.getByText('请继续处理这一项')).toBeInTheDocument();
    expect(screen.getByLabelText('要 AI 帮什么')).toHaveValue('尚未发送的补充内容');
    fireEvent.click(screen.getByRole('link', { name: '去任务证据' }));

    expect(screen.getByText('证据页面')).toBeInTheDocument();
    expect(screen.getByText('请继续处理这一项')).toBeInTheDocument();
    expect(useAIDockStore.getState().sessionId).toBe(
      '11111111-1111-4111-8111-111111111111',
    );
    expect(useAIDockStore.getState().mode).toBe('drawer');
    expect(screen.getByRole('link', { name: '任务证据' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('provides visible input guidance and a textual Dock state', () => {
    useAIDockStore.setState({ mode: 'drawer' });
    useAIDockStore.getState().registerPageContext(workObjectsPageContext());
    renderShell();

    expect(screen.getByLabelText('要 AI 帮什么')).toBeInTheDocument();
    expect(
      screen.queryByText('写清对象、时间和要得到的结果'),
    ).not.toBeInTheDocument();
    expect(screen.getByText('正在协助：工作事项')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '工作事项' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(screen.queryByRole('button', { name: /^⚙$/ })).not.toBeInTheDocument();
    expect(document.body.textContent ?? '').not.toMatch(/[✦▤✎▦✉☻⊘◇➜◐◉⚑⚡⚙⏻✥«»]/u);
  });

  it('submits the global Work Object search without changing the AI input', () => {
    useAIDockStore.setState({ mode: 'drawer' });
    renderShell('/admin/tasks');

    fireEvent.change(screen.getByLabelText('搜索工作事项'), {
      target: { value: '  OA-WF-001  ' },
    });
    fireEvent.click(screen.getByRole('button', { name: /搜\s*索/ }));

    expect(screen.getByText('搜索结果页 ?q=OA-WF-001')).toBeInTheDocument();
    expect(screen.getByLabelText('搜索工作事项')).toHaveAttribute(
      'placeholder',
      '搜索工作事项、文件编号、责任人',
    );
    expect(screen.getByLabelText('要 AI 帮什么')).toHaveValue('');
    expect(useAIDockStore.getState().draft).toBe('');
  });

  it('keeps the AI assistant page context-free without crashing the singleton Dock', async () => {
    useAIDockStore.setState({ mode: 'drawer' });
    useAIDockStore.getState().registerPageContext(workObjectsPageContext());

    renderShell('/chat');

    expect(screen.getByText('AI 助手页面')).toBeInTheDocument();
    expect(screen.getByTestId('ai-dock')).not.toBeVisible();
    await waitFor(() =>
      expect(useAIDockStore.getState().pageContextDeclaration).toBeNull(),
    );
  });

  it('clears a removal notice when the Work Objects page binds again', async () => {
    useAIDockStore.setState({ lastOpenMode: 'drawer', mode: 'drawer' });
    useAIDockStore.getState().registerPageContext(workObjectsPageContext());
    renderShell('/chat', { registerWorkObjectsContext: true });

    await waitFor(() => {
      expect(useAIDockStore.getState().pageContextDeclaration).toBeNull();
      expect(useAIDockStore.getState().contextNotice).toContain(
        '页面上下文已移除',
      );
    });

    fireEvent.click(screen.getByRole('link', { name: '进入工作事项' }));

    await waitFor(() => {
      expect(useAIDockStore.getState().pageContextDeclaration?.surface_id).toBe(
        'work-objects',
      );
      expect(useAIDockStore.getState().contextNotice).toBeNull();
    });
    expect(screen.queryByText(/页面上下文已移除/)).not.toBeInTheDocument();
    expect(screen.getByText('正在协助：工作事项')).toBeVisible();
  });
});

describe('AppShell primary navigation', () => {
  beforeEach(resetStores);

  it('lays out the five decided primary items in order above the admin group', () => {
    renderShell();

    const navigation = screen.getByRole('navigation', { name: '工作区' });
    const links = within(navigation).getAllByRole('link');
    expect(
      links.slice(0, 5).map((link) => [
        link.getAttribute('aria-label'),
        link.getAttribute('href'),
      ]),
    ).toEqual([
      ['AI 助手', '/chat'],
      ['工作事项', '/work-objects'],
      ['任务交办', '/work-dispatch'],
      ['软件中心', '/apps'],
      ['消息', '/messages'],
    ]);

    const adminSummary = within(navigation).getByTitle('管理页面');
    const lastPrimaryLink = links.at(4);
    expect(lastPrimaryLink).toBeDefined();
    const relativePosition =
      lastPrimaryLink?.compareDocumentPosition(adminSummary) ?? 0;
    expect(relativePosition & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('renders the product mark as a plain label that cannot be clicked', () => {
    renderShell();

    expect(screen.queryByRole('link', { name: /EternalAI/ })).not.toBeInTheDocument();
    const brand = screen.getByTestId('app-brand');
    expect(brand.tagName).toBe('DIV');
    expect(within(brand).getByText('EternalAI')).toBeInTheDocument();
    expect(within(brand).queryByRole('link')).not.toBeInTheDocument();
    expect(within(brand).queryByRole('button')).not.toBeInTheDocument();
  });

  it('collapses to an icon rail, persists the choice, and keeps accessible names', () => {
    renderShell();

    const sidebar = screen.getByRole('complementary', { name: '主导航' });
    expect(sidebar).toHaveAttribute('data-collapsed', 'false');
    expect(within(sidebar).getByText('工作事项')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '收起导航' }));

    expect(sidebar).toHaveAttribute('data-collapsed', 'true');
    expect(within(sidebar).queryByText('工作事项')).not.toBeInTheDocument();
    for (const label of ['AI 助手', '工作事项', '任务交办', '软件中心', '消息']) {
      const link = within(sidebar).getByRole('link', { name: label });
      expect(link).toHaveAttribute('title', label);
    }
    expect(useNavigationStore.getState().collapsed).toBe(true);
    expect(window.localStorage.getItem('eternalai-navigation-collapsed')).toContain(
      '"collapsed":true',
    );

    fireEvent.click(screen.getByRole('button', { name: '展开导航' }));

    expect(sidebar).toHaveAttribute('data-collapsed', 'false');
    expect(useNavigationStore.getState().collapsed).toBe(false);
  });

  it('renders the persistent floating AI entry everywhere except the AI assistant page', () => {
    renderShell();

    expect(screen.getByTestId('ai-dock-launcher')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '打开 AI 助手' }));
    expect(useAIDockStore.getState().mode).toBe('drawer');
  });

  it('does not render the floating AI entry on the AI assistant page', () => {
    renderShell('/chat');

    expect(screen.queryByTestId('ai-dock-launcher')).not.toBeInTheDocument();
  });
});

describe('AppShell topbar', () => {
  beforeEach(resetStores);

  it('fixes the element order and no longer renders a current-location block', () => {
    renderShell();

    const topbar = screen.getByTestId('app-topbar');
    expect(
      Array.from(topbar.children).map((child) => child.getAttribute('data-slot')),
    ).toEqual([
      'search',
      'identity',
      'style',
      'system-status',
      'notifications',
      'avatar',
    ]);
    expect(screen.queryByText('当前位置')).not.toBeInTheDocument();
    expect(screen.queryByText('搜索工作事项', { selector: 'strong' })).toBeNull();
  });

  it('states that the department and name cannot be read, on the single line the slot has', () => {
    renderShell();

    /*
     * 画板上这一格只有一行（`办公室 / 王××`）。返修把顶栏压回一行：如实说明取不到留在顶栏，下一步
     * 移进头像点开的用户菜单（见下一条），两句合起来仍满足 2026-08-27 的「说明 + 下一步」。
     */
    const identity = screen.getByTestId('topbar-identity');
    expect(identity).toHaveTextContent(IDENTITY_UNAVAILABLE_STATEMENT);
    expect(identity).not.toHaveTextContent(IDENTITY_UNAVAILABLE_NEXT_STEP);
    expect(identity.textContent?.trim().length ?? 0).toBeGreaterThan(0);
    expect(identity.textContent).not.toMatch(/[A-Za-z0-9]/);
    expect(identity.textContent).not.toContain('/');
    expect(within(identity).queryByRole('button')).not.toBeInTheDocument();
  });

  it('turns the avatar into the user menu trigger without inventing a photo or an initial', () => {
    renderShell();

    const avatar = screen.getByTestId('topbar-avatar');
    expect(avatar.tagName).toBe('BUTTON');
    expect(avatar).toHaveAccessibleName('用户菜单，暂时取不到你的照片');
    expect(avatar).toHaveAttribute('aria-expanded', 'false');
    expect(avatar.textContent).not.toMatch(/[一-龥A-Za-z]/);

    fireEvent.click(avatar);

    expect(avatar).toHaveAttribute('aria-expanded', 'true');
    const menu = screen.getByRole('region', { name: '用户菜单' });
    expect(within(menu).getByText(IDENTITY_UNAVAILABLE_STATEMENT)).toBeInTheDocument();
    expect(within(menu).getByText(IDENTITY_UNAVAILABLE_NEXT_STEP)).toBeInTheDocument();
  });

  it('carries logout in the user menu instead of the sidebar and keeps the help entry honest', () => {
    renderShell();

    const sidebar = screen.getByRole('complementary', { name: '主导航' });
    expect(
      within(sidebar).queryByRole('button', { name: /退出登录/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /退出登录/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('topbar-avatar'));

    const menu = screen.getByRole('region', { name: '用户菜单' });
    /*
     * 画板 `TopPops.dc.html` 的用户菜单五行照抄，「退出登录」不再带「（本地）」后缀——那个后缀是我们
     * 自己加的。它说明的那件事（只退工作台、不退 OA）改由行右侧的短注承担，仍然如实。
     */
    const logout = within(menu).getByRole('button', { name: /退出登录/ });
    expect(logout).toBeInTheDocument();
    expect(logout).toHaveTextContent('只退工作台');
    expect(within(menu).getByRole('link', { name: /账号绑定/ })).toHaveAttribute(
      'href',
      '/admin/bindings',
    );
    expect(
      within(menu).getAllByText((_, node) => node?.textContent === '换个界面风格').length,
    ).toBeGreaterThan(0);
    expect(within(menu).getByText(/怎么用 \/ 找人帮忙/)).toBeInTheDocument();
    expect(menu.textContent).not.toContain('8012');
    expect(menu.textContent).not.toContain('内线');

    fireEvent.click(logout);

    expect(useAuthStore.getState().status).toBe('unauthenticated');
  });

  it('switches the background image from the style control and remembers the choice', () => {
    renderShell();

    const styleButton = screen.getByRole('button', { name: '切换界面风格' });
    styleButton.focus();
    expect(styleButton).toHaveFocus();
    expect(styleButton).toHaveAttribute('aria-expanded', 'false');
    expect(shellElement()).toHaveAttribute('data-background', 'bgA');

    fireEvent.click(styleButton);

    expect(styleButton).toHaveAttribute('aria-expanded', 'true');
    expect(screen.queryByText('换底图在下一版提供。')).not.toBeInTheDocument();
    const warm = screen.getByRole('button', { name: '暖橙' });
    expect(warm).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(warm);

    expect(warm).toHaveAttribute('aria-pressed', 'true');
    expect(useAppearanceStore.getState().background).toBe('bgB');
    expect(shellElement()).toHaveAttribute('data-background', 'bgB');
    expect(
      window.localStorage.getItem('eternalai-appearance-background'),
    ).toContain('"background":"bgB"');
  });

  it('counts invalid OA credentials on the system status control only', async () => {
    apiMocks.getBinding.mockResolvedValue(binding({ poll_status: 'invalid' }));
    renderShell();

    const statusButton = await screen.findByRole('button', {
      name: '系统状态，1 项需要处理',
    });
    expect(within(statusButton).getByTestId('system-status-count')).toHaveTextContent(
      '1',
    );

    const notificationsButton = screen.getByRole('button', {
      name: '通知，消息功能尚未开发，没有可显示的提醒',
    });
    expect(notificationsButton.textContent).not.toContain('1');
    expect(
      within(notificationsButton).queryByTestId('system-status-count'),
    ).not.toBeInTheDocument();

    fireEvent.click(statusButton);

    expect(screen.getByText('OA 办公系统')).toBeInTheDocument();
    expect(
      screen.getByText(
        '你在 OA 的密码已经失效，现在取不到新数据。重新绑定大概 10 秒。',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '重新绑定 OA 账号' })).toHaveAttribute(
      'href',
      '/admin/bindings',
    );
    expect(screen.getByTestId('system-status-summary')).toHaveTextContent(
      '1 个不正常',
    );
    expect(apiMocks.getBinding).toHaveBeenCalledWith('oa');
  });

  /*
   * 2026-08-20「每个前端棒的最低体验要求」：取后端数据的区块要有加载中 / 空 / 出错三态，且出错不得
   * 冒充「没有数据」。系统状态面板的「还在读」与「读不到」是两回事，不能并成一个「暂时不知道」。
   */
  it('separates “still loading” from “could not read” on the system status panel', async () => {
    let resolveBinding!: (value: CredentialBindingView) => void;
    apiMocks.getBinding.mockReturnValue(
      new Promise<CredentialBindingView>((resolve) => {
        resolveBinding = resolve;
      }),
    );
    renderShell();

    const loadingButton = screen.getByRole('button', { name: '系统状态，正在读取' });
    expect(within(loadingButton).getByTestId('system-status-count')).toHaveTextContent(
      '…',
    );
    fireEvent.click(loadingButton);
    expect(screen.getByText('正在读取。读到了这里会自己变。')).toBeInTheDocument();
    expect(screen.queryByText('正常。')).not.toBeInTheDocument();

    await act(async () => {
      resolveBinding(binding());
    });

    expect(
      await screen.findByRole('button', { name: '系统状态，暂无需要处理的项' }),
    ).toBeInTheDocument();
    expect(screen.getByText('正常。')).toBeInTheDocument();
  });

  it('says the system status is unknown instead of pretending it is normal', async () => {
    apiMocks.getBinding.mockRejectedValue(new Error('unreachable'));
    renderShell();

    const statusButton = await screen.findByRole('button', {
      name: '系统状态，暂时无法判断',
    });
    expect(within(statusButton).getByTestId('system-status-count')).toHaveTextContent(
      '？',
    );

    fireEvent.click(statusButton);

    expect(
      screen.getByText(
        '读不到，暂时不知道。刷新本页；还是取不到就找管理员，别当成正常。',
      ),
    ).toBeInTheDocument();
    expect(screen.getByTestId('system-status-summary')).toHaveTextContent('读不到');
  });

  it('shows no attention count while OA credentials are healthy', async () => {
    renderShell();

    const statusButton = await screen.findByRole('button', {
      name: '系统状态，暂无需要处理的项',
    });
    expect(
      within(statusButton).queryByTestId('system-status-count'),
    ).not.toBeInTheDocument();
  });

  it('explains why the notification panel is empty and what to do instead', () => {
    renderShell();

    fireEvent.click(screen.getByRole('button', {
      name: '通知，消息功能尚未开发，没有可显示的提醒',
    }));

    expect(screen.getByText('这里现在是空的。')).toBeInTheDocument();
    expect(
      screen.getByText('消息功能还没有开发，系统还不会给你发提醒。'),
    ).toBeInTheDocument();
    const panel = screen.getByRole('region', { name: '通知' });
    expect(within(panel).getByRole('link', { name: '工作事项' })).toHaveAttribute(
      'href',
      '/work-objects',
    );
  });
});

/*
 * 2026-09-04 雨爷实机走查：「右上角用户头像点击后不是一个弹出层变成了一个很宽的占用主页面的层，这个
 * 也不符合我们的设计稿，这句话我说了很多次了」「系统状态图标要通过颜色区分，你就照搬我们设计稿」。
 *
 * 下面这组断言把画板 `TopPops.dc.html` 的两条尺寸（用户菜单 268px、系统状态 384px）与「浮在正文之上
 * 而不是挤开正文」的定位方式钉在渲染结果与样式表上：谁把 `.popover` 改回流内块、或把宽度改宽，这里
 * 就会变红。
 */
describe('AppShell topbar popovers follow the finalized canvas', () => {
  beforeEach(resetStores);

  function popoverRule(panel: string): string {
    const css = readSource('../AppShell.module.css');
    const block = new RegExp(
      `\\.popover\\[data-panel='${panel}'\\]\\s*\\{([^}]*)\\}`,
    ).exec(css);
    if (block === null) {
      throw new Error(`No .popover[data-panel='${panel}'] rule in AppShell.module.css`);
    }
    return block[1] ?? '';
  }

  it('floats every topbar panel above the content instead of pushing it down', () => {
    const css = readSource('../AppShell.module.css');
    const popover = /\.popover\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';

    expect(popover).toContain('position: absolute');
    expect(popover).not.toContain('position: static');
    expect(/\.stage\s*\{[^}]*position: relative/.test(css)).toBe(true);
  });

  it('gives the user menu the 268px width the canvas draws', () => {
    renderShell();
    fireEvent.click(screen.getByTestId('topbar-avatar'));

    const popover = screen.getByTestId('topbar-popover');
    expect(popover).toHaveAttribute('data-panel', 'user');
    expect(popoverRule('user')).toContain('width: 268px');
    // 弹层打开时主内容仍在，且弹层不是主内容的祖先——它是覆盖层，不是新的页面区块。
    expect(screen.getByText('事项页面')).toBeInTheDocument();
    expect(popover.contains(screen.getByTestId('app-main'))).toBe(false);
  });

  it('lists the five canvas rows and reads the OA binding state instead of assuming it', async () => {
    renderShell();
    fireEvent.click(screen.getByTestId('topbar-avatar'));

    const menu = screen.getByRole('region', { name: '用户菜单' });
    const rowLabels = ['账号绑定', '换个界面风格', '个人设置', '怎么用 / 找人帮忙', '退出登录'];
    for (const label of rowLabels) {
      expect(within(menu).getByText(label)).toBeInTheDocument();
    }
    expect(await within(menu).findByText('OA 已绑')).toBeInTheDocument();
    // 姓名 / 部门 / 职务没有数据源：如实说明，不得出现任何像姓名的占位值。
    expect(within(menu).getByText(IDENTITY_UNAVAILABLE_STATEMENT)).toBeInTheDocument();
    expect(menu.textContent).not.toMatch(/王|张三|李四|主任科员/);
  });

  it('says the OA binding could not be read rather than showing it as bound', async () => {
    apiMocks.getBinding.mockRejectedValue(new Error('unreachable'));
    renderShell();
    fireEvent.click(screen.getByTestId('topbar-avatar'));

    const menu = screen.getByRole('region', { name: '用户菜单' });
    expect(await within(menu).findByText('读不到')).toBeInTheDocument();
    expect(within(menu).queryByText('OA 已绑')).not.toBeInTheDocument();
  });

  it('gives the system status panel the 384px width and colour-coded rows, not glyphs', async () => {
    apiMocks.getBinding.mockResolvedValue(binding({ poll_status: 'invalid' }));
    renderShell();
    fireEvent.click(
      await screen.findByRole('button', { name: '系统状态，1 项需要处理' }),
    );

    const panel = screen.getByRole('region', { name: '系统状态' });
    expect(panel).toHaveAttribute('data-panel', 'system-status');
    expect(popoverRule('system-status')).toContain('width: 384px');

    /*
     * 状态靠颜色区分：每一行带一个 `data-status-kind`，样式表按该值给圆点上色。原实现用的
     * `？ × — ! √` 这类字符符号必须一个都不剩。
     */
    const kinds = Array.from(
      panel.querySelectorAll('[data-status-kind]'),
      (row) => row.getAttribute('data-status-kind'),
    );
    expect(kinds).toEqual(['attention', 'unchecked', 'unchecked']);
    expect(panel.textContent ?? '').not.toMatch(/[？×—!√✓✗⚠]/u);

    const css = readSource('../AppShell.module.css');
    for (const kind of ['normal', 'attention', 'unchecked']) {
      expect(css).toContain(`.statusRow[data-status-kind='${kind}'] .statusDot`);
    }
  });

  /*
   * 「AI 助手」「工作台自己的数据」两行画板上有，但工作台没有任何健康检查来源。行位照画板保留，状态
   * 一律标成「还没有接上检查」——既不编「正常」，也不编一个不存在的检查周期。
   */
  it('never claims the assistant or the workbench itself is healthy', async () => {
    renderShell();
    fireEvent.click(
      await screen.findByRole('button', { name: '系统状态，暂无需要处理的项' }),
    );

    const panel = screen.getByRole('region', { name: '系统状态' });
    for (const name of ['AI 助手', '工作台自己的数据']) {
      const row = within(panel).getByText(name).closest('[data-status-kind]');
      expect(row).not.toBeNull();
      expect(row).toHaveAttribute('data-status-kind', 'unchecked');
      expect(row).toHaveTextContent('还没有接上检查，这里不代表正常。');
    }
    expect(panel.textContent).not.toContain('每 2 分钟');
    expect(panel.textContent).not.toMatch(/每 \d+ 分钟自动检查/);
  });

  it('closes an open panel with Escape so it cannot sit on top of the content', () => {
    renderShell();
    fireEvent.click(screen.getByTestId('topbar-avatar'));
    expect(screen.getByTestId('topbar-popover')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.queryByTestId('topbar-popover')).not.toBeInTheDocument();
  });

  /*
   * 「页面最上方搜索框显示不明显且搜索按钮位置没对齐」。画板 `Main.dc.html` 的搜索是一个 46px 高的
   * 凹槽，左边一个图标、右边一行提示文字，没有独立按钮。这里钉死：提交入口就是那个左侧图标按钮，
   * 与输入框在同一个 flex 行里垂直居中；凹槽自己带画板那组内阴影。
   */
  it('rebuilds the search box as the 46px well the canvas draws', () => {
    renderShell('/admin/tasks');

    const submit = screen.getByRole('button', { name: '搜索' });
    const field = submit.parentElement;
    expect(field?.tagName).toBe('FORM');
    expect(field).toContainElement(screen.getByLabelText('搜索工作事项'));

    const css = readSource('../AppShell.module.css');
    const rule = /\.searchField\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';
    expect(rule).toContain('height: 46px');
    expect(rule).toContain('align-items: center');
    expect(rule).toContain('var(--workbench-well-edge)');
    expect(css).not.toContain('ant-input-group-addon');
  });
});
