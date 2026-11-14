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
  readMe: vi.fn(),
  getWorkObject: vi.fn(),
  listWorkObjects: vi.fn(),
  setHandlingMark: vi.fn(),
  syncWorkObjects: vi.fn(),
  bindPassword: vi.fn(),
  unbindPassword: vi.fn(),
  readWorkQueue: vi.fn(),
}));

vi.mock('../../generated/credential-bindings/credential-bindings', () => ({
  bindPasswordApiV1CredentialBindingsTargetSystemPut: apiMocks.bindPassword,
  getBindingApiV1CredentialBindingsTargetSystemGet: apiMocks.getBinding,
  unbindPasswordApiV1CredentialBindingsTargetSystemDelete: apiMocks.unbindPassword,
}));

vi.mock('../../features/work-dispatch/workObjectLifecycleApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../features/work-dispatch/workObjectLifecycleApi')>(),
  readWorkQueue: apiMocks.readWorkQueue,
}));

vi.mock('../../generated/me/me', () => ({
  readMeApiV1MeGet: apiMocks.readMe,
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
        oa_observation: {
          pending_state: 'current',
          revision: 1,
          last_seen_at: '2026-09-02T03:00:00Z',
          last_checked_at: '2026-09-02T03:00:00Z',
        },
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
    oa_sync: {
      status: 'succeeded',
      revision: 1,
      attempt_revision: 1,
      last_attempt_at: '2026-09-02T03:00:00Z',
      last_success_at: '2026-09-02T03:00:00Z',
      failure_code: null,
    },
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
  apiMocks.readMe.mockReset();
  apiMocks.readMe.mockResolvedValue({
    authenticated: true,
    display_name: '甲用户',
    org: { department_name: '部门乙', department_id: '22' },
    org_status: 'ok',
    avatar_path: '/api/v1/me/avatar',
  });
  apiMocks.getBinding.mockReset();
  apiMocks.getBinding.mockResolvedValue(binding());
  apiMocks.readWorkQueue.mockReset();
  apiMocks.readWorkQueue.mockResolvedValue({ ...workObjectList(), items: [] });
  apiMocks.listWorkObjects.mockReset();
  apiMocks.listWorkObjects.mockResolvedValue(workObjectList());
  apiMocks.syncWorkObjects.mockReset();
  apiMocks.syncWorkObjects.mockResolvedValue(workObjectList());
  apiMocks.getWorkObject.mockReset();
  apiMocks.getWorkObject.mockResolvedValue(null);
  window.localStorage.clear();
  useAuthStore.setState({ generation: 1, status: 'authenticated' });
  useAppearanceStore.setState({ skin: 'ink', size: 'm', clock: 'digit', fx: null });
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

// 按真实渲染集合核对预算，包含 wb-glass 全局规则和仍在用的页面、Dock 样式。

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

  /*
   * 2026-11-14 登录页改为左墨右纸、不再有玻璃卡，样本换成仍在用模块类名声明模糊的浮动面板
   * `AIDock.module.css .dock`（钉住时可见）。
   */
  it('resolves declared class names to the names that reach the DOM', async () => {
    useAIDockStore.setState({ lastOpenMode: 'pinned', mode: 'pinned' });
    renderScreen('/work-objects');
    const dock = await screen.findByTestId('ai-dock');
    const dockLayer = findBlurLayers(shellElement()).find(
      (layer) => layer.declaredSelector === '.dock',
    );
    expect(dockLayer).toBeDefined();
    expect(dockLayer?.file).toBe('app/AIDock.module.css');
    expect(dockLayer?.element).toBe(dock);
    expect(dockLayer?.matchSelector).not.toBe('.dock');
    expect(dockLayer?.element.matches(dockLayer.matchSelector)).toBe(true);
  });

  /*
   * antd 与 `@ant-design/x` 自己带 `backdrop-filter`（Attachments 拖放区、Drawer 遮罩、Image 预览
   * 遮罩、Button 加载进度）。这些样式是运行时注入的，所以预算必须连它们一起数；`matches()` 解析不了
   * 的选择器会被当成「没命中」，等于给预算开一个看不见的口子，这里钉死它为空。
   */
  it('parses every blur selector it found, including the runtime-injected ones', async () => {
    renderScreen('/chat');
    await screen.findByTestId('user-bar');

    expect(unparsableBlurSelectors(shellElement())).toEqual([]);
  });
});

describe('per-screen blur-layer budget', () => {
  it('keeps the work-objects screen on its floating glass entry', async () => {
    renderScreen('/work-objects');
    await waitFor(() =>
      expect(screen.getByText('核对本月采购流程')).toBeInTheDocument(),
    );

    const layers = describeBlurLayers(shellElement());
    expect(layers).toEqual([
      'shared/ui/workbench.css .wb-glass',
    ]);
    expect(layers.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  it('keeps the AI assistant screen within budget without the floating entry', async () => {
    renderScreen('/chat');
    await screen.findByTestId('user-bar');

    const layers = describeBlurLayers(shellElement());
    expect(layers).toEqual([]);
    expect(layers.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
    fireEvent.click(screen.getByRole('button', { name: '历史对话' }));
    expect(screen.getByRole('complementary', { name: '我问过的' })).toBeInTheDocument();
    expect(describeBlurLayers(shellElement())).toEqual([]);
  });

  it('keeps the enlarged floating panel within budget on top of a content panel', async () => {
    useAIDockStore.setState({ lastOpenMode: 'pinned', mode: 'pinned' });
    renderScreen('/work-objects');
    await waitFor(() =>
      expect(screen.getByText('核对本月采购流程')).toBeInTheDocument(),
    );
    expect(await screen.findByTestId('ai-dock')).toBeVisible();

    const layers = describeBlurLayers(shellElement());
    expect(layers).toEqual([
      'app/AIDock.module.css .dock',
      'shared/ui/workbench.css .wb-glass',
    ]);
    expect(layers.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  it('adds one glass layer within budget when settings opens above the Dock', async () => {
    useAIDockStore.setState({ lastOpenMode: 'pinned', mode: 'pinned' });
    renderScreen('/work-objects');
    await waitFor(() =>
      expect(screen.getByText('核对本月采购流程')).toBeInTheDocument(),
    );
    await screen.findByTestId('ai-dock');
    const before = describeBlurLayers(shellElement());

    fireEvent.click(screen.getByTestId('user-bar-identity'));
    expect(screen.getByRole('region', { name: '设置' })).toBeInTheDocument();

    const after = describeBlurLayers(shellElement());
    expect(after).toEqual([...before, 'shared/ui/workbench.css .wb-glass'].sort());
    expect(after.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  // 设置与系统状态共用一层毛玻璃浮层，打开后仍不得超过预算。
  it.each([
    ['设置', ['user-bar-identity']],
    ['系统状态', ['user-bar-identity', 'user-menu-system-status']],
  ])('adds one glass layer within budget when the %s popover is open', async (region, triggers) => {
    renderScreen('/work-objects');
    await waitFor(() =>
      expect(screen.getByText('核对本月采购流程')).toBeInTheDocument(),
    );
    const before = describeBlurLayers(shellElement());

    for (const trigger of triggers) {
      fireEvent.click(await screen.findByTestId(trigger));
    }
    expect(screen.getByRole('region', { name: region })).toBeInTheDocument();

    const after = describeBlurLayers(shellElement());
    expect(after).toEqual([...before, 'shared/ui/workbench.css .wb-glass'].sort());
    expect(after.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  it('keeps the dispatch draft screen on its floating glass entry', async () => {
    renderScreen('/work-dispatch?tab=new');
    await screen.findByTestId('user-bar');

    const layers = describeBlurLayers(shellElement());
    expect(layers).toEqual([
      'shared/ui/workbench.css .wb-glass',
    ]);
    expect(layers.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  it('keeps the software-centre screen on its floating glass entry', async () => {
    renderScreen('/apps');
    await screen.findByRole('button', { name: /OA 办公系统/ });

    const layers = describeBlurLayers(shellElement());
    expect(layers).toEqual([
      'shared/ui/workbench.css .wb-glass',
    ]);
    expect(layers.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  /*
   * 「新建应用」是一个盖住整屏的弹窗。弹窗自己不许再买一层模糊——遮罩按画板就是一层纯半透明黑。
   * 谁给遮罩或弹窗本体加上 `backdrop-filter`，这里的列表就会多出一项 → 变红。
   */
  it('adds no blur layer when the create-software dialog covers the screen', async () => {
    renderScreen('/apps');
    await screen.findByRole('button', { name: /OA 办公系统/ });
    const before = describeBlurLayers(shellElement());

    fireEvent.click(screen.getByRole('button', { name: '添加软件' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();

    const after = describeBlurLayers(document.body);
    expect(after).toEqual(before);
    expect(after.length).toBeLessThanOrEqual(BLUR_LAYER_BUDGET);
  });

  // 2026-11-14 起登录页是左墨右纸，不再有玻璃卡：这一屏一层模糊都不买。
  it('keeps the login screen free of blur layers', () => {
    const layers = describeBlurLayers(renderLoginScreen());

    expect(layers).toEqual([]);
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
      'shared/ui/workbench.css .wb-glass',
    ]);
  });

  /*
   * 上面那条按文件钉，这一条按**渲染出来的那一屏**钉：主内容区 `<main id="main-content">` 里不许有
   * 模糊层。两条角度不同——文件那条防「新写一条模糊规则」，这条防「把某个大面挪进内容区」。
   *
   * AI 助手页的案头、线程、输入框与按需展开的历史面板同样不买模糊。
   */
  it.each([
    ['/work-dispatch?tab=new', [] as string[]],
    ['/apps', [] as string[]],
    ['/work-objects', [] as string[]],
    ['/chat', [] as string[]],
  ])('keeps the main content region of %s free of scroll-bound blur', async (path, expected) => {
    renderScreen(path);
    await screen.findByTestId('user-bar');

    const content = document.querySelector('#main-content');
    expect(content).toBeInstanceOf(HTMLElement);
    expect(describeBlurLayers(content as HTMLElement)).toEqual(expected);
  });

  it('does not count the closed floating panel that is still in the tree', async () => {
    renderScreen('/work-objects');
    await screen.findByTestId('user-bar');

    expect(screen.queryByTestId('ai-dock')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '打开 AI 助手' }));
    await screen.findByTestId('ai-dock');
    fireEvent.click(screen.getByRole('button', { name: '关闭 AI 助手' }));
    expect(screen.getByTestId('ai-dock')).not.toBeVisible();
    expect(describeBlurLayers(shellElement())).not.toContain(
      'app/AIDock.module.css .dock',
    );
  });
});
