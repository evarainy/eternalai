import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CredentialBindingView } from '../../generated/credential-bindings/credential-bindings.schemas';
import type { WorkObjectListResponse } from '../../generated/work-objects/work-objects.schemas';
import AppsPage from '../../features/apps/AppsPage';
import ChatPage from '../../pages/ChatPage';
import LoginPage from '../../pages/LoginPage';
import WorkDispatchPage from '../../features/work-dispatch/WorkDispatchPage';
import WorkObjectsPage from '../../pages/WorkObjectsPage';
import { useAIDockStore } from '../../stores/aiDockStore';
import { useAppearanceStore } from '../../stores/appearanceStore';
import { useAuthStore } from '../../stores/authStore';
import { useNavigationStore } from '../../stores/navigationStore';
import {
  blurDeclaringSelectors,
  describeBlurLayers,
  fileBlurRules,
  findBlurLayers,
  unparsableBlurSelectors,
} from '../../test/blurLayers';
import { AppShell } from '../AppShell';
import { BLUR_LAYER_BUDGET } from '../theme';

const apiMocks = vi.hoisted(() => ({
  getBinding: vi.fn(),
  getWorkObject: vi.fn(),
  listWorkObjects: vi.fn(),
  setHandlingMark: vi.fn(),
  syncWorkObjects: vi.fn(),
  bindPassword: vi.fn(),
  unbindPassword: vi.fn(),
}));

vi.mock('../../generated/credential-bindings/credential-bindings', () => ({
  bindPasswordApiV1CredentialBindingsTargetSystemPut: apiMocks.bindPassword,
  getBindingApiV1CredentialBindingsTargetSystemGet: apiMocks.getBinding,
  unbindPasswordApiV1CredentialBindingsTargetSystemDelete: apiMocks.unbindPassword,
}));

vi.mock('../../generated/work-objects/work-objects', () => ({
  getWorkObjectApiV1WorkObjectsWorkObjectIdGet: apiMocks.getWorkObject,
  listWorkObjectsApiV1WorkObjectsGet: apiMocks.listWorkObjects,
  setWorkObjectHandlingMarkApiV1WorkObjectsWorkObjectIdHandlingMarkPatch:
    apiMocks.setHandlingMark,
  syncWorkObjectsApiV1WorkObjectsSyncPost: apiMocks.syncWorkObjects,
}));

function binding(): CredentialBindingView {
  return {
    bound: true,
    poll_failure_count: 0,
    poll_status: 'active',
    target_system: 'oa',
    updated_at: null,
  };
}

function workObjectList(): WorkObjectListResponse {
  return {
    items: [
      {
        assignee_display_name: '雨爷',
        due_at: '2026-08-20T08:00:00Z',
        handling_action: 'go_source_system',
        handling_capability_id: null,
        handling_mark: null,
        handling_marked_at: null,
        source_created_at: '2026-09-01 09:00:00',
        source_fetched_at: '2026-09-02T03:00:00Z',
        source_kind: 'pending_workflow',
        source_received_at: '2026-09-01 09:05:00',
        source_ref: 'OA-WF-001',
        source_status: '待办',
        source_system: 'oa',
        source_title: '核对本月采购流程',
        source_workflow_type_id: 'purchase-review',
        state_authority: 'external_snapshot',
        task_record_id: null,
        work_object_id: 'work-object-1',
      },
    ],
    limit: 200,
    limit_exceeded: false,
  };
}

function renderScreen(initialPath: string) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <ConfigProvider>
      <AntApp>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[initialPath]}>
            <Routes>
              <Route element={<AppShell />}>
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/work-objects" element={<WorkObjectsPage />} />
                <Route path="/work-dispatch" element={<WorkDispatchPage />} />
                <Route path="/apps" element={<AppsPage />} />
              </Route>
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
}

/**
 * 登录页是**外壳之外的独立一屏**：它挂在 `ProtectedRoute` 之外、不经过 `AppShell`，模糊层预算必须
 * 单独按这一屏计算。
 */
function renderLoginScreen(): HTMLElement {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const { container } = render(
    <ConfigProvider>
      <AntApp>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={['/login']}>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
  const screenRoot = container.firstElementChild;
  if (!(screenRoot instanceof HTMLElement)) {
    throw new Error('LoginPage root element is missing.');
  }
  return screenRoot;
}

function shellElement(): HTMLElement {
  const shell = screen.getByTestId('app-main').parentElement;
  if (shell === null) {
    throw new Error('AppShell root element is missing.');
  }
  return shell;
}

beforeEach(() => {
  apiMocks.getBinding.mockReset();
  apiMocks.getBinding.mockResolvedValue(binding());
  apiMocks.listWorkObjects.mockReset();
  apiMocks.listWorkObjects.mockResolvedValue(workObjectList());
  apiMocks.syncWorkObjects.mockReset();
  apiMocks.syncWorkObjects.mockResolvedValue(workObjectList());
  apiMocks.getWorkObject.mockReset();
  apiMocks.getWorkObject.mockResolvedValue(null);
  window.localStorage.clear();
  useNavigationStore.setState({ collapsed: false });
  useAuthStore.setState({ generation: 1, status: 'authenticated' });
  useAppearanceStore.setState({ background: 'bgA' });
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
});

/*
 * 2026-09-02「视觉方向改为 iOS 26 玻璃拟态」的模糊层预算：单屏带 `backdrop-filter` 的元素不得超过
 * 6 个，且只用于左导航、顶栏、内容面板、浮动窗、悬浮按钮这类大面。
 *
 * 下面的每屏断言都钉死**具体是哪几个面**，不是只写一个上限：
 * - 多加一层（例如给按钮或图标底座加模糊）→ 列表多出一项 → 变红；
 * - 把玻璃材质整体回滚 → 列表变空 → 同样变红。
 * 这样才满足「把本棒的视觉改动全部回滚，这个检查还会绿吗」的覆盖判据。
 */

describe('blur-layer detector', () => {
  it('reads declarations, not occurrences of the string', () => {
    const css = `
      /* backdrop-filter: blur(40px) 这行只是注释，不该被算进去 */
      .commentOnly { color: red; }
      .disabled { backdrop-filter: none; }
      .realGlass { background: rgb(255 255 255 / 58%); backdrop-filter: blur(32px) saturate(190%); }
      .prefixed { -webkit-backdrop-filter: blur(20px); }
      @media (min-width: 900px) { .responsiveGlass { backdrop-filter: blur(10px); } }
      .listed, .alsoListed > .child { backdrop-filter: blur(12px); }
      .parent { color: blue; }
      .onlyItsChild { color: green; .nested { backdrop-filter: blur(18px); } }
    `;

    expect(blurDeclaringSelectors(css).sort()).toEqual([
      '.alsoListed > .child',
      '.listed',
      '.onlyItsChild .nested',
      '.prefixed',
      '.realGlass',
      '.responsiveGlass',
    ]);
  });

  it('resolves declared class names to the names that reach the DOM', async () => {
    renderScreen('/work-objects');
    await screen.findByTestId('app-topbar');

    const sidebarLayer = findBlurLayers(shellElement()).find(
      (layer) => layer.declaredSelector === '.sidebar',
    );
    expect(sidebarLayer).toBeDefined();
    expect(sidebarLayer?.file).toBe('app/AppShell.module.css');
    expect(sidebarLayer?.element).toBe(
      screen.getByRole('complementary', { name: '主导航' }),
    );
    expect(sidebarLayer?.matchSelector).not.toBe('.sidebar');
    expect(sidebarLayer?.element.matches(sidebarLayer.matchSelector)).toBe(true);
  });

  /*
   * antd 与 `@ant-design/x` 自己带 `backdrop-filter`（Attachments 拖放区、Drawer 遮罩、Image 预览
   * 遮罩、Button 加载进度）。这些样式是运行时注入的，所以预算必须连它们一起数；`matches()` 解析不了
   * 的选择器会被当成「没命中」，等于给预算开一个看不见的口子，这里钉死它为空。
   */
  it('parses every blur selector it found, including the runtime-injected ones', async () => {
    renderScreen('/chat');
    await screen.findByTestId('app-topbar');

    expect(unparsableBlurSelectors(shellElement())).toEqual([]);
  });
});

describe('per-screen blur-layer budget', () => {
  it('keeps the work-objects screen on the three stationary surfaces', async () => {
    renderScreen('/work-objects');
    await waitFor(() =>
      expect(screen.getByText('核对本月采购流程')).toBeInTheDocument(),
    );

    const layers = describeBlurLayers(shellElement());
    expect(layers).toEqual([
      'app/AppShell.module.css .floatingEntry',
      'app/AppShell.module.css .sidebar',
      'app/AppShell.module.css .topbar',
    ]);
    expect(layers.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  it('keeps the AI assistant screen within budget without the floating entry', async () => {
    renderScreen('/chat');
    await screen.findByTestId('app-topbar');

    const layers = describeBlurLayers(shellElement());
    expect(layers).toEqual([
      'app/AppShell.module.css .sidebar',
      'app/AppShell.module.css .topbar',
      'pages/ChatPage.module.css .sessionRail',
    ]);
    expect(layers.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  it('keeps the enlarged floating panel within budget on top of a content panel', async () => {
    useAIDockStore.setState({ lastOpenMode: 'pinned', mode: 'pinned' });
    renderScreen('/work-objects');
    await waitFor(() =>
      expect(screen.getByText('核对本月采购流程')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('ai-dock')).toBeVisible();

    const layers = describeBlurLayers(shellElement());
    expect(layers).toEqual([
      'app/AIDock.module.css .dock',
      'app/AppShell.module.css .floatingEntry',
      'app/AppShell.module.css .sidebar',
      'app/AppShell.module.css .topbar',
    ]);
    expect(layers.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  it('adds no blur layer when a topbar popover is open on top of everything else', async () => {
    useAIDockStore.setState({ lastOpenMode: 'pinned', mode: 'pinned' });
    renderScreen('/work-objects');
    await waitFor(() =>
      expect(screen.getByText('核对本月采购流程')).toBeInTheDocument(),
    );
    const before = describeBlurLayers(shellElement());

    fireEvent.click(screen.getByRole('button', { name: '切换界面风格' }));
    expect(screen.getByRole('region', { name: '界面风格' })).toBeInTheDocument();

    const after = describeBlurLayers(shellElement());
    expect(after).toEqual(before);
    expect(after.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  /*
   * 2026-09-04 把用户菜单与系统状态改成了画板的 268px / 384px 弹出层。弹出层承载正文，按落盘的
   * 玻璃约束**不许买模糊**——三个顶栏弹层各开一次，列表都不能多出一项。上面那条只覆盖了「界面
   * 风格」，另外两个是本轮改过的面，这里逐个钉死。
   */
  it.each([
    ['用户菜单', '用户菜单，暂时取不到你的照片'],
    ['系统状态', '系统状态，暂无需要处理的项'],
  ])('adds no blur layer when the %s popover is open', async (region, trigger) => {
    renderScreen('/work-objects');
    await waitFor(() =>
      expect(screen.getByText('核对本月采购流程')).toBeInTheDocument(),
    );
    const before = describeBlurLayers(shellElement());

    fireEvent.click(await screen.findByRole('button', { name: trigger }));
    expect(screen.getByRole('region', { name: region })).toBeInTheDocument();

    const after = describeBlurLayers(shellElement());
    expect(after).toEqual(before);
    expect(after.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  it('keeps the dispatch draft screen on the three stationary surfaces', async () => {
    renderScreen('/work-dispatch');
    await screen.findByTestId('app-topbar');

    const layers = describeBlurLayers(shellElement());
    expect(layers).toEqual([
      'app/AppShell.module.css .floatingEntry',
      'app/AppShell.module.css .sidebar',
      'app/AppShell.module.css .topbar',
    ]);
    expect(layers.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  it('keeps the software-centre screen on the three stationary surfaces', async () => {
    renderScreen('/apps');
    await screen.findByTestId('oa-status');

    const layers = describeBlurLayers(shellElement());
    expect(layers).toEqual([
      'app/AppShell.module.css .floatingEntry',
      'app/AppShell.module.css .sidebar',
      'app/AppShell.module.css .topbar',
    ]);
    expect(layers.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  /*
   * 「新建应用」是一个盖住整屏的弹窗。弹窗自己不许再买一层模糊——遮罩按画板就是一层纯半透明黑。
   * 谁给遮罩或弹窗本体加上 `backdrop-filter`，这里的列表就会多出一项 → 变红。
   */
  it('adds no blur layer when the create-software dialog covers the screen', async () => {
    renderScreen('/apps');
    await screen.findByTestId('oa-status');
    const before = describeBlurLayers(shellElement());

    fireEvent.click(screen.getByRole('button', { name: /新建应用/ }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();

    const after = describeBlurLayers(document.body);
    expect(after).toEqual(before);
    expect(after.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  it('keeps the login screen on its single glass card', () => {
    const layers = describeBlurLayers(renderLoginScreen());

    expect(layers).toEqual(['pages/LoginPage.module.css .card']);
    expect(layers.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  /*
   * 2026-09-04 雨爷实机走查：「任务交办页面卡顿明显，特别是页面向下滑动时。」
   *
   * 只数层数是数不出这个问题的——交办页当时也只有 4 层，远在预算 6 以内。真正的成因是**哪个元素**买了
   * 模糊：底图是 `background: ... fixed`，而内容面板跟着文档流滚动，于是每滚一帧，面板背后的底图相对
   * 它就位移一次，浏览器必须重新采样并重新模糊整块面板区域。交办页是全站最长的一页（实测
   * scrollHeight 1048 > 视口 800），所以卡在这一页最明显。
   *
   * 所以在层数之外再钉一条**落点**约束：模糊只许出现在不随内容滚动的大面上。承载正文、高度由内容
   * 决定的面板一律不买模糊——这也正是 2026-09-02 视觉裁决原本就写着的「正文一律不靠玻璃承载」。
   *
   * 反证：把 `backdrop-filter` 加回任意一块内容面板（例如 `WorkDispatchPage.module.css .panel`），
   * 下面第一条的集合会多出一项、第二条对应那一屏的 `.content` 里会出现一层，两条同时变红。
   */
  it('declares blur only on the surfaces that never scroll with the content', () => {
    expect(
      fileBlurRules()
        .map((rule) => `${rule.file} ${rule.declaredSelector}`)
        .sort(),
    ).toEqual([
      'app/AIDock.module.css .dock',
      'app/AppShell.module.css .floatingEntry',
      'app/AppShell.module.css .sidebar',
      'app/AppShell.module.css .topbar',
      'pages/ChatPage.module.css .sessionRail',
      'pages/LoginPage.module.css .card',
    ]);
  });

  /*
   * 上面那条按文件钉，这一条按**渲染出来的那一屏**钉：主内容区 `<main id="main-content">` 里不许有
   * 模糊层。两条角度不同——文件那条防「新写一条模糊规则」，这条防「把某个大面挪进内容区」。
   *
   * AI 助手页的左栏 `.sessionRail` 是唯一留在内容区里的模糊面，理由写在这里而不是留成默契：它是
   * 定高的会话导航栏（`height` 由外壳给定、内部自己滚动），不随文档流伸长，也不承载正文，与左导航
   * 同类。哪天它改成随内容伸长，这条就该跟着改。
   */
  it.each([
    ['/work-dispatch', [] as string[]],
    ['/apps', [] as string[]],
    ['/work-objects', [] as string[]],
    ['/chat', ['pages/ChatPage.module.css .sessionRail']],
  ])('keeps the main content region of %s free of scroll-bound blur', async (path, expected) => {
    renderScreen(path);
    await screen.findByTestId('app-topbar');

    const content = document.querySelector('#main-content');
    expect(content).toBeInstanceOf(HTMLElement);
    expect(describeBlurLayers(content as HTMLElement)).toEqual(expected);
  });

  it('does not count the closed floating panel that is still in the tree', async () => {
    renderScreen('/work-objects');
    await screen.findByTestId('app-topbar');

    expect(screen.getByTestId('ai-dock')).not.toBeVisible();
    expect(describeBlurLayers(shellElement())).not.toContain(
      'app/AIDock.module.css .dock',
    );
  });
});
