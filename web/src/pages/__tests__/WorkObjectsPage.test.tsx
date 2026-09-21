import { assertLogoutCache, trackAuthGenerations } from '../../test/logoutCache';
import { MemoryRouter } from 'react-router-dom';
import WorkDispatchPage from '../../features/work-dispatch/WorkDispatchPage';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest';
import { ApiError } from '../../api/mutator';
import { customInstance } from '../../api/mutator';
import { AuthenticationEffects } from '../../App';
import { AIDock } from '../../app/AIDock';
import type {
  InternalWorkObjectView,
  OAWorkObjectView,
  WorkObjectListResponse,
} from '../../generated/work-objects/work-objects.schemas';
import { useAIDockStore } from '../../stores/aiDockStore';
import { useAuthStore } from '../../stores/authStore';
import WorkObjectsPage from '../WorkObjectsPage';

const apiMocks = vi.hoisted(() => ({
  getWorkObject: vi.fn(),
  listWorkObjects: vi.fn(),
  historyWorkObjects: vi.fn(),
  completedWorkObjects: vi.fn(),
  setHandlingMark: vi.fn(),
  syncWorkObjects: vi.fn(),
}));

vi.mock('../../generated/work-objects/work-objects', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../generated/work-objects/work-objects')>(),
  getWorkObjectApiV1WorkObjectsWorkObjectIdGet: apiMocks.getWorkObject,
  listWorkObjectsApiV1WorkObjectsGet: (params: { oa_view?: string; completion?: string } | undefined) =>
    params?.oa_view === 'unconfirmed' ? apiMocks.historyWorkObjects(params) : params?.completion === 'completed' ? apiMocks.completedWorkObjects(params) : apiMocks.listWorkObjects(params),
  setWorkObjectHandlingMarkApiV1WorkObjectsWorkObjectIdHandlingMarkPatch:
    apiMocks.setHandlingMark,
  syncWorkObjectsApiV1WorkObjectsSyncPost: apiMocks.syncWorkObjects,
}));

const WORK_OBJECT: OAWorkObjectView = {
  assignee_display_name: '雨爷',
  due_at: '2026-08-20T08:00:00Z',
  handling_mark: null,
  handling_marked_at: null,
  handling_action: 'go_source_system',
  handling_capability_id: null,
  source_created_at: '2026-08-18 09:00:00',
  source_fetched_at: '2026-08-19T03:00:00Z',
  oa_observation: { pending_state: 'current', revision: 1, last_seen_at: '2026-08-19T03:00:00Z', last_checked_at: '2026-08-19T03:00:00Z' },
  source_kind: 'pending_workflow',
  source_received_at: '2026-08-18 09:05:00',
  source_ref: 'OA-WF-001',
  source_status: '待办',
  source_system: 'oa',
  source_title: '核对本月采购流程',
  source_workflow_type_id: 'purchase-review',
  state_authority: 'external_snapshot',
  task_record_id: null,
  work_object_id: 'work-object-1',
};

const OTHER_USER_WORK_OBJECT: OAWorkObjectView = {
  ...WORK_OBJECT,
  assignee_display_name: '其他用户',
  source_ref: 'OA-WF-OTHER',
  source_title: '其他用户的待办',
  work_object_id: 'work-object-other',
};

const INTERNAL_WORK_OBJECT: InternalWorkObjectView = {
  title: null,
  requirement: null,
  receipt_requirement: null,
  owner_department_id: null,
  initiator_ai_user_id: null,
  kind: null,
  target_kind: null,
  status: null,
  reminder_choices: null,
  reminder_delivery: null,
  version: null,
  created_at: null,
  updated_at: null,
  accepted_at: null,
  completed_at: null,
  assignee_display_name: '内部任务责任人',
  due_at: null,
  handling_mark: null,
  handling_marked_at: null,
  handling_action: 'view_only',
  handling_capability_id: null,
  source_created_at: null,
  source_fetched_at: null,
  source_kind: 'internal_task',
  source_received_at: null,
  source_ref: null,
  source_status: null,
  source_system: 'eternalai',
  source_title: null,
  source_workflow_type_id: null,
  state_authority: 'internal',
  task_record_id: null,
  work_object_id: 'internal-work-object-1',
};

const COMPLETED_INTERNAL: InternalWorkObjectView = {
  ...INTERNAL_WORK_OBJECT, work_object_id: 'internal-completed', source_kind: 'manual_dispatch',
  title: '合成办结事项', status: 'completed', version: 4, accepted_at: '2026-09-20T01:00:00.000001Z',
  completed_at: '2026-09-20T02:00:00.000001Z', due_at: '2026-09-01T00:00:00Z',
};

function listResponse(
  overrides: Partial<WorkObjectListResponse> = {},
): WorkObjectListResponse {
  return {
    items: [WORK_OBJECT],
    limit: 200,
    limit_exceeded: false,
    oa_sync: { status: 'succeeded', revision: 1, attempt_revision: 1, last_attempt_at: '2026-08-19T03:00:00Z', last_success_at: '2026-08-19T03:00:00Z', failure_code: null },
    ...overrides,
  };
}

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
}

function renderPage(
  queryClient = makeClient(),
  { withDock = false }: { withDock?: boolean } = {},
) {
  const rendered = render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <WorkObjectsPage />
          {withDock ? <AIDock /> : null}
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
  return { queryClient, ...rendered };
}

function AuthenticatedWorkObjects() {
  const status = useAuthStore((state) => state.status);
  return <><AuthenticationEffects />{status === 'authenticated' ? <WorkObjectsPage /> : <p>合成退出状态</p>}</>;
}

describe('WorkObjectsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    apiMocks.historyWorkObjects.mockReset().mockResolvedValue(listResponse({ items: [] }));
    apiMocks.completedWorkObjects.mockReset().mockResolvedValue(listResponse({ items: [] }));
    apiMocks.listWorkObjects.mockResolvedValue(listResponse());
    apiMocks.syncWorkObjects.mockResolvedValue(listResponse());
    apiMocks.getWorkObject.mockResolvedValue(WORK_OBJECT);
    apiMocks.setHandlingMark.mockResolvedValue({
      ...WORK_OBJECT,
      handling_mark: 'handled_elsewhere',
      handling_marked_at: '2026-08-19T03:10:00Z',
    });
  });

  it('includes internal work objects in the todo category', async () => {
    const mixedResponse = listResponse({
      items: [WORK_OBJECT, INTERNAL_WORK_OBJECT],
    });
    apiMocks.listWorkObjects.mockResolvedValue(mixedResponse);
    apiMocks.syncWorkObjects.mockResolvedValue(mixedResponse);

    renderPage();

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    expect(screen.getByTestId('work-count-urgent')).toHaveTextContent('1');
    expect(screen.getByTestId('work-count-todo')).toHaveTextContent('1');
    fireEvent.click(screen.getByRole('radio', { name: /待办/ }));
    expect(screen.getByText('内部任务责任人')).toBeInTheDocument();
  });

  it('logout_discards_all_views_without_breaking_revision_merge', async () => {
    const generations = trackAuthGenerations(useAuthStore);
    onTestFinished(generations.stop);
    const client = makeClient();
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({
      authenticated: true, display_name: 'Synthetic A', org: null, org_status: 'unavailable', avatar_path: null,
    })));
    let mounted: ReturnType<typeof render> | undefined;
    try {
      apiMocks.completedWorkObjects.mockResolvedValue(listResponse({ items: [COMPLETED_INTERNAL] }));
      mounted = render(<ConfigProvider><AntApp><QueryClientProvider client={client}>
        <AuthenticatedWorkObjects />
      </QueryClientProvider></AntApp></ConfigProvider>);
      await screen.findByText(WORK_OBJECT.source_title!);
      fireEvent.click(within(screen.getByText(WORK_OBJECT.source_title!).closest('tr')!).getByRole('button'));
      await waitFor(() => expect(apiMocks.getWorkObject).toHaveBeenCalled());
      await waitFor(() => expect(client.getQueryCache().findAll({ queryKey: ['work-objects', 1] })
        .filter((query) => query.state.data !== undefined)).toHaveLength(4));
      const activeKey = ['work-objects', 1, 'list', 'active', 'active'];
      const completedKey = ['work-objects', 1, 'list', 'active', 'completed'];
      const newer = listResponse({
        items: [{ ...WORK_OBJECT, oa_observation: { ...WORK_OBJECT.oa_observation, revision: 2 } }],
        oa_sync: { ...listResponse().oa_sync, revision: 2, attempt_revision: 2 },
      });
      apiMocks.listWorkObjects.mockResolvedValueOnce(newer);
      await act(async () => { await client.refetchQueries({ queryKey: activeKey }); });
      apiMocks.listWorkObjects.mockResolvedValueOnce(listResponse());
      apiMocks.completedWorkObjects.mockResolvedValueOnce(listResponse({
        items: [{ ...COMPLETED_INTERNAL, version: 3, title: 'Synthetic stale completed title' }],
      }));
      await act(async () => {
        await client.refetchQueries({ queryKey: activeKey });
        await client.refetchQueries({ queryKey: completedKey });
      });
      expect(client.getQueryData<WorkObjectListResponse>(activeKey)?.oa_sync.revision).toBe(2);
      expect(client.getQueryData<WorkObjectListResponse>(activeKey)?.items[0]).toMatchObject({
        oa_observation: { revision: 2 },
      });
      expect(client.getQueryData<WorkObjectListResponse>(completedKey)?.items[0]).toMatchObject({
        version: 4, title: COMPLETED_INTERNAL.title, handling_action: 'view_only',
      });
      let release!: (value: WorkObjectListResponse) => void;
      apiMocks.listWorkObjects.mockReturnValueOnce(new Promise<WorkObjectListResponse>((done) => { release = done; }));
      const late = client.refetchQueries({ queryKey: ['work-objects', 1, 'list', 'active', 'active'] });
      await waitFor(() => expect(client.isFetching()).toBe(1));
      fetchSpy.mockResolvedValueOnce(new Response('', { status: 401 }));
      await act(async () => {
        await expect(customInstance({ url: '/api/v1/me', method: 'GET' })).rejects.toMatchObject({ status: 401 });
      });
      expect(screen.getByText('合成退出状态')).toBeVisible();
      expect(client.getQueryCache().findAll({ queryKey: ['work-objects', 1] })).toEqual([]);
      assertLogoutCache(client, { generations, phase: 'unauthenticated', currentGeneration: useAuthStore.getState().generation });
      for (const query of client.getQueryCache().getAll()) expect(query.state.data).toBeUndefined();
      await act(async () => { release(listResponse()); await late; });
      expect(client.getQueryCache().findAll({ queryKey: ['work-objects', 1] })).toEqual([]);
      assertLogoutCache(client, { generations, phase: 'unauthenticated', currentGeneration: useAuthStore.getState().generation });
      apiMocks.listWorkObjects.mockResolvedValue(listResponse({ items: [OTHER_USER_WORK_OBJECT] }));
      apiMocks.syncWorkObjects.mockResolvedValue(listResponse({ items: [OTHER_USER_WORK_OBJECT] }));
      apiMocks.completedWorkObjects.mockResolvedValue(listResponse({ items: [] }));
      fetchSpy.mockImplementation(async () => new Response(JSON.stringify({
        authenticated: true, display_name: 'Synthetic B', org: null, org_status: 'unavailable', avatar_path: null,
      })));
      act(() => useAuthStore.getState().markAuthenticated());
      await screen.findByText(OTHER_USER_WORK_OBJECT.source_title!);
      expect(screen.queryByText(WORK_OBJECT.source_title!)).toBeNull();
      expect(client.getQueryCache().findAll({ queryKey: ['work-objects', 1] })).toEqual([]);
      assertLogoutCache(client, { generations, phase: 'identity-ready', currentGeneration: useAuthStore.getState().generation });
      for (const query of client.getQueryCache().getAll()) {
        if (query.state.data === undefined) continue;
        if (query.queryKey[0] === 'me') {
          expect(query.state.data).toMatchObject({ display_name: 'Synthetic B' });
        } else {
          expect(query.queryKey[0]).toBe('work-objects');
          const data = query.state.data as WorkObjectListResponse;
          expect(data.items).toBeDefined();
          for (const item of data.items) expect(item).toEqual(OTHER_USER_WORK_OBJECT);
        }
      }
    } finally {
      mounted?.unmount();
      fetchSpy.mockRestore();
    }
  });

  it('registers the visible Work Objects page through the nine-field contract', async () => {
    const capableItem: OAWorkObjectView = {
      ...WORK_OBJECT,
      handling_action: 'ai_draft',
      handling_capability_id: 'oa.work.read',
    };
    const response = listResponse({ items: [capableItem] });
    apiMocks.listWorkObjects.mockResolvedValue(response);
    apiMocks.syncWorkObjects.mockResolvedValue(response);
    apiMocks.getWorkObject.mockResolvedValue(capableItem);

    const page = renderPage();

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    expect(
      useAIDockStore.getState().pageContextDeclaration?.work_object_refs,
    ).toEqual([]);
    fireEvent.click(screen.getByRole('button', { name: '让 AI 先写' }));
    await waitFor(() => {
      const context = useAIDockStore.getState().pageContextDeclaration;
      expect(context?.surface_id).toBe('work-objects');
      expect(context?.organization_scope).toBeNull();
      expect(context?.work_object_refs).toEqual([
        { work_object_id: 'work-object-1' },
      ]);
      expect(context?.source_refs).toEqual([
        { source_system: 'oa', source_ref: 'OA-WF-001' },
      ]);
      expect(context?.filters).toEqual([
        {
          field: 'view',
          operator: 'equals',
          value: 'urgent',
          source: 'visible_control',
        },
      ]);
      expect(context?.allowed_capabilities).toEqual(['oa.work.read']);
      expect(context?.visibility).toBe('principal');
    });

    page.unmount();
    expect(useAIDockStore.getState().pageContextDeclaration).toBeNull();
  });

  it('keeps the page usable when OA freshness is not a UTC Z timestamp', async () => {
    const invalidTimestampItem: OAWorkObjectView = {
      ...WORK_OBJECT,
      source_fetched_at: '2026-08-19T11:00:00+08:00',
    };
    const response = listResponse({ items: [invalidTimestampItem], oa_sync: { ...listResponse().oa_sync, last_success_at: invalidTimestampItem.source_fetched_at } });
    apiMocks.listWorkObjects.mockResolvedValue(response);
    apiMocks.syncWorkObjects.mockResolvedValue(response);
    useAIDockStore.setState({ lastOpenMode: 'drawer', mode: 'drawer' });

    renderPage(makeClient(), { withDock: true });

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    expect(
      await screen.findByText('当前页面上下文不可用；AI 不会读取本页数据。'),
    ).toBeVisible();
    expect(screen.getByText('正在协助：未绑定页面上下文')).toBeVisible();
    expect(useAIDockStore.getState().pageContextDeclaration).toBeNull();
  });

  it('keeps the page usable when an OA source reference matches a credential shape', async () => {
    const credentialShapedSourceRef: OAWorkObjectView = {
      ...WORK_OBJECT,
      source_ref: '11010519491231002X',
    };
    const response = listResponse({ items: [credentialShapedSourceRef] });
    apiMocks.listWorkObjects.mockResolvedValue(response);
    apiMocks.syncWorkObjects.mockResolvedValue(response);
    apiMocks.getWorkObject.mockResolvedValue(credentialShapedSourceRef);
    useAIDockStore.setState({ lastOpenMode: 'drawer', mode: 'drawer' });

    renderPage(makeClient(), { withDock: true });

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '去 OA 办' }));
    expect(
      await screen.findByText('当前页面上下文不可用；AI 不会读取本页数据。'),
    ).toBeVisible();
    expect(
      screen.getByRole('heading', { level: 1, name: '工作事项' }),
    ).toBeVisible();
    expect(screen.getByText('正在协助：未绑定页面上下文')).toBeVisible();
    expect(useAIDockStore.getState().pageContextDeclaration).toBeNull();
  });

  it('registers only the opened row and never hidden pagination rows', async () => {
    const items = Array.from({ length: 11 }, (_, index): OAWorkObjectView => ({
      ...WORK_OBJECT,
      source_ref: `OA-PAGE-${index + 1}`,
      source_title: `分页事项 ${index + 1}`,
      work_object_id: `work-page-${index + 1}`,
    }));
    const response = listResponse({ items });
    apiMocks.listWorkObjects.mockResolvedValue(response);
    apiMocks.syncWorkObjects.mockResolvedValue(response);
    apiMocks.getWorkObject.mockResolvedValue(items[0] as OAWorkObjectView);

    renderPage();

    expect(await screen.findByText('分页事项 1')).toBeInTheDocument();
    expect(screen.queryByText('分页事项 11')).not.toBeInTheDocument();
    expect(
      useAIDockStore.getState().pageContextDeclaration?.work_object_refs,
    ).toEqual([]);
    const firstVisibleAction = screen.getAllByRole('button', { name: '去 OA 办' })[0];
    expect(firstVisibleAction).toBeDefined();
    fireEvent.click(firstVisibleAction as HTMLElement);

    await waitFor(() =>
      expect(
        useAIDockStore.getState().pageContextDeclaration?.work_object_refs,
      ).toEqual([{ work_object_id: 'work-page-1' }]),
    );
    expect(useAIDockStore.getState().pageContextDeclaration?.source_refs).toEqual([
      { source_system: 'oa', source_ref: 'OA-PAGE-1' },
    ]);
  });

  it('renders exactly one backend-projected handling action per row', async () => {
    const items: OAWorkObjectView[] = [
      {
        ...WORK_OBJECT,
        handling_action: 'ai_draft',
        handling_capability_id: 'oa.handle.full',
        source_ref: 'OA-AI',
        source_title: 'AI 起草事项',
        work_object_id: 'work-ai',
      },
      {
        ...WORK_OBJECT,
        handling_action: 'self_serve',
        handling_capability_id: 'oa.handle.assisted',
        source_ref: 'OA-SELF',
        source_title: '自行办理事项',
        work_object_id: 'work-self',
      },
      {
        ...WORK_OBJECT,
        source_ref: 'OA-SOURCE',
        source_title: '回源办理事项',
        work_object_id: 'work-source',
      },
      {
        ...WORK_OBJECT,
        handling_action: 'view_only',
        source_ref: 'OA-VIEW',
        source_title: '只读事项',
        work_object_id: 'work-view',
      },
    ];
    const response = listResponse({ items });
    apiMocks.listWorkObjects.mockResolvedValue(response);
    apiMocks.syncWorkObjects.mockResolvedValue(response);

    renderPage();

    expect(await screen.findByRole('button', { name: '让 AI 先写' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '我自己办' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '去 OA 办' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '先看看' })).toBeInTheDocument();
    const dataRows = screen.getAllByRole('row').slice(1);
    for (const row of dataRows) {
      expect(within(row).getAllByRole('button')).toHaveLength(1);
    }
  });

  it('defines Urgent as overdue, due today or tomorrow, or waiting for confirmation, and can switch to Todo', async () => {
    const futureItem: OAWorkObjectView = {
      ...WORK_OBJECT,
      due_at: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(),
      source_ref: 'OA-FUTURE',
      source_title: '下周再办的事项',
      work_object_id: 'work-future',
    };
    const pendingConfirmation: OAWorkObjectView = {
      ...futureItem,
      handling_mark: 'pending_sync_confirmation',
      source_ref: 'OA-CONFIRM',
      source_title: '等待确认的事项',
      work_object_id: 'work-confirm',
    };
    const response = listResponse({
      items: [WORK_OBJECT, futureItem, pendingConfirmation],
    });
    apiMocks.listWorkObjects.mockResolvedValue(response);
    apiMocks.syncWorkObjects.mockResolvedValue(response);

    renderPage();

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    expect(screen.getByText('等待确认的事项')).toBeInTheDocument();
    expect(screen.queryByText('下周再办的事项')).not.toBeInTheDocument();
    expect(screen.getByTestId('work-count-urgent')).toHaveTextContent('2');
    expect(screen.getByTestId('work-count-todo')).toHaveTextContent('1');
    expect(screen.getByTestId('work-count-done')).toHaveTextContent('0');

    fireEvent.click(screen.getByRole('radio', { name: /待办/ }));

    expect(screen.getByText('下周再办的事项')).toBeInTheDocument();
    expect(screen.queryByText('核对本月采购流程')).not.toBeInTheDocument();
    expect(screen.queryByText('等待确认的事项')).not.toBeInTheDocument();
    await waitFor(() => expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1));
  });

  /*
   * 「已完成」后端没有数据源。这条钉死两件事：计数只给占位符（既不是编出来的数字，也不是会被读成
   * 「我没有已完成的」的 0），空态把缺口本身说出来。任何一处改成显示数字都会变红。
   */
  it('shows the Done category without inventing a count and names the missing data source', async () => {
    renderPage();

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    const doneCount = screen.getByTestId('work-count-done');
    expect(doneCount).toHaveTextContent('0');
    expect(apiMocks.completedWorkObjects).toHaveBeenCalledWith({ oa_view: 'active', completion: 'completed' });

    fireEvent.click(screen.getByRole('radio', { name: /已完成/ }));

    expect(screen.getByText('近 30 天暂无内部办结事项。')).toBeInTheDocument();
    expect(
      screen.getByText('OA 已办结数据尚未接入，请到 OA 核对。'),
    ).toBeInTheDocument();
    expect(screen.queryByText('核对本月采购流程')).not.toBeInTheDocument();
  });

  /*
   * 画板上首屏顶部只有分类控件与列表。把 hero 卡或页面内凭证卡加回来，这条就会变红。
   */
  it('drops the hero card and the in-page credential card from the first screen', async () => {
    renderPage();

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    expect(screen.queryByText('后台同步凭证')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '绑定 OA 密码' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('每一行都写明责任人、截止时间和下一步。'),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('heading', { level: 1, name: '工作事项' }),
    ).toBeInTheDocument();
  });

  it('keeps source and state text visible and pairs status color with an icon and words', async () => {
    renderPage();

    expect(await screen.findByText(/OA-WF-001/)).toBeInTheDocument();
    expect(screen.getByText('OA 办公系统')).toBeInTheDocument();
    expect(screen.getByText(/上次OA步骤 待办/)).toBeInTheDocument();
    expect(screen.getAllByText(/^数据截至 /).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('筛选')).toBeInTheDocument();
    const overdueStatus = within(screen.getByRole('table')).getByText(/已逾期/);
    expect(overdueStatus.querySelector('[aria-hidden="true"]')).not.toBeNull();
  });

  it('switches to compact rows when Dock is pinned without enabling horizontal table scrolling', async () => {
    useAIDockStore.setState({ mode: 'pinned', lastOpenMode: 'pinned' });
    const { container } = renderPage();

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    expect(container.querySelector('[data-density="compact"]')).not.toBeNull();
    expect(container.querySelector('.ant-table-body')).toBeNull();
    expect(screen.getByRole('columnheader', { name: /事项/ })).toBeInTheDocument();
    expect(
      screen.getByRole('columnheader', { name: /责任人 \/ 部门/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /截止时间/ })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /下一动作/ })).toBeInTheDocument();
  });

  it('explains why Urgent is empty and gives a concrete next step', async () => {
    const futureItem: OAWorkObjectView = {
      ...WORK_OBJECT,
      due_at: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(),
      source_ref: 'OA-FUTURE',
      source_title: '以后再办的事项',
      work_object_id: 'work-future',
    };
    const response = listResponse({ items: [futureItem] });
    apiMocks.listWorkObjects.mockResolvedValue(response);
    apiMocks.syncWorkObjects.mockResolvedValue(response);

    renderPage();

    expect(await screen.findByText('现在没有要紧的事。')).toBeInTheDocument();
    expect(
      screen.getByText('下一步：到「待办」里还有 1 件。'),
    ).toBeInTheDocument();
    await waitFor(() => expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1));
  });

  it('keeps the saved OA snapshot visible when sync fails and warns about bounded results', async () => {
    apiMocks.listWorkObjects.mockResolvedValue(
      listResponse({ limit_exceeded: true }),
    );
    apiMocks.syncWorkObjects.mockRejectedValueOnce(
      new ApiError(503, 'oa_sync_failed', 'OA 暂时不可用'),
    );

    const { container } = renderPage();

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    expect(await screen.findByText('OA 同步失败')).toBeInTheDocument();
    expect(
      screen.getByText('仍在显示上次成功拉取的数据；请以批次数据截至时间为准。'),
    ).toBeInTheDocument();
    expect(screen.getByText(/oa_sync_failed: OA 暂时不可用/)).toBeInTheDocument();
    expect(screen.getByText(/上次OA步骤 待办/)).toBeInTheDocument();
    expect(screen.getByText('事项超过首版展示上限 200 条')).toBeInTheDocument();
    expect(
      screen.getByText(
        '服务端最多返回前 200 条：有截止时间的优先，截止越早越靠前；截止时间相同或均未设置时，按首次入库时间从新到旧选取。本页筛选、排序和分页仅整理已取得的事项，不代表全部事项。',
      ),
    ).toBeInTheDocument();
    expect(container.querySelector('.ant-pagination')).toBeInTheDocument();
    expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1);
  });

  it('discloses bounded selection without claiming complete results', async () => {
    apiMocks.listWorkObjects.mockResolvedValue(
      listResponse({ limit_exceeded: true }),
    );
    apiMocks.syncWorkObjects.mockResolvedValue(
      listResponse({ limit_exceeded: true }),
    );

    const { unmount } = renderPage();

    expect(await screen.findByText('事项超过首版展示上限 200 条')).toBeInTheDocument();
    expect(
      screen.getByText(
        '服务端最多返回前 200 条：有截止时间的优先，截止越早越靠前；截止时间相同或均未设置时，按首次入库时间从新到旧选取。本页筛选、排序和分页仅整理已取得的事项，不代表全部事项。',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/OA 里的全部事项/)).not.toBeInTheDocument();
    unmount();
    apiMocks.listWorkObjects.mockResolvedValue(listResponse());
    apiMocks.syncWorkObjects.mockResolvedValue(listResponse());
    renderPage();

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    expect(
      screen.queryByText('事项超过首版展示上限 200 条'),
    ).not.toBeInTheDocument();
  });

  it('routes an expired OA identity to the existing reauthentication state', async () => {
    apiMocks.syncWorkObjects.mockRejectedValueOnce(
      new ApiError(
        409,
        'oa_reauthentication_required',
        'OA 凭证已失效',
      ),
    );
    renderPage();

    expect(
      await screen.findByText('OA 凭证已失效，需要重新认证'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新认证' }));

    expect(useAuthStore.getState().status).toBe('unauthenticated');
  });

  it('keeps a binding-scope clarification distinct without logging out', async () => {
    apiMocks.syncWorkObjects.mockRejectedValueOnce(
      new ApiError(
        409,
        'oa_binding_scope_required',
        'OA 账号范围需要明确',
      ),
    );
    renderPage();

    expect(
      await screen.findByText('需要先明确 OA 账号范围'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('请先在账号绑定中明确 OA 账号范围后再刷新。'),
    ).toBeInTheDocument();
    expect(screen.getByText('核对本月采购流程')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重新认证' })).not.toBeInTheDocument();
    expect(useAuthStore.getState().status).toBe('authenticated');
  });

  it('rejects a prior authentication generation response after another user logs in', async () => {
    let resolveOldSync!: (response: WorkObjectListResponse) => void;
    const oldSync = new Promise<WorkObjectListResponse>((resolve) => {
      resolveOldSync = resolve;
    });
    const otherUserResponse = listResponse({ items: [OTHER_USER_WORK_OBJECT] });
    apiMocks.listWorkObjects.mockReset().mockImplementation(() => Promise.resolve(
      useAuthStore.getState().generation === 1 ? listResponse() : otherUserResponse,
    ));
    apiMocks.syncWorkObjects.mockReset().mockImplementation(() =>
      useAuthStore.getState().generation === 1 ? oldSync : Promise.resolve(otherUserResponse),
    );
    const firstPage = renderPage();

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    await waitFor(() => expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1));
    firstPage.unmount();
    act(() => {
      useAuthStore.getState().markUnauthenticated(1);
      useAuthStore.getState().markAuthenticated();
      firstPage.queryClient.clear();
    });
    const otherUserGeneration = useAuthStore.getState().generation;
    renderPage(firstPage.queryClient);

    expect(await screen.findByText('其他用户的待办')).toBeInTheDocument();
    await waitFor(() => expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(2));
    await act(async () => {
      resolveOldSync(listResponse());
      await oldSync;
    });

    await waitFor(() => {
      expect(
        firstPage.queryClient.getQueryData([
          'work-objects',
          otherUserGeneration, 'list', 'active', 'active',
        ]),
      ).toEqual(otherUserResponse);
    });
    expect(screen.queryByText('核对本月采购流程')).not.toBeInTheDocument();
  });

  it('keeps a newer handling mark when an older sync response arrives last', async () => {
    let resolveSync!: (response: WorkObjectListResponse) => void;
    const pendingSync = new Promise<WorkObjectListResponse>((resolve) => {
      resolveSync = resolve;
    });
    const refreshedSource: OAWorkObjectView = {
      ...WORK_OBJECT,
      source_fetched_at: '2026-08-19T03:05:00Z',
      source_status: 'OA_UPDATED',
    };
    apiMocks.syncWorkObjects.mockReset().mockReturnValue(pendingSync);
    renderPage();
    await screen.findByText('核对本月采购流程');
    await waitFor(() => expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '去 OA 办' }));
    await screen.findByText(/OA 状态数据截至/);
    fireEvent.click(
      screen.getByRole('button', { name: '标记为已在别处处理' }),
    );
    expect(await screen.findByText('处理痕迹已记录；OA 状态未被修改')).toBeInTheDocument();

    await act(async () => {
      apiMocks.listWorkObjects.mockResolvedValue(listResponse({ items: [refreshedSource] }));
      apiMocks.getWorkObject.mockResolvedValue(refreshedSource);
      apiMocks.listWorkObjects.mockResolvedValue(listResponse({ items: [refreshedSource] }));
    apiMocks.getWorkObject.mockResolvedValue(refreshedSource);
    resolveSync(listResponse({ items: [refreshedSource] }));
      await pendingSync;
    });

    await waitFor(() => {
      expect(screen.getByText(/上次OA步骤 OA_UPDATED/)).toBeInTheDocument();
      expect(screen.getByText('OA_UPDATED')).toBeInTheDocument();
      expect(screen.getAllByText('已在别处处理').length).toBeGreaterThanOrEqual(2);
    });
  });

  it('keeps a newer OA snapshot when an older mark response arrives last', async () => {
    let resolveSync!: (response: WorkObjectListResponse) => void;
    let resolveMark!: (response: OAWorkObjectView) => void;
    const pendingSync = new Promise<WorkObjectListResponse>((resolve) => {
      resolveSync = resolve;
    });
    const pendingMark = new Promise<OAWorkObjectView>((resolve) => {
      resolveMark = resolve;
    });
    const refreshedSource: OAWorkObjectView = {
      ...WORK_OBJECT,
      source_fetched_at: '2026-08-19T03:05:00Z',
      source_status: 'OA_UPDATED',
    };
    const markedOldSource: OAWorkObjectView = {
      ...WORK_OBJECT,
      handling_mark: 'handled_elsewhere',
      handling_marked_at: '2026-08-19T03:10:00Z',
    };
    apiMocks.syncWorkObjects.mockReset().mockReturnValue(pendingSync);
    apiMocks.setHandlingMark.mockReset().mockReturnValue(pendingMark);
    renderPage();
    await screen.findByText('核对本月采购流程');
    await waitFor(() => expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '去 OA 办' }));
    await screen.findByText(/OA 状态数据截至/);
    fireEvent.click(
      screen.getByRole('button', { name: '标记为已在别处处理' }),
    );
    await waitFor(() => expect(apiMocks.setHandlingMark).toHaveBeenCalledTimes(1));

    apiMocks.listWorkObjects.mockResolvedValue(listResponse({ items: [refreshedSource] }));
    apiMocks.getWorkObject.mockResolvedValue(refreshedSource);
    resolveSync(listResponse({ items: [refreshedSource] }));
    await waitFor(() => {
      expect(screen.getByText(/上次OA步骤 OA_UPDATED/)).toBeInTheDocument();
      expect(screen.getByText('OA_UPDATED')).toBeInTheDocument();
    });
    resolveMark(markedOldSource);

    expect(await screen.findByText('处理痕迹已记录；OA 状态未被修改')).toBeInTheDocument();
    expect(screen.getByText(/上次OA步骤 OA_UPDATED/)).toBeInTheDocument();
    expect(screen.getByText('OA_UPDATED')).toBeInTheDocument();
    expect(screen.getAllByText('已在别处处理').length).toBeGreaterThanOrEqual(2);
  });

  it('ignores an older detail query that arrives after sync updates its cache', async () => {
    let resolveSync!: (response: WorkObjectListResponse) => void;
    let resolveDetail!: (response: OAWorkObjectView) => void;
    const pendingSync = new Promise<WorkObjectListResponse>((resolve) => {
      resolveSync = resolve;
    });
    const pendingDetail = new Promise<OAWorkObjectView>((resolve) => {
      resolveDetail = resolve;
    });
    const refreshedSource: OAWorkObjectView = {
      ...WORK_OBJECT,
      source_fetched_at: '2026-08-19T03:05:00Z',
      source_status: 'OA_UPDATED',
      task_record_id: 'task-new',
    };
    apiMocks.syncWorkObjects.mockReset().mockReturnValue(pendingSync);
    apiMocks.getWorkObject.mockReset().mockReturnValue(pendingDetail);
    renderPage();
    await screen.findByText('核对本月采购流程');
    await waitFor(() => expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '去 OA 办' }));
    await waitFor(() => expect(apiMocks.getWorkObject).toHaveBeenCalledTimes(1));

    apiMocks.listWorkObjects.mockResolvedValue(listResponse({ items: [refreshedSource] }));
    apiMocks.getWorkObject.mockResolvedValue(refreshedSource);
    resolveSync(listResponse({ items: [refreshedSource] }));
    expect(await screen.findByText('task-new')).toBeInTheDocument();
    expect(screen.getByText(/上次OA步骤 OA_UPDATED/)).toBeInTheDocument();
    expect(screen.getByText('OA_UPDATED')).toBeInTheDocument();
    await act(async () => {
      resolveDetail(WORK_OBJECT);
      await pendingDetail;
    });

    expect(screen.getByText('task-new')).toBeInTheDocument();
    expect(screen.getByText(/上次OA步骤 OA_UPDATED/)).toBeInTheDocument();
    expect(screen.getByText('OA_UPDATED')).toBeInTheDocument();
  });

  it('keeps a pending detail usable when an overflowing sync batch excludes it', async () => {
    let resolveSync!: (response: WorkObjectListResponse) => void;
    let resolveDetail!: (response: OAWorkObjectView) => void;
    const pendingSync = new Promise<WorkObjectListResponse>((resolve) => {
      resolveSync = resolve;
    });
    const pendingDetail = new Promise<OAWorkObjectView>((resolve) => {
      resolveDetail = resolve;
    });
    apiMocks.syncWorkObjects.mockReset().mockReturnValue(pendingSync);
    apiMocks.getWorkObject.mockReset().mockReturnValue(pendingDetail);
    renderPage();
    await screen.findByText('核对本月采购流程');
    await waitFor(() => expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '去 OA 办' }));
    await waitFor(() => expect(apiMocks.getWorkObject).toHaveBeenCalledTimes(1));

    apiMocks.listWorkObjects.mockResolvedValue(listResponse({ items: [OTHER_USER_WORK_OBJECT], oa_sync: { ...listResponse().oa_sync, revision: 2, attempt_revision: 2 }, limit_exceeded: true }));
    apiMocks.historyWorkObjects.mockResolvedValue(listResponse({ items: [], oa_sync: { ...listResponse().oa_sync, revision: 2, attempt_revision: 2 } }));
    resolveSync(
      listResponse({
        items: [OTHER_USER_WORK_OBJECT],
        oa_sync: { ...listResponse().oa_sync, revision: 2, attempt_revision: 2 },
        limit_exceeded: true,
      }),
    );
    expect(await screen.findByText('其他用户的待办')).toBeInTheDocument();
    resolveDetail(WORK_OBJECT);

    expect(await screen.findByText(/OA 状态数据截至/)).toBeInTheDocument();
    expect(screen.getByText(/OA 待办 · OA-WF-001/)).toBeInTheDocument();
    expect(screen.queryByText('详情读取失败')).not.toBeInTheDocument();
  });

  it('ignores an older list refetch that arrives after a handling mark', async () => {
    let resolveList!: (response: WorkObjectListResponse) => void;
    const pendingList = new Promise<WorkObjectListResponse>((resolve) => {
      resolveList = resolve;
    });
    apiMocks.listWorkObjects.mockReset().mockResolvedValue(listResponse());
    const page = renderPage();
    await screen.findByText('核对本月采购流程');
    fireEvent.click(screen.getByRole('button', { name: '去 OA 办' }));
    await screen.findByText(/OA 状态数据截至/);
    apiMocks.listWorkObjects.mockReturnValue(pendingList);
    void page.queryClient.refetchQueries({
      queryKey: ['work-objects', useAuthStore.getState().generation, 'list', 'active', 'active'],
      exact: true,
    });
    await waitFor(() => expect(apiMocks.listWorkObjects.mock.results.at(-1)?.value).toBe(pendingList));
    fireEvent.click(
      screen.getByRole('button', { name: '标记为已在别处处理' }),
    );
    await act(async () => {
      resolveList(listResponse());
      await pendingList;
    });

    expect(await screen.findByText('处理痕迹已记录；OA 状态未被修改')).toBeInTheDocument();
    expect(screen.getAllByText('已在别处处理').length).toBeGreaterThanOrEqual(2);
  });

  it('shows the source freshness in detail and records a local handling mark only', async () => {
    renderPage();
    await screen.findByText('核对本月采购流程');

    fireEvent.click(screen.getByRole('button', { name: '去 OA 办' }));

    expect(await screen.findByText(/OA 状态数据截至/)).toBeInTheDocument();
    expect(
      screen.getByText('这条事项的状态权威在 OA，请在 OA 中办理。'),
    ).toBeInTheDocument();
    expect(screen.getByText(/处理痕迹只记录你在 EternalAI 中的声明，不会改写 OA 状态。/)).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: '标记为已在别处处理' }),
    );

    await waitFor(() => {
      expect(apiMocks.setHandlingMark).toHaveBeenCalledWith('work-object-1', {
        mark: 'handled_elsewhere',
      });
    });
    expect(await screen.findByText('处理痕迹已记录；OA 状态未被修改')).toBeInTheDocument();
    const handlingTimeRow = screen
      .getByText('处理痕迹记录时间')
      .closest('tr');
    expect(handlingTimeRow).not.toBeNull();
    expect(handlingTimeRow).toHaveTextContent('2026');
    expect(handlingTimeRow).not.toHaveTextContent('未记录');
  });
  it('shows empty success batch time and unconfirmed history without a done count', async () => {
    const stamp = '2026-09-16T01:00:00Z';
    const sync = { ...listResponse().oa_sync, revision: 2, attempt_revision: 2,
      last_attempt_at: stamp, last_success_at: stamp };
    const history: OAWorkObjectView = { ...WORK_OBJECT, handling_action: 'view_only',
      handling_capability_id: null, oa_observation: { ...WORK_OBJECT.oa_observation,
        pending_state: 'unconfirmed', revision: 2, last_checked_at: stamp } };
    apiMocks.listWorkObjects.mockResolvedValue(listResponse({ items: [], oa_sync: sync }));
    apiMocks.syncWorkObjects.mockResolvedValue(listResponse({ items: [], oa_sync: sync }));
    apiMocks.historyWorkObjects.mockResolvedValue(listResponse({ items: [history], oa_sync: sync }));
    apiMocks.getWorkObject.mockResolvedValue(history);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('work-count-todo')).toHaveTextContent('0'));
    expect(screen.getByTestId('work-count-urgent')).toHaveTextContent('0');
    expect(screen.getByTestId('work-count-done')).toHaveTextContent('0');
    expect(screen.getByText(/数据截至/)).toHaveTextContent('2026年9月16日');
    fireEvent.click(screen.getByRole('radio', { name: /待办/ }));
    expect(screen.getByText('当前无待办。')).toBeVisible();
    expect(screen.queryByText('还没有取得可显示的工作事项。')).toBeNull();
    expect(screen.queryByText(/下一步：先在顶栏确认 OA 绑定/)).toBeNull();
    const historySection = screen.getByText('当前待办未再确认（1）').closest('details')!;
    fireEvent.click(screen.getByText('当前待办未再确认（1）'));
    expect(historySection).toHaveTextContent('可能已转交、撤回或办结，请到OA核对');
    expect(within(historySection).queryByRole('button', { name: '去 OA 办' })).toBeNull();
    fireEvent.click(within(historySection).getByRole('button', { name: '先看看' }));
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent('当前待办未再确认'));
    expect(apiMocks.historyWorkObjects).toHaveBeenCalledWith({ oa_view: 'unconfirmed' });
  });

  it.each([
    ['never', '尚未成功核对 OA 待办'],
    ['running', 'OA 同步进行中，当前显示已保存数据'],
    ['failed', '最近一次 OA 同步失败，保留已保存数据'],
  ] as const)('does not call an empty %s batch a successful empty snapshot', async (status, title) => {
    const sync: WorkObjectListResponse['oa_sync'] = {
      status, revision: 0, attempt_revision: status === 'never' ? 0 : 1,
      last_attempt_at: status === 'never' ? null : '2026-09-16T04:00:00Z',
      last_success_at: null,
      failure_code: status === 'failed' ? 'upstream_unavailable' : null,
    };
    const response = listResponse({ items: [], oa_sync: sync });
    apiMocks.listWorkObjects.mockResolvedValue(response);
    apiMocks.historyWorkObjects.mockResolvedValue(response);
    apiMocks.syncWorkObjects.mockResolvedValue(response);
    renderPage();
    expect(await screen.findByText(title)).toBeVisible();
    fireEvent.click(screen.getByRole('radio', { name: /待办/ }));
    expect(screen.getByText('还没有取得可显示的工作事项。')).toBeVisible();
    expect(screen.queryByText('当前无待办。')).toBeNull();
    expect(screen.getByTestId('work-count-done')).toHaveTextContent('0');
  });

  it('keeps latest attempt failure when stale running arrives and accepts a newer attempt', async () => {
    const failed = listResponse({ oa_sync: { ...listResponse().oa_sync, status: 'failed',
      attempt_revision: 2, failure_code: 'upstream_unavailable' } });
    apiMocks.listWorkObjects.mockResolvedValue(failed);
    apiMocks.syncWorkObjects.mockRejectedValue(new ApiError(503, 'work_object_sync_failed', 'synthetic'));
    const page = renderPage();
    expect(await screen.findByText('最近一次 OA 同步失败，保留已保存数据')).toBeVisible();
    const key = ['work-objects', 1, 'list', 'active', 'active'];
    apiMocks.listWorkObjects.mockResolvedValue(listResponse({ oa_sync: { ...failed.oa_sync,
      status: 'running', failure_code: null } }));
    await act(async () => { await page.queryClient.refetchQueries({ queryKey: key, exact: true }); });
    expect(screen.getByText('最近一次 OA 同步失败，保留已保存数据')).toBeVisible();
    expect(page.queryClient.getQueryData<WorkObjectListResponse>(key)?.oa_sync.status).toBe('failed');
    apiMocks.listWorkObjects.mockResolvedValue(listResponse({ oa_sync: { ...failed.oa_sync,
      status: 'running', failure_code: null, attempt_revision: 3 } }));
    await act(async () => { await page.queryClient.refetchQueries({ queryKey: key, exact: true }); });
    expect(await screen.findByText('OA 同步进行中，当前显示已保存数据')).toBeVisible();
    expect(page.queryClient.getQueryData<WorkObjectListResponse>(key)?.oa_sync.attempt_revision).toBe(3);
  });

  it('an old mark response cannot restore an unconfirmed handling capability', async () => {
    let resolveSync!: (value: WorkObjectListResponse) => void;
    let resolveMark!: (value: OAWorkObjectView) => void;
    apiMocks.syncWorkObjects.mockReturnValue(new Promise<WorkObjectListResponse>((resolve) => { resolveSync = resolve; }));
    apiMocks.setHandlingMark.mockReturnValue(new Promise<OAWorkObjectView>((resolve) => { resolveMark = resolve; }));
    const page = renderPage();
    await screen.findByText('核对本月采购流程');
    fireEvent.click(screen.getByRole('button', { name: '去 OA 办' }));
    fireEvent.click(await screen.findByRole('button', { name: '标记为已在别处处理' }));
    const stamp = '2026-08-19T03:20:00Z';
    const metadata = { ...listResponse().oa_sync, revision: 2, attempt_revision: 2,
      last_attempt_at: stamp, last_success_at: stamp };
    const history: OAWorkObjectView = { ...WORK_OBJECT, handling_action: 'view_only',
      handling_capability_id: null, oa_observation: { ...WORK_OBJECT.oa_observation,
        pending_state: 'unconfirmed', revision: 2, last_checked_at: stamp } };
    apiMocks.listWorkObjects.mockResolvedValue(listResponse({ items: [], oa_sync: metadata }));
    apiMocks.historyWorkObjects.mockResolvedValue(listResponse({ items: [history], oa_sync: metadata }));
    apiMocks.getWorkObject.mockResolvedValue(history);
    await act(async () => { resolveSync(listResponse({ items: [], oa_sync: metadata })); });
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveTextContent('当前待办未再确认'));
    let releaseReads!: () => void;
    const delayedReads = new Promise<void>((resolve) => { releaseReads = resolve; });
    apiMocks.listWorkObjects.mockImplementation(async () => {
      await delayedReads; return listResponse({ items: [], oa_sync: metadata });
    });
    apiMocks.historyWorkObjects.mockImplementation(async () => {
      await delayedReads; return listResponse({ items: [history], oa_sync: metadata });
    });
    apiMocks.getWorkObject.mockImplementation(async () => { await delayedReads; return history; });
    await act(async () => { resolveMark({ ...WORK_OBJECT, handling_mark: 'handled_elsewhere',
      handling_marked_at: '2026-08-19T03:21:00Z', handling_action: 'self_serve',
      handling_capability_id: 'oa.synthetic.handle' }); });
    const key = ['work-objects', 1, 'detail', WORK_OBJECT.work_object_id];
    try {
      await waitFor(() => expect(page.queryClient.getQueryData<OAWorkObjectView>(key)?.handling_mark).toBe('handled_elsewhere'));
      const detail = page.queryClient.getQueryData<OAWorkObjectView>(key);
      expect(detail?.oa_observation.pending_state).toBe('unconfirmed');
      expect(detail?.handling_action).toBe('view_only');
      expect(detail?.handling_capability_id).toBeNull();
      expect(screen.getByRole('dialog')).toHaveTextContent('当前待办未再确认');
    } finally {
      await act(async () => { releaseReads(); });
    }
    await screen.findByText('处理痕迹已记录；OA 状态未被修改');

  });

});

describe('internal dispatch integration at fetch boundary', () => {
  const originalFetch = globalThis.fetch;
  let internal: InternalWorkObjectView;
  beforeEach(async () => {
    vi.clearAllMocks();
    useAuthStore.getState().markAuthenticated();
    const real = await vi.importActual<typeof import('../../generated/work-objects/work-objects')>('../../generated/work-objects/work-objects');
    apiMocks.listWorkObjects.mockReset().mockImplementation(real.listWorkObjectsApiV1WorkObjectsGet);
    apiMocks.historyWorkObjects.mockReset().mockImplementation(real.listWorkObjectsApiV1WorkObjectsGet);
    apiMocks.completedWorkObjects.mockReset().mockImplementation(real.listWorkObjectsApiV1WorkObjectsGet);
    apiMocks.getWorkObject.mockReset().mockImplementation(real.getWorkObjectApiV1WorkObjectsWorkObjectIdGet);
    apiMocks.syncWorkObjects.mockReset().mockImplementation(real.syncWorkObjectsApiV1WorkObjectsSyncPost);
    internal = { ...INTERNAL_WORK_OBJECT, title: '合成内部事项', assignee_display_name: null, kind: '通知', target_kind: 'user', status: 'assigned', requirement: '办理正文', receipt_requirement: '回执正文', reminder_choices: ['提前 1 天'], reminder_delivery: 'not_enabled', created_at: '2026-09-14T01:00:00Z', updated_at: '2026-09-14T02:00:00Z', owner_department_id: 'd1', version: 1 };
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => Promise.resolve(new Response(JSON.stringify(url.includes('/internal-work-object-1') ? internal : listResponse({ items: url.includes('completion=completed') ? [] : [internal] }))))));
  });
  afterEach(() => { vi.stubGlobal('fetch', originalFetch); });
  it('N1 shows_internal_null_rows_and_view_only_detail', async () => {
    renderPage(); await screen.findByTestId('work-count-todo');
    await waitFor(() => expect(screen.getByTestId('work-count-todo')).toHaveTextContent('1'));
    fireEvent.click(screen.getByRole('radio', { name: /待办/ }));
    const row = screen.getByText('合成内部事项').closest('tr')!;
    expect(row.querySelector('[data-assignee-value]')).toBeNull();
    expect(within(row).getByText('未提供显示名')).toBeVisible();
    fireEvent.click(within(row).getByRole('button', { name: '先看看' }));
    expect(await screen.findByText('内部事项')).toBeVisible();
    const drawer = screen.getByRole('dialog');
    for (const value of ['internal-work-object-1', '办理正文', '回执正文', '已派发']) expect(within(drawer).getByText(value)).toBeVisible();
    expect(within(drawer).getByText(/自动提醒尚未启用/)).toHaveTextContent('提前 1 天');
    expect(drawer.querySelector('[data-assignee-value]')).toBeNull();
    expect(within(drawer).queryByRole('button', { name: /标记为|去 OA|认领|转派/ })).toBeNull();
    expect(vi.mocked(fetch).mock.calls.map(([url, init]) => [url, init?.method])).toEqual([['/api/v1/work-objects?oa_view=active&completion=active', 'GET'], ['/api/v1/work-objects?oa_view=unconfirmed', 'GET'], ['/api/v1/work-objects?oa_view=active&completion=completed', 'GET'], ['/api/v1/work-objects/sync', 'POST'], ['/api/v1/work-objects?oa_view=active&completion=active', 'GET'], ['/api/v1/work-objects?oa_view=unconfirmed', 'GET'], ['/api/v1/work-objects?oa_view=active&completion=completed', 'GET'], ['/api/v1/work-objects/internal-work-object-1', 'GET']]);
    expect(apiMocks.setHandlingMark).not.toHaveBeenCalled();
    expect(useAIDockStore.getState().pageContextDeclaration).toMatchObject({ work_object_refs: [{ work_object_id: 'internal-work-object-1' }], source_refs: [], allowed_capabilities: [], freshness: { state: 'reported', observed_at: '2026-08-19T03:00:00Z' } });
  });
  it('N2 filters_and_sorts_null_without_synthetic_values', async () => {
    const items = [internal, { ...internal, work_object_id: 'null2', title: '空名二' }, { ...internal, work_object_id: 'named1', title: '同名一', assignee_display_name: '张三' }, { ...internal, work_object_id: 'named2', title: '同名二', assignee_display_name: '张三' }];
    vi.mocked(fetch).mockImplementation((url) => Promise.resolve(new Response(JSON.stringify(listResponse({ items: String(url).includes('completion=completed') ? [] : items })))));
    renderPage(); await waitFor(() => expect(screen.getByTestId('work-count-todo')).toHaveTextContent('4'));
    fireEvent.click(screen.getByRole('radio', { name: /待办/ }));
    const rows = () => Array.from(document.querySelectorAll('tbody tr[data-row-key]'), (node) => node.getAttribute('data-row-key'));
    const header = within(screen.getByRole('region', { name: '事项分类' })).getByRole('columnheader', { name: /责任人/ });
    fireEvent.click(header);
    expect(rows()).toEqual(['named1', 'named2', 'internal-work-object-1', 'null2']);
    fireEvent.click(header);
    expect(rows()).toEqual(['internal-work-object-1', 'null2', 'named1', 'named2']);
    fireEvent.click(within(header).getByText('筛选'));
    const checkbox = await screen.findByRole('menuitem', { name: '张三' });
    expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['张三']);
    expect(within(checkbox).getByRole('checkbox')).not.toBeChecked();
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole('button', { name: /OK|确 定|确定/ }));
    expect(rows()).toEqual(['named1', 'named2']);
    fireEvent.click(within(header).getByText('筛选'));
    fireEvent.click(screen.getByRole('button', { name: /Reset|重 置|重置/ }));
    fireEvent.click(screen.getByRole('button', { name: /OK|确 定|确定/ }));
    expect(rows()).toHaveLength(4);
  });
  it('N4 projects_internal_status_without_lifecycle at deadline boundaries', async () => {
    const now = new Date(); const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1); tomorrow.setHours(23, 59, 59, 999);
    const dayAfter = new Date(tomorrow.getTime() + 1);
    const items = [internal, { ...internal, work_object_id: 'past', title: '逾期项', due_at: new Date(now.getTime() - 60000).toISOString() }, { ...internal, work_object_id: 'tomorrow', title: '明日末项', due_at: tomorrow.toISOString() }, { ...internal, work_object_id: 'future', title: '后日零点项', due_at: dayAfter.toISOString(), status: 'department_pending' as const }];
    vi.mocked(fetch).mockImplementation((url) => Promise.resolve(new Response(JSON.stringify(listResponse({ items: String(url).includes('completion=completed') ? [] : items })))));
    renderPage(); expect(await screen.findByText('逾期项')).toBeVisible(); expect(screen.getByText('明日末项')).toBeVisible();
    expect(screen.getByTestId('work-count-urgent')).toHaveTextContent('2'); expect(screen.getByTestId('work-count-todo')).toHaveTextContent('2');
    fireEvent.click(screen.getByRole('radio', { name: /待办/ }));
    expect(screen.getByText('后日零点项')).toBeVisible(); expect(screen.getByText('待部门认领')).toBeVisible();
    expect(screen.getByText('合成内部事项')).toBeVisible();
    expect(screen.getByText(/数据截至/)).toHaveTextContent('2026');
    expect(screen.getByTestId('work-count-done')).toHaveTextContent('0');
    fireEvent.click(screen.getByRole('radio', { name: /已完成/ }));
    expect(screen.queryByText('合成内部事项')).toBeNull(); expect(screen.getByText('近 30 天暂无内部办结事项。')).toBeVisible();
  });
  it('N5 keeps_self_reads_when_directory_is_stale and hides denied detail', async () => {
    vi.mocked(fetch).mockImplementation((url) => {
      const path = String(url);
      return Promise.resolve(path.includes('dispatch-options') ? new Response(JSON.stringify({ detail: { code: 'organization_directory_stale', message: 'synthetic' } }), { status: 503 })
        : path.includes('/internal-work-object-1') ? new Response(JSON.stringify({ detail: { code: 'work_object_not_found', message: 'synthetic' } }), { status: 404 })
        : new Response(JSON.stringify(listResponse({ items: [internal] }))));
    });
    const client = makeClient();
    const dispatch = render(<QueryClientProvider client={client}><MemoryRouter><WorkDispatchPage /></MemoryRouter></QueryClientProvider>);
    expect(await screen.findByRole('alert')).toHaveTextContent('目录已过期'); dispatch.unmount();
    const list = renderPage(client);
    await waitFor(() => expect(screen.getByTestId('work-count-todo')).toHaveTextContent('1'));
    fireEvent.click(screen.getByRole('radio', { name: /待办/ })); fireEvent.click(screen.getByRole('button', { name: '先看看' }));
    expect(await screen.findByText('详情读取失败')).toBeVisible();
    expect(within(screen.getByRole('dialog')).queryByText('办理正文')).toBeNull();
    expect(within(screen.getByRole('dialog')).queryByText('合成内部事项')).toBeNull(); list.unmount();
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: { code: 'organization_directory_unavailable', message: 'synthetic' } }), { status: 503 }));
    const failedClient = makeClient(); failedClient.setQueryData(['work-objects', useAuthStore.getState().generation, 'list', 'active', 'active'], listResponse({ items: [internal] }));
    renderPage(failedClient); expect(await screen.findByText('无法读取已保存的工作事项')).toBeVisible();
    fireEvent.click(screen.getByRole('radio', { name: /待办/ }));
    expect(screen.queryByText('合成内部事项')).toBeNull();
  });
});


describe('internal completion query isolation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({ generation: 100, status: 'authenticated' });
    useAIDockStore.setState({ mode: 'closed' });
    apiMocks.listWorkObjects.mockReset().mockResolvedValue(listResponse());
    apiMocks.historyWorkObjects.mockReset().mockResolvedValue(listResponse({ items: [] }));
    apiMocks.completedWorkObjects.mockReset().mockResolvedValue(listResponse({ items: [COMPLETED_INTERNAL] }));
    apiMocks.syncWorkObjects.mockReset().mockResolvedValue(listResponse({ items: [COMPLETED_INTERNAL] }));
    apiMocks.getWorkObject.mockReset().mockResolvedValue(COMPLETED_INTERNAL);
  });
  const activeKey = ['work-objects', 100, 'list', 'active', 'active'];
  const completedKey = ['work-objects', 100, 'list', 'active', 'completed'];

  it('shows_internal_completed_without_fabricating_oa_history', async () => {
    apiMocks.completedWorkObjects.mockResolvedValue(listResponse({ items: Array.from({ length: 200 }, (_, index) => ({
      ...COMPLETED_INTERNAL, work_object_id: `completed-${index}`, title: index === 0 ? '合成办结事项' : `完成${index}`,
    })), limit_exceeded: true }));
    renderPage();
    await waitFor(() => expect(screen.getByTestId('work-count-done')).toHaveTextContent('200+'));
    expect(apiMocks.completedWorkObjects).toHaveBeenCalledWith({ oa_view: 'active', completion: 'completed' });
    expect(screen.queryByText('合成办结事项')).toBeNull();
    fireEvent.click(screen.getByRole('radio', { name: /已完成/ }));
    expect(await screen.findByText('合成办结事项')).toBeVisible();
    expect(within(screen.getByText('合成办结事项').closest('tr')!).getByText('已办结')).toBeVisible();
    expect(screen.getByText('内部办结事项超过展示上限 200 条')).toBeVisible();
    expect(screen.getByText(/「已完成」仅展示近 30 天内部办结事项，OA 已办结数据尚未接入/)).toBeVisible();
  });

  it.each(['auto', 'manual'])('%s sync never injects its mixed body into completion queries', async (mode) => {
    let resolveSync!: (value: WorkObjectListResponse) => void;
    const pendingSync = new Promise<WorkObjectListResponse>((resolve) => { resolveSync = resolve; });
    if (mode === 'auto') apiMocks.syncWorkObjects.mockReturnValue(pendingSync);
    const page = renderPage();
    await waitFor(() => expect(screen.getByTestId('work-count-done')).toHaveTextContent('1'));
    await waitFor(() => expect(apiMocks.listWorkObjects).toHaveBeenCalledTimes(mode === 'auto' ? 1 : 2));
    if (mode === 'manual') {
      apiMocks.syncWorkObjects.mockReturnValue(pendingSync);
      fireEvent.click(screen.getByRole('button', { name: '刷新 OA 事项' }));
    }
    let release!: () => void;
    const blocked = new Promise<void>((resolve) => { release = resolve; });
    apiMocks.listWorkObjects.mockImplementation(async () => { await blocked; return listResponse(); });
    apiMocks.completedWorkObjects.mockImplementation(async () => { await blocked; return listResponse({ items: [COMPLETED_INTERNAL] }); });
    const snapshots: [string[], string[], boolean | undefined][] = [];
    const capture = () => snapshots.push([
      page.queryClient.getQueryData<WorkObjectListResponse>(activeKey)?.items.map((item) => item.work_object_id) ?? [],
      page.queryClient.getQueryData<WorkObjectListResponse>(completedKey)?.items.map((item) => item.work_object_id) ?? [],
      page.queryClient.getQueryData<WorkObjectListResponse>(completedKey)?.limit_exceeded,
    ]);
    const unsubscribe = page.queryClient.getQueryCache().subscribe(capture);
    await act(async () => resolveSync(listResponse({ items: [COMPLETED_INTERNAL,
      { ...COMPLETED_INTERNAL, work_object_id: 'outside-30-days', completed_at: '2020-01-01T00:00:00.000000Z' }], limit_exceeded: true })));
    await waitFor(() => expect(apiMocks.completedWorkObjects).toHaveBeenCalledTimes(mode === 'auto' ? 2 : 3));
    capture();
    expect(screen.queryByText('合成办结事项')).toBeNull();
    expect(snapshots.length).toBeGreaterThan(0);
    for (const [active, completed, overflow] of snapshots) {
      expect(active).toEqual(['work-object-1']);
      expect(completed).toEqual(['internal-completed']);
      expect(overflow).toBe(false);
    }
    await act(async () => { release(); await blocked; });
    await waitFor(() => expect(page.queryClient.isFetching()).toBe(0));
    capture(); unsubscribe();
    for (const [active, completed] of snapshots) {
      expect(active).toEqual(['work-object-1']); expect(completed).toEqual(['internal-completed']);
    }
    expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(mode === 'auto' ? 1 : 2);
    expect(page.queryClient.getQueryData(['work-objects', 100])).toBeUndefined();
    expect(apiMocks.listWorkObjects).toHaveBeenLastCalledWith({ oa_view: 'active', completion: 'active' });
    expect(apiMocks.completedWorkObjects).toHaveBeenLastCalledWith({ oa_view: 'active', completion: 'completed' });
  });

  it.each(['active', 'completed'])('reports %s refetch failure without using the sync body', async (failed) => {
    let resolve!: (value: WorkObjectListResponse) => void;
    apiMocks.syncWorkObjects.mockReturnValue(new Promise<WorkObjectListResponse>((done) => { resolve = done; }));
    const page = renderPage();
    await waitFor(() => expect(screen.getByTestId('work-count-done')).toHaveTextContent('1'));
    const mock = failed === 'active' ? apiMocks.listWorkObjects : apiMocks.completedWorkObjects;
    mock.mockRejectedValue(new ApiError(503, 'work_object_unavailable', 'synthetic'));
    await act(async () => resolve(listResponse({ items: [{ ...COMPLETED_INTERNAL, work_object_id: 'sync-only' }] })));
    expect(await screen.findByText('OA 同步已完成，事项列表刷新失败')).toBeVisible();
    expect(page.queryClient.getQueryData<WorkObjectListResponse>(activeKey)?.items).toEqual([WORK_OBJECT]);
    expect(page.queryClient.getQueryData<WorkObjectListResponse>(completedKey)?.items).toEqual([COMPLETED_INTERNAL]);
    expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1);
  });

  it('renders_all_internal_status_labels_in_list_rows', async () => {
    const states = ['assigned', 'department_pending', 'in_progress'] as const;
    apiMocks.listWorkObjects.mockResolvedValue(listResponse({ items: states.map((status) => ({
      ...COMPLETED_INTERNAL, work_object_id: status, title: status, status, due_at: null, completed_at: null,
    })) }));
    renderPage();
    await waitFor(() => expect(screen.getByTestId('work-count-todo')).toHaveTextContent('3'));
    fireEvent.click(screen.getByRole('radio', { name: /待办/ }));
    for (const [status, label] of [['assigned', '已派发'], ['department_pending', '待部门认领'], ['in_progress', '办理中']]) {
      expect(within(screen.getByText(status!).closest('tr')!).getByText(label!)).toBeVisible();
    }
    expect(screen.queryByText('合成办结事项')).toBeNull();
    fireEvent.click(screen.getByRole('radio', { name: /已完成/ }));
    expect(within(screen.getByText('合成办结事项').closest('tr')!).getByText('已办结')).toBeVisible();
  });
});
