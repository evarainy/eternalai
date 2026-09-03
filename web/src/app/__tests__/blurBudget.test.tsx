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
  it('keeps the work-objects screen on the five decided large surfaces', async () => {
    renderScreen('/work-objects');
    await waitFor(() =>
      expect(screen.getByText('核对本月采购流程')).toBeInTheDocument(),
    );

    const layers = describeBlurLayers(shellElement());
    expect(layers).toEqual([
      'app/AppShell.module.css .floatingEntry',
      'app/AppShell.module.css .sidebar',
      'app/AppShell.module.css .topbar',
      'pages/WorkObjectsPage.module.css .listSection',
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
      'pages/ChatPage.module.css .conversation',
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
      'pages/WorkObjectsPage.module.css .listSection',
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

  it('keeps the dispatch draft screen on its single content panel', async () => {
    renderScreen('/work-dispatch');
    await screen.findByTestId('app-topbar');

    const layers = describeBlurLayers(shellElement());
    expect(layers).toEqual([
      'app/AppShell.module.css .floatingEntry',
      'app/AppShell.module.css .sidebar',
      'app/AppShell.module.css .topbar',
      'features/work-dispatch/WorkDispatchPage.module.css .panel',
    ]);
    expect(layers.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  it('keeps the software-centre screen on its single content panel', async () => {
    renderScreen('/apps');
    await screen.findByTestId('oa-status');

    const layers = describeBlurLayers(shellElement());
    expect(layers).toEqual([
      'app/AppShell.module.css .floatingEntry',
      'app/AppShell.module.css .sidebar',
      'app/AppShell.module.css .topbar',
      'features/apps/AppsPage.module.css .panel',
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

  it('does not count the closed floating panel that is still in the tree', async () => {
    renderScreen('/work-objects');
    await screen.findByTestId('app-topbar');

    expect(screen.getByTestId('ai-dock')).not.toBeVisible();
    expect(describeBlurLayers(shellElement())).not.toContain(
      'app/AIDock.module.css .dock',
    );
  });
});
