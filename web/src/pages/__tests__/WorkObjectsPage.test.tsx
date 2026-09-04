import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/mutator';
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
  setHandlingMark: vi.fn(),
  syncWorkObjects: vi.fn(),
}));

vi.mock('../../generated/work-objects/work-objects', () => ({
  getWorkObjectApiV1WorkObjectsWorkObjectIdGet: apiMocks.getWorkObject,
  listWorkObjectsApiV1WorkObjectsGet: apiMocks.listWorkObjects,
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
  assignee_display_name: '内部任务责任人',
  due_at: null,
  handling_mark: null,
  handling_marked_at: null,
  handling_action: 'view_only',
  handling_capability_id: null,
  source_created_at: null,
  source_fetched_at: null,
  source_kind: 'manual_dispatch',
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

function listResponse(
  overrides: Partial<WorkObjectListResponse> = {},
): WorkObjectListResponse {
  return {
    items: [WORK_OBJECT],
    limit: 200,
    limit_exceeded: false,
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
    apiMocks.listWorkObjects.mockResolvedValue(listResponse());
    apiMocks.syncWorkObjects.mockResolvedValue(listResponse());
    apiMocks.getWorkObject.mockResolvedValue(WORK_OBJECT);
    apiMocks.setHandlingMark.mockResolvedValue({
      ...WORK_OBJECT,
      handling_mark: 'handled_elsewhere',
      handling_marked_at: '2026-08-19T03:10:00Z',
    });
  });

  it('skips internal work objects until their business display is implemented', async () => {
    const mixedResponse = listResponse({
      items: [WORK_OBJECT, INTERNAL_WORK_OBJECT],
    });
    apiMocks.listWorkObjects.mockResolvedValueOnce(mixedResponse);
    apiMocks.syncWorkObjects.mockResolvedValueOnce(mixedResponse);

    renderPage();

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    expect(screen.getByTestId('work-count-urgent')).toHaveTextContent('1');
    expect(screen.getByTestId('work-count-todo')).toHaveTextContent('0');
    expect(screen.queryByText('内部任务责任人')).not.toBeInTheDocument();
  });

  it('registers the visible Work Objects page through the nine-field contract', async () => {
    const capableItem: OAWorkObjectView = {
      ...WORK_OBJECT,
      handling_action: 'ai_draft',
      handling_capability_id: 'oa.work.read',
    };
    const response = listResponse({ items: [capableItem] });
    apiMocks.listWorkObjects.mockResolvedValueOnce(response);
    apiMocks.syncWorkObjects.mockResolvedValueOnce(response);
    apiMocks.getWorkObject.mockResolvedValueOnce(capableItem);

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
    const response = listResponse({ items: [invalidTimestampItem] });
    apiMocks.listWorkObjects.mockResolvedValueOnce(response);
    apiMocks.syncWorkObjects.mockResolvedValueOnce(response);
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
    apiMocks.listWorkObjects.mockResolvedValueOnce(response);
    apiMocks.syncWorkObjects.mockResolvedValueOnce(response);
    apiMocks.getWorkObject.mockResolvedValueOnce(credentialShapedSourceRef);
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
    apiMocks.listWorkObjects.mockResolvedValueOnce(response);
    apiMocks.syncWorkObjects.mockResolvedValueOnce(response);
    apiMocks.getWorkObject.mockResolvedValueOnce(items[0] as OAWorkObjectView);

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
    apiMocks.listWorkObjects.mockResolvedValueOnce(response);
    apiMocks.syncWorkObjects.mockResolvedValueOnce(response);

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
    apiMocks.listWorkObjects.mockResolvedValueOnce(response);
    apiMocks.syncWorkObjects.mockResolvedValueOnce(response);

    renderPage();

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    expect(screen.getByText('等待确认的事项')).toBeInTheDocument();
    expect(screen.queryByText('下周再办的事项')).not.toBeInTheDocument();
    expect(screen.getByTestId('work-count-urgent')).toHaveTextContent('2');
    expect(screen.getByTestId('work-count-todo')).toHaveTextContent('1');
    expect(screen.getByTestId('work-count-done')).toHaveTextContent('—');

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
    expect(doneCount).toHaveTextContent('—');
    expect(doneCount.textContent).not.toMatch(/[0-9]/);

    fireEvent.click(screen.getByRole('radio', { name: /已完成/ }));

    expect(screen.getByText('办结数据还没有接进来。')).toBeInTheDocument();
    expect(
      screen.getByText('下一步：办结记录接进来后，这里会自动出现。'),
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
    expect(screen.getByText(/当前步骤 待办/)).toBeInTheDocument();
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
    apiMocks.listWorkObjects.mockResolvedValueOnce(response);
    apiMocks.syncWorkObjects.mockResolvedValueOnce(response);

    renderPage();

    expect(await screen.findByText('现在没有要紧的事。')).toBeInTheDocument();
    expect(
      screen.getByText('下一步：到「待办」里还有 1 件。'),
    ).toBeInTheDocument();
    await waitFor(() => expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1));
  });

  it('keeps the saved OA snapshot visible when sync fails and warns about bounded results', async () => {
    apiMocks.listWorkObjects.mockResolvedValueOnce(
      listResponse({ limit_exceeded: true }),
    );
    apiMocks.syncWorkObjects.mockRejectedValueOnce(
      new ApiError(503, 'oa_sync_failed', 'OA 暂时不可用'),
    );

    const { container } = renderPage();

    expect(await screen.findByText('核对本月采购流程')).toBeInTheDocument();
    expect(await screen.findByText('OA 同步失败')).toBeInTheDocument();
    expect(
      screen.getByText('仍在显示上次成功拉取的数据；请以每项的数据截至时间为准。'),
    ).toBeInTheDocument();
    expect(screen.getByText(/oa_sync_failed: OA 暂时不可用/)).toBeInTheDocument();
    expect(screen.getByText(/当前步骤 待办/)).toBeInTheDocument();
    expect(screen.getByText('事项超过首版展示上限 200 条')).toBeInTheDocument();
    expect(screen.getByText('分页只整理已取得的部分，不代表 OA 里的全部事项。')).toBeInTheDocument();
    expect(container.querySelector('.ant-pagination')).toBeInTheDocument();
    expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1);
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
    apiMocks.listWorkObjects
      .mockReset()
      .mockResolvedValueOnce(listResponse())
      .mockResolvedValueOnce(otherUserResponse);
    apiMocks.syncWorkObjects
      .mockReset()
      .mockReturnValueOnce(oldSync)
      .mockResolvedValueOnce(otherUserResponse);
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
          otherUserGeneration,
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
    apiMocks.syncWorkObjects.mockReset().mockReturnValueOnce(pendingSync);
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
      resolveSync(listResponse({ items: [refreshedSource] }));
      await pendingSync;
    });

    await waitFor(() => {
      expect(screen.getByText(/当前步骤 OA_UPDATED/)).toBeInTheDocument();
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
    apiMocks.syncWorkObjects.mockReset().mockReturnValueOnce(pendingSync);
    apiMocks.setHandlingMark.mockReset().mockReturnValueOnce(pendingMark);
    renderPage();
    await screen.findByText('核对本月采购流程');
    await waitFor(() => expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '去 OA 办' }));
    await screen.findByText(/OA 状态数据截至/);
    fireEvent.click(
      screen.getByRole('button', { name: '标记为已在别处处理' }),
    );
    await waitFor(() => expect(apiMocks.setHandlingMark).toHaveBeenCalledTimes(1));

    resolveSync(listResponse({ items: [refreshedSource] }));
    await waitFor(() => {
      expect(screen.getByText(/当前步骤 OA_UPDATED/)).toBeInTheDocument();
      expect(screen.getByText('OA_UPDATED')).toBeInTheDocument();
    });
    resolveMark(markedOldSource);

    expect(await screen.findByText('处理痕迹已记录；OA 状态未被修改')).toBeInTheDocument();
    expect(screen.getByText(/当前步骤 OA_UPDATED/)).toBeInTheDocument();
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
    apiMocks.syncWorkObjects.mockReset().mockReturnValueOnce(pendingSync);
    apiMocks.getWorkObject.mockReset().mockReturnValueOnce(pendingDetail);
    renderPage();
    await screen.findByText('核对本月采购流程');
    await waitFor(() => expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '去 OA 办' }));
    await waitFor(() => expect(apiMocks.getWorkObject).toHaveBeenCalledTimes(1));

    resolveSync(listResponse({ items: [refreshedSource] }));
    expect(await screen.findByText('task-new')).toBeInTheDocument();
    expect(screen.getByText(/当前步骤 OA_UPDATED/)).toBeInTheDocument();
    expect(screen.getByText('OA_UPDATED')).toBeInTheDocument();
    await act(async () => {
      resolveDetail(WORK_OBJECT);
      await pendingDetail;
    });

    expect(screen.getByText('task-new')).toBeInTheDocument();
    expect(screen.getByText(/当前步骤 OA_UPDATED/)).toBeInTheDocument();
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
    apiMocks.syncWorkObjects.mockReset().mockReturnValueOnce(pendingSync);
    apiMocks.getWorkObject.mockReset().mockReturnValueOnce(pendingDetail);
    renderPage();
    await screen.findByText('核对本月采购流程');
    await waitFor(() => expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: '去 OA 办' }));
    await waitFor(() => expect(apiMocks.getWorkObject).toHaveBeenCalledTimes(1));

    resolveSync(
      listResponse({
        items: [OTHER_USER_WORK_OBJECT],
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
    apiMocks.listWorkObjects
      .mockReset()
      .mockResolvedValueOnce(listResponse())
      .mockReturnValueOnce(pendingList);
    const page = renderPage();
    await screen.findByText('核对本月采购流程');
    fireEvent.click(screen.getByRole('button', { name: '去 OA 办' }));
    await screen.findByText(/OA 状态数据截至/);
    void page.queryClient.refetchQueries({
      queryKey: ['work-objects', useAuthStore.getState().generation],
      exact: true,
    });
    await waitFor(() => expect(apiMocks.listWorkObjects).toHaveBeenCalledTimes(2));
    fireEvent.click(
      screen.getByRole('button', { name: '标记为已在别处处理' }),
    );
    expect(await screen.findByText('处理痕迹已记录；OA 状态未被修改')).toBeInTheDocument();
    await act(async () => {
      resolveList(listResponse());
      await pendingList;
    });

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
    expect(screen.getByText('处理痕迹只记录你在 EternalAI 中的声明，不会改写 OA 状态。')).toBeInTheDocument();
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
});
