import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
  historyWorkObjects: vi.fn(),
  listWorkObjects: vi.fn(),
  completedWorkObjects: vi.fn(),
  setHandlingMark: vi.fn(),
  syncWorkObjects: vi.fn(),
}));

vi.mock('../../generated/work-objects/work-objects', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../generated/work-objects/work-objects')>(),
  getWorkObjectApiV1WorkObjectsWorkObjectIdGet: apiMocks.getWorkObject,
  listWorkObjectsApiV1WorkObjectsGet: (
    params: { oa_view?: string; completion?: string } | undefined,
  ) => {
    if (params?.oa_view === 'unconfirmed') return apiMocks.historyWorkObjects(params);
    return params?.completion === 'completed'
      ? apiMocks.completedWorkObjects(params)
      : apiMocks.listWorkObjects(params);
  },
  setWorkObjectHandlingMarkApiV1WorkObjectsWorkObjectIdHandlingMarkPatch:
    apiMocks.setHandlingMark,
  syncWorkObjectsApiV1WorkObjectsSyncPost: apiMocks.syncWorkObjects,
}));

const GENERATION = 77;
const ACTIVE_KEY = ['work-objects', GENERATION, 'list', 'active', 'active'] as const;
const COMPLETED_KEY = ['work-objects', GENERATION, 'list', 'active', 'completed'] as const;

const ACTIVE: OAWorkObjectView = {
  assignee_display_name: '测试办理人',
  due_at: '2026-09-22T08:00:00Z',
  handling_mark: null,
  handling_marked_at: null,
  handling_action: 'go_source_system',
  handling_capability_id: null,
  source_created_at: '2026-09-20 09:00:00',
  source_fetched_at: '2026-09-21T03:00:00Z',
  oa_observation: {
    pending_state: 'current',
    revision: 1,
    last_seen_at: '2026-09-21T03:00:00Z',
    last_checked_at: '2026-09-21T03:00:00Z',
  },
  source_kind: 'pending_workflow',
  source_received_at: '2026-09-20 09:05:00',
  source_ref: 'OA-COMPLETED-MERGE-001',
  source_status: '待办',
  source_system: 'oa',
  source_title: '协调刷新测试事项',
  source_workflow_type_id: 'completed-merge',
  state_authority: 'external_snapshot',
  task_record_id: null,
  work_object_id: 'active-completed-merge-001',
};

const COMPLETED_BASE: InternalWorkObjectView = {
  accepted_at: '2026-09-20T01:00:00.000001Z',
  assignee_display_name: '测试办理人',
  completed_at: '2026-09-20T02:00:00.000001Z',
  created_at: '2026-09-20T00:00:00.000001Z',
  due_at: '2026-09-20T08:00:00Z',
  handling_action: 'view_only',
  handling_capability_id: null,
  handling_mark: null,
  handling_marked_at: null,
  initiator_ai_user_id: 'initiator-completed-merge-001',
  kind: '通知',
  owner_department_id: 'department-completed-merge-001',
  receipt_requirement: null,
  reminder_choices: null,
  reminder_delivery: null,
  requirement: null,
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
  status: 'completed',
  target_kind: 'user',
  task_record_id: null,
  title: '旧版已办结事项',
  updated_at: '2026-09-20T02:00:00.000001Z',
  version: 3,
  work_object_id: 'completed-merge-001',
};

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function listResponse(
  items: WorkObjectListResponse['items'],
  revision = 1,
): WorkObjectListResponse {
  return {
    items,
    limit: 200,
    limit_exceeded: false,
    oa_sync: {
      status: 'succeeded',
      revision,
      attempt_revision: revision,
      last_attempt_at: '2026-09-21T03:00:00Z',
      last_success_at: '2026-09-21T03:00:00Z',
      failure_code: null,
    },
  };
}

function completedVersion(response: WorkObjectListResponse | undefined): number | undefined {
  const completed = response?.items.find(
    (item) => item.work_object_id === COMPLETED_BASE.work_object_id,
  );
  return completed?.state_authority === 'internal' ? completed.version ?? undefined : undefined;
}

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });
}

function renderPage(queryClient: QueryClient) {
  return render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <WorkObjectsPage />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
}

function listQueriesAreIdle(queryClient: QueryClient) {
  const listQueries = queryClient.getQueryCache().findAll({
    queryKey: ['work-objects', GENERATION, 'list'],
  });
  return listQueries.length === 3 && listQueries.every((query) => query.state.fetchStatus === 'idle');
}

function expectMonotonic(versions: number[]) {
  expect(versions).not.toHaveLength(0);
  for (let index = 1; index < versions.length; index += 1) {
    expect(versions[index]!).toBeGreaterThanOrEqual(versions[index - 1]!);
  }
}

describe('completed work-object version merge', () => {
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
    useAuthStore.setState({ generation: GENERATION, status: 'authenticated' });
    apiMocks.getWorkObject.mockResolvedValue(ACTIVE);
    apiMocks.setHandlingMark.mockResolvedValue(ACTIVE);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('keeps completed updates monotonic after coordinated refresh and accepts a newer version', async () => {
    const current = { ...COMPLETED_BASE, title: '当前已办结事项', version: 4 };
    const stale = { ...COMPLETED_BASE, title: '过期已办结事项', version: 3 };
    const newer = { ...COMPLETED_BASE, title: '最新已办结事项', version: 5 };
    let activeResponse = listResponse([ACTIVE]);
    let completedRead = () => Promise.resolve(listResponse([COMPLETED_BASE]));
    const sync = deferred<WorkObjectListResponse>();
    const queryClient = makeClient();
    const successfulCompletedVersions: number[] = [];
    let lastCompletedData: WorkObjectListResponse | undefined;
    const unsubscribe = queryClient.getQueryCache().subscribe(() => {
      const data = queryClient.getQueryData<WorkObjectListResponse>(COMPLETED_KEY);
      if (data === undefined || data === lastCompletedData) return;
      lastCompletedData = data;
      const version = completedVersion(data);
      if (version !== undefined) successfulCompletedVersions.push(version);
    });

    apiMocks.listWorkObjects.mockImplementation(() => Promise.resolve(activeResponse));
    apiMocks.historyWorkObjects.mockImplementation(() => Promise.resolve(listResponse([])));
    apiMocks.completedWorkObjects.mockImplementation(() => completedRead());
    apiMocks.syncWorkObjects.mockImplementation(() => sync.promise);
    const mounted = renderPage(queryClient);

    try {
      await waitFor(() => {
        expect(completedVersion(queryClient.getQueryData(COMPLETED_KEY))).toBe(3);
      });
      await waitFor(() => expect(queryClient.isMutating()).toBe(1));

      completedRead = () => Promise.resolve(listResponse([current]));
      await act(async () => {
        sync.resolve(listResponse([ACTIVE]));
        await Promise.resolve();
      });
      await waitFor(() => {
        expect(queryClient.isMutating()).toBe(0);
        expect(listQueriesAreIdle(queryClient)).toBe(true);
        expect(completedVersion(queryClient.getQueryData(COMPLETED_KEY))).toBe(4);
      });

      const coordinatedRefresh = deferred<WorkObjectListResponse>();
      activeResponse = listResponse([
        {
          ...ACTIVE,
          oa_observation: { ...ACTIVE.oa_observation, revision: 2 },
        },
      ], 2);
      completedRead = () => coordinatedRefresh.promise;
      await act(async () => {
        await queryClient.refetchQueries({ queryKey: ACTIVE_KEY, exact: true });
      });
      await waitFor(() => {
        expect(queryClient.getQueryState(COMPLETED_KEY)?.fetchStatus).toBe('fetching');
      });
      await act(async () => {
        coordinatedRefresh.resolve(listResponse([current]));
        await Promise.resolve();
      });
      await waitFor(() => {
        expect(listQueriesAreIdle(queryClient)).toBe(true);
        expect(completedVersion(queryClient.getQueryData(COMPLETED_KEY))).toBe(4);
      });

      const staleRefresh = deferred<WorkObjectListResponse>();
      completedRead = () => staleRefresh.promise;
      const staleRefetch = queryClient.refetchQueries({ queryKey: COMPLETED_KEY, exact: true });
      await waitFor(() => {
        expect(queryClient.getQueryState(COMPLETED_KEY)?.fetchStatus).toBe('fetching');
      });
      await act(async () => {
        staleRefresh.resolve(listResponse([stale]));
        await staleRefetch;
      });
      expect(completedVersion(queryClient.getQueryData(COMPLETED_KEY))).toBe(4);
      expectMonotonic(successfulCompletedVersions);

      completedRead = () => Promise.resolve(listResponse([newer]));
      await act(async () => {
        await queryClient.refetchQueries({ queryKey: COMPLETED_KEY, exact: true });
      });
      await waitFor(() => {
        expect(completedVersion(queryClient.getQueryData(COMPLETED_KEY))).toBe(5);
      });
      expectMonotonic(successfulCompletedVersions);

      fireEvent.click(screen.getByRole('radio', { name: /已完成/ }));
      expect(await screen.findByText('最新已办结事项')).toBeVisible();
      expect(screen.queryByText('过期已办结事项')).toBeNull();
    } finally {
      unsubscribe();
      mounted.unmount();
      queryClient.clear();
    }
  });
});
