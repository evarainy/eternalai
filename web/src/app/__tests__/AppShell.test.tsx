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
import type { MeResponse } from '../../generated/me/me.schemas';
import { useAIDockStore } from '../../stores/aiDockStore';
import { useAppearanceStore } from '../../stores/appearanceStore';
import { useAuthStore } from '../../stores/authStore';
import { AppShell } from '../AppShell';
import {
  DEPARTMENT_UNAVAILABLE_LINE,
  JOB_TITLE_UNAVAILABLE_LINE,
  NAME_UNAVAILABLE_LINE,
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
  imConfiguration: vi.fn(),
  getBinding: vi.fn(),
  readMe: vi.fn(),
}));

vi.mock('../../generated/credential-bindings/credential-bindings', () => ({
  getBindingApiV1CredentialBindingsTargetSystemGet: apiMocks.getBinding,
}));

vi.mock('../../generated/me/me', () => ({
  readMeApiV1MeGet: apiMocks.readMe,
}));

vi.mock('../../generated/im-context/im-context', () => ({
  configurationApiV1ImContextConfigurationGet: apiMocks.imConfiguration,
}));

const DISPLAY_NAME = '甲用户';
const DEPARTMENT_NAME = '部门乙';

function meResponse(overrides: Partial<MeResponse> = {}): MeResponse {
  return {
    authenticated: true,
    display_name: DISPLAY_NAME,
    org: { department_name: DEPARTMENT_NAME, department_id: '22' },
    org_status: 'ok',
    avatar_path: '/api/v1/me/avatar',
    ...overrides,
  };
}

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

async function settleLazyDock(): Promise<void> {
  await act(async () => {
    await vi.dynamicImportSettled();
  });
}

function shellElement(): HTMLElement {
  const shell = screen.getByTestId('app-main').parentElement;
  if (shell === null) {
    throw new Error('AppShell root element is missing.');
  }
  return shell;
}

function resetStores(): void {
  apiMocks.imConfiguration.mockReset();
  apiMocks.imConfiguration.mockResolvedValue(null);
  apiMocks.getBinding.mockReset();
  apiMocks.getBinding.mockResolvedValue(binding());
  apiMocks.readMe.mockReset();
  apiMocks.readMe.mockResolvedValue(meResponse());
  window.localStorage.clear();
  useAppearanceStore.setState({ skin: 'ink', size: 'm', clock: 'digit', fx: null });
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

  it('loads one Dock only after explicit open and retains it after close', async () => {
    renderShell();

    expect(screen.queryByTestId('ai-dock')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '打开 AI 助手' }));
    expect(screen.getByTestId('lazy-dock-loading')).toHaveTextContent('正在打开 AI 助手');
    await settleLazyDock();
    expect(screen.getAllByTestId('ai-dock')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: '关闭 AI 助手' }));
    expect(screen.getByTestId('ai-dock')).not.toBeVisible();
    expect(screen.getAllByTestId('ai-dock')).toHaveLength(1);
  });

  it('keeps the main content width identical in drawer and enlarged floating modes', async () => {
    useAIDockStore.setState({ mode: 'drawer', lastOpenMode: 'drawer' });
    renderShell();
    await settleLazyDock();

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

  it('moves the floating panel by mouse drag, by arrow keys, and back by reset', async () => {
    useAIDockStore.setState({ mode: 'drawer', lastOpenMode: 'drawer' });
    renderShell();
    await settleLazyDock();

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

  it('keeps the same temporary session and conversation across SPA navigation', async () => {
    useAIDockStore.setState({
      draft: '尚未发送的补充内容',
      lastOpenMode: 'drawer',
      mode: 'drawer',
      sessionId: '11111111-1111-4111-8111-111111111111',
      transcript: [{ role: 'user', text: '请继续处理这一项' }],
    });
    renderShell();
    await settleLazyDock();

    expect(screen.getByText('请继续处理这一项')).toBeInTheDocument();
    expect(screen.getByLabelText('要 AI 帮什么')).toHaveValue('尚未发送的补充内容');
    fireEvent.click(screen.getByRole('link', { name: '去任务证据' }));

    expect(screen.getByText('证据页面')).toBeInTheDocument();
    expect(screen.getByText('请继续处理这一项')).toBeInTheDocument();
    expect(useAIDockStore.getState().sessionId).toBe(
      '11111111-1111-4111-8111-111111111111',
    );
    expect(useAIDockStore.getState().mode).toBe('drawer');
    fireEvent.click(screen.getByRole('button', { name: '管理页面' }));
    expect(screen.getByRole('link', { name: '任务证据' })).toHaveAttribute(
      'aria-current',
      'page',
    );
  });

  it('provides visible input guidance and a textual Dock state', async () => {
    useAIDockStore.setState({ mode: 'drawer' });
    useAIDockStore.getState().registerPageContext(workObjectsPageContext());
    renderShell();
    await settleLazyDock();

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

  it('submits the Ctrl K Work Object search without changing the AI input', async () => {
    useAIDockStore.setState({ mode: 'drawer' });
    renderShell('/admin/tasks');
    await settleLazyDock();

    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    const input = await screen.findByLabelText('搜索工作事项');
    fireEvent.change(input, {
      target: { value: '　 OA-WF-001  ReVIEW﻿' },
    });
    expect(input).toHaveAttribute('placeholder', '搜索工作事项、文件编号、责任人');
    fireEvent.submit(input.closest('form')!);

    expect(screen.getByText('搜索结果页 ?q=oa-wf-001+review')).toBeInTheDocument();
    expect(screen.getByLabelText('要 AI 帮什么')).toHaveValue('');
    expect(useAIDockStore.getState().draft).toBe('');
  });

  it('keeps the AI assistant page context-free without crashing the singleton Dock', async () => {
    useAIDockStore.setState({ mode: 'drawer' });
    useAIDockStore.getState().registerPageContext(workObjectsPageContext());

    renderShell('/chat');
    await settleLazyDock();

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
    await settleLazyDock();

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

  it('lays out five primary items in order with a separate admin entry', () => {
    renderShell();
    const navigation = screen.getByRole('navigation', { name: '工作区' });
    const links = within(navigation).getAllByRole('link');
    expect(links.map((link) => [link.getAttribute('aria-label'), link.getAttribute('href')])).toEqual([
      ['AI 助手', '/chat'],
      ['消息', '/messages'],
      ['工作事项', '/work-objects'],
      ['任务交办', '/work-dispatch'],
      ['软件中心', '/apps'],
    ]);
    const admin = screen.getByRole('button', { name: '管理页面' });
    expect(links[4]!.compareDocumentPosition(admin) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(admin);
    const panel = screen.getByRole('region', { name: '管理页面' });
    expect(panel).toHaveAttribute('data-panel', 'admin');
    expect(within(panel).getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual([
      '/admin/registry', '/admin/tasks', '/admin/bindings',
    ]);
  });

  it('renders the product mark as a plain label that cannot be clicked', () => {
    renderShell();

    expect(screen.queryByRole('link', { name: /EternalAI/ })).not.toBeInTheDocument();
    const brand = screen.getByTestId('app-brand');
    expect(brand.tagName).toBe('DIV');
    expect(within(brand).getByText('EternalAI 办事工作台')).toBeInTheDocument();
    expect(within(brand).queryByRole('link')).not.toBeInTheDocument();
    expect(within(brand).queryByRole('button')).not.toBeInTheDocument();
  });

  it('keeps accessible names and hover labels on the fixed rail', () => {
    renderShell();
    const rail = screen.getByRole('complementary', { name: '主导航' });
    for (const label of ['AI 助手', '工作事项', '任务交办', '软件中心', '消息']) {
      expect(within(rail).getByRole('link', { name: label })).toHaveAttribute('title', label);
    }
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

/*
 * 2026-11-11 雨爷决定全站取消顶栏：全局项移到左导航底部的个人栏（头像、姓名、单位、通知铃），点个人栏
 * 弹出菜单（搜索工作事项 Ctrl K、切换身份、风格、系统状态、退出登录），设计稿 `dt/claude-v3/v3-7`。
 * 下面这组断言是原「AppShell topbar」各条在新结构上的等价断言：身份如实、取不到不编、状态靠颜色加文字、
 * 读取三态分开、退出由服务端确认。
 */
describe('AppShell user bar', () => {
  beforeEach(resetStores);

  function openUserMenu(): HTMLElement {
    fireEvent.click(screen.getByTestId('user-bar-identity'));
    return screen.getByRole('region', { name: '设置' });
  }

  it('puts admin, settings and avatar in the rail footer and notifications in settings', () => {
    renderShell();
    expect(screen.queryByTestId('app-topbar')).not.toBeInTheDocument();
    expect(screen.queryByRole('banner')).not.toBeInTheDocument();
    expect(screen.queryByText('当前位置')).not.toBeInTheDocument();
    const rail = screen.getByRole('complementary', { name: '主导航' });
    const footer = within(rail).getByTestId('user-bar');
    expect(within(footer).getAllByRole('button').map((button) => button.getAttribute('aria-label'))).toEqual([
      '管理页面', '设置', '设置',
    ]);
    expect(within(footer).getByTestId('user-bar-identity')).toBeInTheDocument();
    fireEvent.click(within(footer).getByTestId('rail-settings'));
    expect(screen.getByRole('region', { name: '设置' })).toHaveAttribute('data-panel', 'user');
    expect(screen.getByTestId('user-menu-notifications')).toHaveTextContent('通知');
  });

  it('shows the real name and department in settings and the name on the avatar', async () => {
    renderShell();
    const identity = screen.getByTestId('user-bar-identity');
    await waitFor(() => expect(identity).toHaveAccessibleName(`设置：${DISPLAY_NAME}`));
    const menu = openUserMenu();
    expect(within(menu).getByText(DISPLAY_NAME)).toBeInTheDocument();
    expect(within(menu).getByTestId('user-menu-meta')).toHaveTextContent(`${DEPARTMENT_NAME} · ${JOB_TITLE_UNAVAILABLE_LINE}`);
  });

  it('drops to the name alone when the department cannot be read, and says nothing else', async () => {
    apiMocks.readMe.mockResolvedValue(
      meResponse({ org: null, org_status: 'unparsable', avatar_path: null }),
    );
    renderShell();

    const identity = screen.getByTestId('user-bar-identity');
    await waitFor(() => expect(identity).toHaveAccessibleName(`设置：${DISPLAY_NAME}`));
    // 个人栏取不到部门时不加一个字的提示——说明留给用户菜单。
    expect(identity.textContent).toBe(DISPLAY_NAME.slice(0, 1));
    expect(identity.textContent).not.toContain('取不到');
  });

  it('renders no name in the user bar before the backend has answered', () => {
    apiMocks.readMe.mockReturnValue(new Promise(() => {}));
    renderShell();

    // 确认还没回来时不写占位、不写「读取中」，也不写一个假名字。
    const identity = screen.getByTestId('user-bar-identity');
    expect(identity.textContent).toBe('');
  });

  it('shows the proxied photo and falls back to the surname when it cannot be loaded', async () => {
    renderShell();

    const trigger = screen.getByTestId('user-bar-identity');
    expect(trigger.tagName).toBe('BUTTON');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    const image = await screen.findByTestId('user-bar-avatar-image');
    // 前端只看到常量路径；OA 主机地址与照片相对路径都留在后端。
    expect(image).toHaveAttribute('src', '/api/v1/me/avatar');
    expect(trigger).toHaveAccessibleName(`设置：${DISPLAY_NAME}`);

    fireEvent.error(image);

    expect(screen.queryByTestId('user-bar-avatar-image')).not.toBeInTheDocument();
    expect(screen.getByTestId('user-bar-avatar-initial')).toHaveTextContent(
      DISPLAY_NAME.slice(0, 1),
    );
  });

  it('shows the surname instead of an empty slot when there is no photo at all', async () => {
    apiMocks.readMe.mockResolvedValue(meResponse({ avatar_path: null }));
    renderShell();

    expect(
      await screen.findByTestId('user-bar-avatar-initial'),
    ).toHaveTextContent(DISPLAY_NAME.slice(0, 1));
    expect(screen.queryByTestId('user-bar-avatar-image')).not.toBeInTheDocument();
  });

  it('keeps a place for the job title and says it cannot be read instead of inventing one', async () => {
    renderShell();

    const menu = openUserMenu();
    expect(await within(menu).findByText(DISPLAY_NAME)).toBeInTheDocument();
    // 部门有数据源，职务没有（OA 的用户信息接口里没有这个字段），雨爷 2026-09-04 裁定留位 + 如实说明。
    expect(within(menu).getByTestId('user-menu-meta')).toHaveTextContent(
      `${DEPARTMENT_NAME} · ${JOB_TITLE_UNAVAILABLE_LINE}`,
    );
    expect(menu.textContent).not.toMatch(/王|张三|李四|主任科员|科员/);
  });

  it('states each missing identity field on its own line in the user menu', async () => {
    apiMocks.readMe.mockResolvedValue(
      meResponse({ display_name: '', org: null, org_status: 'unavailable' }),
    );
    renderShell();

    const menu = openUserMenu();
    expect(await within(menu).findByText(NAME_UNAVAILABLE_LINE)).toBeInTheDocument();
    expect(within(menu).getByTestId('user-menu-meta')).toHaveTextContent(
      `${DEPARTMENT_UNAVAILABLE_LINE} · ${JOB_TITLE_UNAVAILABLE_LINE}`,
    );
  });

  it('offers appearance and status settings, keeps bindings in admin, and logs out', async () => {
    renderShell();
    expect(screen.queryByRole('button', { name: /退出登录/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '管理页面' }));
    const admin = screen.getByRole('region', { name: '管理页面' });
    expect(within(admin).getByRole('link', { name: '账号绑定' })).toHaveAttribute('href', '/admin/bindings');
    const menu = openUserMenu();
    for (const label of ['皮肤', '字号', '时钟', '动效', '通知', '系统状态', '退出登录']) {
      expect(within(menu).getByText(label)).toBeInTheDocument();
    }
    const logout = within(menu).getByRole('button', { name: /退出登录/ });
    expect(logout).toHaveTextContent('只退工作台');
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ authenticated: false }), { status: 200 }));
    fireEvent.click(logout);
    await waitFor(() => expect(useAuthStore.getState().status).toBe('unauthenticated'));
    fetchSpy.mockRestore();
  });

  it('switches the skin in settings and remembers the choice', () => {
    renderShell();
    const menu = openUserMenu();
    const skins = within(menu).getByRole('group', { name: '皮肤' });
    expect(within(skins).getByRole('button', { name: '墨' })).toHaveAttribute('aria-pressed', 'true');
    const rose = within(skins).getByRole('button', { name: '樱' });
    expect(rose).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(rose);
    expect(rose).toHaveAttribute('aria-pressed', 'true');
    expect(useAppearanceStore.getState().skin).toBe('rose');
    expect(window.localStorage.getItem('eternalai-appearance')).toContain('"skin":"rose"');
  });

  it('flags invalid OA credentials on the user bar without opening the menu', async () => {
    apiMocks.getBinding.mockResolvedValue(binding({ poll_status: 'invalid' }));
    renderShell();

    const trigger = await screen.findByRole('button', {
      name: `设置：${DISPLAY_NAME}，系统状态 1 项需要处理`,
    });
    expect(within(trigger).getByTestId('user-bar-status-dot')).toHaveAttribute(
      'data-status-kind',
      'attention',
    );

    fireEvent.click(trigger);
    const menu = screen.getByRole('region', { name: '设置' });
    expect(within(menu).getByTestId('user-menu-notifications')).toHaveTextContent('通知暂无');
    expect(within(menu).getByTestId('system-status-count')).toHaveTextContent('1 个不正常');
    fireEvent.click(within(menu).getByTestId('user-menu-system-status'));

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
   * 冒充「没有数据」。系统状态的「还在读」与「读不到」是两回事，不能并成一个「暂时不知道」。
   */
  it('separates “still loading” from “could not read” on the system status panel', async () => {
    let resolveBinding!: (value: CredentialBindingView) => void;
    apiMocks.getBinding.mockReturnValue(
      new Promise<CredentialBindingView>((resolve) => {
        resolveBinding = resolve;
      }),
    );
    renderShell();

    const menu = openUserMenu();
    expect(within(menu).getByTestId('system-status-count')).toHaveTextContent('正在读取');
    expect(screen.queryByTestId('user-bar-status-dot')).not.toBeInTheDocument();
    fireEvent.click(within(menu).getByTestId('user-menu-system-status'));
    expect(screen.getByText('正在读取。读到了这里会自己变。')).toBeInTheDocument();
    expect(screen.queryByText('正常。')).not.toBeInTheDocument();

    await act(async () => {
      resolveBinding(binding());
    });

    expect(await screen.findByText('正常。')).toBeInTheDocument();
    expect(screen.getByTestId('system-status-summary')).toHaveTextContent('暂无不正常');
  });

  it('says the system status is unknown instead of pretending it is normal', async () => {
    apiMocks.getBinding.mockRejectedValue(new Error('unreachable'));
    renderShell();

    const trigger = await screen.findByRole('button', {
      name: `设置：${DISPLAY_NAME}，系统状态暂时无法判断`,
    });
    expect(within(trigger).getByTestId('user-bar-status-dot')).toHaveAttribute(
      'data-status-kind',
      'unknown',
    );

    fireEvent.click(trigger);
    const menu = screen.getByRole('region', { name: '设置' });
    expect(within(menu).getByTestId('system-status-count')).toHaveTextContent('读不到');
    fireEvent.click(within(menu).getByTestId('user-menu-system-status'));

    expect(
      screen.getByText(
        '读不到，暂时不知道。刷新本页；还是取不到就找管理员，别当成正常。',
      ),
    ).toBeInTheDocument();
    expect(screen.getByTestId('system-status-summary')).toHaveTextContent('读不到');
  });

  it('shows no attention mark while OA credentials are healthy', async () => {
    renderShell();

    const menu = openUserMenu();
    expect(await within(menu).findByText('暂无不正常')).toBeInTheDocument();
    expect(screen.queryByTestId('user-bar-status-dot')).not.toBeInTheDocument();
  });

  it('explains why the notification panel is empty and what to do instead', () => {
    renderShell();

    fireEvent.click(screen.getByTestId('user-bar-identity'));
    fireEvent.click(screen.getByTestId('user-menu-notifications'));

    expect(screen.getByText('暂无通知。')).toBeInTheDocument();
    expect(screen.getByText('系统还不会给你发提醒。')).toBeInTheDocument();
    const panel = screen.getByRole('region', { name: '通知' });
    expect(within(panel).getByRole('link', { name: '工作事项' })).toHaveAttribute(
      'href',
      '/work-objects',
    );
  });
});

/*
 * 2026-09-04 雨爷实机走查要求弹出层「浮在正文之上、不占据主页面」，用户菜单 268px、系统状态 384px；
 * 系统状态靠带颜色的点区分。2026-11-11 弹层改锚在个人栏上方，这几条约束不变。
 */
describe('AppShell user bar popovers', () => {
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

  it('floats every panel above the content instead of pushing it down', () => {
    const css = readSource('../AppShell.module.css');
    const popover = /\.popover\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';

    expect(popover).toContain('position: fixed');
    expect(popover).not.toContain('position: static');
  });

  it('gives the user menu the 268px width the canvas draws', () => {
    renderShell();
    fireEvent.click(screen.getByTestId('user-bar-identity'));

    const popover = screen.getByTestId('shell-popover');
    expect(popover).toHaveAttribute('data-panel', 'user');
    const css = readSource('../AppShell.module.css');
    expect(/\.popover\s*\{([^}]*)\}/.exec(css)?.[1]).toContain('width: 268px');
    // 弹层打开时主内容仍在，且弹层不是主内容的祖先——它是覆盖层，不是新的页面区块。
    expect(screen.getByText('事项页面')).toBeInTheDocument();
    expect(popover.contains(screen.getByTestId('app-main'))).toBe(false);
  });

  it('gives the system status panel the 384px width and colour-coded rows, not glyphs', async () => {
    apiMocks.getBinding.mockResolvedValue(binding({ poll_status: 'invalid' }));
    renderShell();
    fireEvent.click(
      await screen.findByRole('button', { name: /系统状态 1 项需要处理/ }),
    );
    fireEvent.click(screen.getByTestId('user-menu-system-status'));

    const panel = screen.getByRole('region', { name: '系统状态' });
    expect(panel).toHaveAttribute('data-panel', 'system-status');
    expect(popoverRule('system-status')).toContain('width: 384px');

    const rows = Array.from(
      panel.querySelectorAll('[data-status-kind]:not([aria-hidden="true"])'),
    );
    expect(rows.map((row) => row.getAttribute('data-status-kind'))).toEqual([
      'attention',
      'unchecked',
      'unchecked',
    ]);
    for (const row of rows) {
      const dot = row.querySelector('[aria-hidden="true"][data-status-kind]');
      expect(dot).not.toBeNull();
      expect(dot?.getAttribute('data-status-kind')).toBe(
        row.getAttribute('data-status-kind'),
      );
    }
    expect(panel.textContent ?? '').not.toMatch(/[？×—!√✓✗⚠]/u);

    // 一份色值，两个落点：面板行的圆点与菜单/个人栏的圆点共用同一组规则。
    const css = readSource('../AppShell.module.css');
    for (const kind of ['normal', 'attention', 'unchecked']) {
      expect(css).toContain(`.statusDot[data-status-kind='${kind}']`);
      const dotRule = new RegExp(`\\.statusDot\\[data-status-kind='${kind}'\\][^{]*\\{([^}]*)\\}`).exec(css)?.[1];
      expect(dotRule).toContain(kind === 'normal' ? 'var(--ok)' : kind === 'attention' ? 'var(--seal)' : 'var(--text-3)');
    }
  });

  it.each([
    ['invalid' as const, 'attention', '1 个不正常'],
    ['active' as const, 'normal', '暂无不正常'],
  ])(
    'shows a colour-coded dot and words on the menu row when OA is %s',
    async (pollStatus, kind, summary) => {
      apiMocks.getBinding.mockResolvedValue(binding({ poll_status: pollStatus }));
      renderShell();
      fireEvent.click(screen.getByTestId('user-bar-identity'));

      const row = await screen.findByTestId('user-menu-system-status');
      await waitFor(() => expect(row).toHaveTextContent(summary));
      const dot = within(row).getByTestId('user-menu-system-status-dot');
      expect(dot).toHaveAttribute('data-status-kind', kind);
      expect(dot).toHaveAttribute('aria-hidden', 'true');
      // 颜色之外仍有文字：可见的「系统状态」四个字与状态摘要。
      expect(row).toHaveTextContent('系统状态');
      expect(dot.querySelector('svg')).toBeNull();
    },
  );

  it('never claims the assistant or the workbench itself is healthy', async () => {
    renderShell();
    fireEvent.click(screen.getByTestId('user-bar-identity'));
    fireEvent.click(await screen.findByTestId('user-menu-system-status'));

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
    fireEvent.click(screen.getByTestId('user-bar-identity'));
    expect(screen.getByTestId('shell-popover')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.queryByTestId('shell-popover')).not.toBeInTheDocument();
  });

  /*
   * Ctrl K 已改为自绘搜索浮层：保留带名称的输入、唯一搜索动作及外层焦点指示，
   * 不再要求旧 WorkObjectSearchField 的 46px 凹槽与表单内提交键。
   */
  it('keeps search accessible in the custom dialog with an outer focus indicator', async () => {
    renderShell('/admin/tasks');
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true });
    const dialog = await screen.findByRole('dialog', { name: '快速搜索' });
    expect(dialog).toHaveAttribute('data-testid', 'quick-search');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveClass('wb-glass');
    const form = within(dialog).getByRole('search');
    expect(form.tagName).toBe('FORM');
    expect(form).toHaveAttribute('data-focus-ring', 'host');
    const input = within(form).getByLabelText('搜索工作事项');
    expect(input).toHaveFocus();
    expect(within(dialog).getAllByRole('button', { name: '打开搜索页 Enter' })).toHaveLength(1);
    const css = readSource('../QuickSearch.module.css');
    expect(/\.inputRow\s*\{([^}]*)\}/.exec(css)?.[1]).toContain('align-items: center');
    expect(/\.inputRow:focus-within\s*\{([^}]*)\}/.exec(css)?.[1]).toContain('border-bottom-color: var(--primary)');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('quick-search')).not.toBeInTheDocument();
  });
});

describe('server confirmed logout', () => {
  beforeEach(resetStores);

  it.each([200, 401, 503])('late_logout_response_does_not_change_new_identity %s', async (status) => {
    let release!: (value: Response) => void;
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise<Response>((resolve) => { release = resolve; }));
    const mounted = renderShell();
    try {
      fireEvent.click(screen.getByTestId('user-bar-identity'));
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: /退出登录/ })); });
      expect(screen.getByRole('button', { name: /正在退出/ })).toBeDisabled();
      act(() => useAuthStore.getState().markAuthenticated());
      const newGeneration = useAuthStore.getState().generation;
      await act(async () => {
        release(new Response(JSON.stringify(status === 200 ? { authenticated: false } : {
          detail: { code: 'authentication_required', message: 'Synthetic old failure' },
        }), { status }));
      });
      expect(useAuthStore.getState().status).toBe('authenticated');
      expect(useAuthStore.getState().generation).toBe(newGeneration);
      expect(screen.queryByText('退出未完成，请重试')).not.toBeInTheDocument();
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    } finally {
      mounted.unmount();
      fetchSpy.mockRestore();
    }
  });

  it.each([201, 202, 204])('logout_non_200_success_is_not_confirmed %s', async (status) => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(
      status === 204 ? null : JSON.stringify({ authenticated: false }), { status },
    )).mockResolvedValueOnce(new Response(JSON.stringify({ authenticated: false }), { status: 200 }));
    useAIDockStore.setState({ draft: 'Synthetic private draft' });
    renderShell();
    fireEvent.click(screen.getByTestId('user-bar-identity'));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /退出登录/ })); });
    expect(useAuthStore.getState().status).toBe('authenticated');
    expect(screen.getByText('退出未完成，请重试')).toBeInTheDocument();
    expect(useAIDockStore.getState().draft).toBe('Synthetic private draft');
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: /退出登录/ }));
    await waitFor(() => expect(useAuthStore.getState().status).toBe('unauthenticated'));
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    fetchSpy.mockRestore();
  });

  it('logout_waits_for_server_before_leaving', async () => {
    let resolve!: (value: Response) => void;
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise((done) => { resolve = done; }));
    renderShell();
    fireEvent.click(screen.getByTestId('user-bar-identity'));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /退出登录/ })); });
    expect(screen.getByRole('button', { name: /正在退出/ })).toBeDisabled();
    expect(useAuthStore.getState().status).toBe('authenticated');
    expect(fetchSpy).toHaveBeenCalledWith('/api/v1/auth/logout', expect.objectContaining({ method: 'POST', headers: expect.objectContaining({ 'X-EternalAI-CSRF': '1' }) }));
    await act(async () => { resolve(new Response(JSON.stringify({ authenticated: false }))); });
    expect(useAuthStore.getState().status).toBe('unauthenticated');
    fetchSpy.mockRestore();
  });

  it.each(['403', '503', 'network', 'wrong-payload'])('logout_failure_is_visible_and_retry_is_user_driven %s', async (failure) => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    if (failure === 'network') fetchSpy.mockRejectedValueOnce(new TypeError('Synthetic response lost'));
    else if (failure === 'wrong-payload') fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify({ authenticated: true })));
    else fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify({ detail: { code: 'logout_unavailable', message: 'Synthetic failure' } }), { status: Number(failure) }));
    fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify({ authenticated: false })));
    renderShell();
    fireEvent.click(screen.getByTestId('user-bar-identity'));
    fireEvent.click(screen.getByRole('button', { name: /退出登录/ }));
    expect(await screen.findByText('退出未完成，请重试')).toBeInTheDocument();
    expect(useAuthStore.getState().status).toBe('authenticated');
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: /退出登录/ }));
    await waitFor(() => expect(useAuthStore.getState().status).toBe('unauthenticated'));
    fetchSpy.mockRestore();
  });
});
