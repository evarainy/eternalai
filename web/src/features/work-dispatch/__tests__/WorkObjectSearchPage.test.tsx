import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AIDock } from '../../../app/AIDock';
import type {
  OAWorkObjectView,
  WorkObjectListResponse,
} from '../../../generated/work-objects/work-objects.schemas';
import { useAIDockStore } from '../../../stores/aiDockStore';
import { useAuthStore } from '../../../stores/authStore';
import WorkObjectSearchPage from '../WorkObjectSearchPage';

const apiMocks = vi.hoisted(() => ({
  listWorkObjects: vi.fn(),
}));

vi.mock('../../../generated/work-objects/work-objects', () => ({
  listWorkObjectsApiV1WorkObjectsGet: apiMocks.listWorkObjects,
}));

const TITLE_ITEM: OAWorkObjectView = {
  assignee_display_name: '张三',
  due_at: null,
  handling_mark: null,
  handling_marked_at: null,
  handling_action: 'go_source_system',
  handling_capability_id: null,
  source_created_at: '2026-08-18 09:00:00',
  source_fetched_at: '2026-08-31T03:00:00Z',
  source_kind: 'pending_workflow',
  source_received_at: '2026-08-18 09:05:00',
  source_ref: 'OA-TITLE-001',
  source_status: '待办',
  source_system: 'oa',
  source_title: 'Quarterly Budget Review',
  source_workflow_type_id: 'budget-review',
  state_authority: 'external_snapshot',
  task_record_id: null,
  work_object_id: 'work-title',
};

const SOURCE_REF_ITEM: OAWorkObjectView = {
  ...TITLE_ITEM,
  assignee_display_name: '李四',
  source_ref: 'OA-REF-002',
  source_title: '合同归档',
  work_object_id: 'work-source-ref',
};

const ASSIGNEE_ITEM: OAWorkObjectView = {
  ...TITLE_ITEM,
  assignee_display_name: 'Li Ming',
  source_ref: 'OA-OWNER-003',
  source_title: '材料复核',
  work_object_id: 'work-assignee',
};

function listResponse(
  overrides: Partial<WorkObjectListResponse> = {},
): WorkObjectListResponse {
  return {
    items: [TITLE_ITEM, SOURCE_REF_ITEM, ASSIGNEE_ITEM],
    limit: 200,
    limit_exceeded: false,
    ...overrides,
  };
}

function renderPage(
  initialEntry: string,
  { withDock = false }: { withDock?: boolean } = {},
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <WorkObjectSearchPage />
          {withDock ? <AIDock /> : null}
        </MemoryRouter>
      </QueryClientProvider>
    </ConfigProvider>,
  );
}

describe('WorkObjectSearchPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
    apiMocks.listWorkObjects.mockResolvedValue(listResponse());
  });

  it.each([
    ['/search?q=bUdGeT', 'Quarterly Budget Review', '命中标题'],
    ['/search?q=%20oa-ref-002%20', '合同归档', '命中来源编号'],
    ['/search?q=%20li%20ming%20', '材料复核', '命中责任人'],
  ])(
    'matches the approved field contract for %s',
    async (entry, expectedTitle, expectedTag) => {
      renderPage(entry);

      expect(await screen.findByText(expectedTitle)).toBeInTheDocument();
      expect(screen.getByText(expectedTag)).toBeInTheDocument();
      expect(screen.getByText('找到 1 条')).toBeInTheDocument();
    },
  );

  it('shows a distinct loading state without a false zero count or page context', () => {
    apiMocks.listWorkObjects.mockReturnValueOnce(new Promise(() => undefined));

    renderPage('/search?q=budget');

    expect(screen.getByText('正在查找', { selector: 'strong' })).toBeInTheDocument();
    expect(
      screen.getByText('正在查找当前可见的工作事项，请稍候。'),
    ).toBeInTheDocument();
    expect(screen.queryByText('找到 0 条')).not.toBeInTheDocument();
    expect(useAIDockStore.getState().pageContextDeclaration).toBeNull();
  });

  it('shows a distinct failure reason and next step without registering a false empty context', async () => {
    apiMocks.listWorkObjects.mockRejectedValueOnce(new Error('network unavailable'));

    renderPage('/search?q=budget');

    expect(
      await screen.findByText('查找失败', { selector: 'strong' }),
    ).toBeInTheDocument();
    expect(screen.getByText('查找失败：network unavailable。')).toBeInTheDocument();
    expect(
      screen.getAllByText(/下一步：稍后重试，或先回到工作事项页检查数据状态/),
    ).toHaveLength(2);
    expect(screen.queryByText('找到 0 条')).not.toBeInTheDocument();
    expect(useAIDockStore.getState().pageContextDeclaration).toBeNull();
  });

  it.each([
    ['/search?q=oa-ref', 'OA-REF-002'],
    ['/search?q=li', 'Li Ming'],
  ])(
    'does not use substring matching for source reference or assignee: %s',
    async (entry, forbiddenText) => {
      renderPage(entry);

      expect(
        await screen.findByText(/没有匹配项。已在当前已加载的 3 条工作事项中查找/),
      ).toBeInTheDocument();
      expect(screen.queryByText(forbiddenText)).not.toBeInTheDocument();
    },
  );

  it('explains the initial state with the loaded search range and a next step', async () => {
    renderPage('/search');

    expect(
      await screen.findByText(/尚未开始搜索，因为还没有提交关键词.*已加载的 3 条工作事项/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/下一步：在顶部搜索框输入标题片段、完整来源编号或完整责任人/),
    ).toBeInTheDocument();
  });

  it('explains a miss without claiming that unloaded Work Objects do not exist', async () => {
    apiMocks.listWorkObjects.mockResolvedValueOnce(
      listResponse({ limit_exceeded: true }),
    );
    renderPage('/search?q=不存在的事项');

    expect(
      await screen.findByText(
        /已在当前已加载的 3 条工作事项中查找；这不代表未加载的事项中一定不存在/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/下一步：检查标题关键词，或输入完整的来源编号、责任人/),
    ).toBeInTheDocument();
    expect(
      screen.getByText('搜索只覆盖当前已加载的 3 条工作事项'),
    ).toBeInTheDocument();
    expect(screen.getByText('找到 0 条')).toBeInTheDocument();
    await waitFor(() => {
      expect(useAIDockStore.getState().pageContextDeclaration).toMatchObject({
        surface_id: 'work-object-search',
        work_object_refs: [],
      });
    });
  });

  it('registers the hit set through the existing nine-field page-context contract', async () => {
    apiMocks.listWorkObjects.mockResolvedValueOnce(
      listResponse({
        items: [
          {
            ...TITLE_ITEM,
            handling_action: 'ai_draft',
            handling_capability_id: 'oa.work.read',
          },
        ],
      }),
    );
    renderPage('/search?q=budget');

    expect(await screen.findByText('Quarterly Budget Review')).toBeInTheDocument();
    await waitFor(() => {
      expect(useAIDockStore.getState().pageContextDeclaration).toEqual({
        surface_id: 'work-object-search',
        organization_scope: null,
        work_object_refs: [{ work_object_id: 'work-title' }],
        source_refs: [{ source_system: 'oa', source_ref: 'OA-TITLE-001' }],
        filters: [
          {
            field: 'query',
            operator: 'equals',
            value: 'budget',
            source: 'visible_control',
          },
        ],
        selected_metric: null,
        allowed_capabilities: ['oa.work.read'],
        freshness: {
          state: 'reported',
          observed_at: '2026-08-31T03:00:00Z',
        },
        visibility: 'principal',
      });
    });
  });

  it('keeps search results usable when existing page-context validation degrades', async () => {
    const syntheticSensitiveShape = '11010519491231002X';
    apiMocks.listWorkObjects.mockResolvedValueOnce(
      listResponse({
        items: [
          {
            ...TITLE_ITEM,
            source_ref: syntheticSensitiveShape,
            source_title: '身份校验事项',
          },
        ],
      }),
    );
    useAIDockStore.setState({ mode: 'drawer' });
    renderPage(`/search?q=${syntheticSensitiveShape}`, { withDock: true });

    expect(await screen.findByText('身份校验事项')).toBeInTheDocument();
    expect(
      await screen.findByText('当前页面上下文不可用；AI 不会读取本页数据。'),
    ).toBeInTheDocument();
    expect(useAIDockStore.getState().pageContextDeclaration).toBeNull();
  });
});
