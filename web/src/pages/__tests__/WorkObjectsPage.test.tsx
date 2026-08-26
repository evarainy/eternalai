import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/mutator';
import type { CredentialBindingView } from '../../generated/credential-bindings/credential-bindings.schemas';
import type {
  InternalWorkObjectView,
  OAWorkObjectView,
  WorkObjectListResponse,
} from '../../generated/work-objects/work-objects.schemas';
import { useAuthStore } from '../../stores/authStore';
import WorkObjectsPage from '../WorkObjectsPage';

const apiMocks = vi.hoisted(() => ({
  bindPassword: vi.fn(),
  getWorkObject: vi.fn(),
  getBinding: vi.fn(),
  listWorkObjects: vi.fn(),
  setHandlingMark: vi.fn(),
  syncWorkObjects: vi.fn(),
  unbindPassword: vi.fn(),
}));

vi.mock('../../generated/credential-bindings/credential-bindings', () => ({
  bindPasswordApiV1CredentialBindingsTargetSystemPut: apiMocks.bindPassword,
  getBindingApiV1CredentialBindingsTargetSystemGet: apiMocks.getBinding,
  unbindPasswordApiV1CredentialBindingsTargetSystemDelete:
    apiMocks.unbindPassword,
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

const UNBOUND_CREDENTIAL: CredentialBindingView = {
  bound: false,
  poll_failure_count: 0,
  poll_status: 'unbound',
  target_system: 'oa',
  updated_at: null,
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

function renderPage(queryClient = makeClient()) {
  const rendered = render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <WorkObjectsPage />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
  return { queryClient, ...rendered };
}

describe('WorkObjectsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    apiMocks.listWorkObjects.mockResolvedValue(listResponse());
    apiMocks.syncWorkObjects.mockResolvedValue(listResponse());
    apiMocks.getWorkObject.mockResolvedValue(WORK_OBJECT);
    apiMocks.getBinding.mockResolvedValue(UNBOUND_CREDENTIAL);
    apiMocks.bindPassword.mockResolvedValue({
      ...UNBOUND_CREDENTIAL,
      bound: true,
      poll_status: 'active',
      updated_at: '2026-08-21T03:00:00Z',
    });
    apiMocks.unbindPassword.mockResolvedValue(UNBOUND_CREDENTIAL);
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
    expect(screen.getByText('当前显示 1 项')).toBeInTheDocument();
    expect(screen.queryByText('内部任务责任人')).not.toBeInTheDocument();
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
    expect(screen.getByText('待办')).toBeInTheDocument();
    expect(screen.getByText('事项超过首版展示上限 200 条')).toBeInTheDocument();
    expect(screen.getByText(/本页面没有服务端分页/)).toBeInTheDocument();
    expect(container.querySelector('.ant-pagination')).not.toBeInTheDocument();
    expect(apiMocks.syncWorkObjects).toHaveBeenCalledTimes(1);
  });

  it('clears plaintext fields immediately while an OA binding request is pending', async () => {
    let resolveBinding!: (binding: CredentialBindingView) => void;
    const pendingBinding = new Promise<CredentialBindingView>((resolve) => {
      resolveBinding = resolve;
    });
    apiMocks.bindPassword.mockReset().mockReturnValueOnce(pendingBinding);
    renderPage();

    expect(await screen.findByText('尚未绑定')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '绑定 OA 密码' }));
    const loginId = screen.getByLabelText('OA 登录标识');
    const password = screen.getByLabelText('OA 密码');
    fireEvent.change(loginId, { target: { value: 'LOGIN-ID-CANARY' } });
    fireEvent.change(password, { target: { value: 'PASSWORD-CANARY' } });
    fireEvent.click(screen.getByRole('button', { name: '验证并保存' }));

    await waitFor(() => {
      expect(apiMocks.bindPassword).toHaveBeenCalledWith('oa', {
        login_id: 'LOGIN-ID-CANARY',
        password: 'PASSWORD-CANARY',
      });
    });
    await waitFor(() => {
      expect(screen.getByLabelText('OA 登录标识')).toHaveValue('');
      expect(screen.getByLabelText('OA 密码')).toHaveValue('');
    });
    expect(screen.queryByText('PASSWORD-CANARY')).not.toBeInTheDocument();

    await act(async () => {
      resolveBinding({
        ...UNBOUND_CREDENTIAL,
        bound: true,
        poll_status: 'active',
        updated_at: '2026-08-21T03:00:00Z',
      });
      await pendingBinding;
    });
    expect(await screen.findByText('后台轮询已启用')).toBeInTheDocument();
  });

  it.each([
    ['invalid', '密码已失效，需重新绑定'],
    ['captcha_required', 'OA 要求验证码，轮询已停止'],
  ] as const)('shows the terminal %s state as a rebind warning', async (status, label) => {
    apiMocks.getBinding.mockResolvedValueOnce({
      ...UNBOUND_CREDENTIAL,
      bound: true,
      poll_status: status,
      updated_at: '2026-08-21T03:00:00Z',
    });

    renderPage();

    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新绑定' })).toBeInTheDocument();
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
    fireEvent.click(screen.getByRole('button', { name: '查看详情' }));
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
      expect(screen.getAllByText('OA_UPDATED').length).toBeGreaterThanOrEqual(2);
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
    fireEvent.click(screen.getByRole('button', { name: '查看详情' }));
    await screen.findByText(/OA 状态数据截至/);
    fireEvent.click(
      screen.getByRole('button', { name: '标记为已在别处处理' }),
    );
    await waitFor(() => expect(apiMocks.setHandlingMark).toHaveBeenCalledTimes(1));

    resolveSync(listResponse({ items: [refreshedSource] }));
    await waitFor(() =>
      expect(screen.getAllByText('OA_UPDATED').length).toBeGreaterThanOrEqual(2),
    );
    resolveMark(markedOldSource);

    expect(await screen.findByText('处理痕迹已记录；OA 状态未被修改')).toBeInTheDocument();
    expect(screen.getAllByText('OA_UPDATED').length).toBeGreaterThanOrEqual(2);
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
    fireEvent.click(screen.getByRole('button', { name: '查看详情' }));
    await waitFor(() => expect(apiMocks.getWorkObject).toHaveBeenCalledTimes(1));

    resolveSync(listResponse({ items: [refreshedSource] }));
    expect(await screen.findByText('task-new')).toBeInTheDocument();
    expect(screen.getAllByText('OA_UPDATED').length).toBeGreaterThanOrEqual(2);
    await act(async () => {
      resolveDetail(WORK_OBJECT);
      await pendingDetail;
    });

    expect(screen.getByText('task-new')).toBeInTheDocument();
    expect(screen.getAllByText('OA_UPDATED').length).toBeGreaterThanOrEqual(2);
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
    fireEvent.click(screen.getByRole('button', { name: '查看详情' }));
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
    fireEvent.click(screen.getByRole('button', { name: '查看详情' }));
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

    fireEvent.click(screen.getByRole('button', { name: '查看详情' }));

    expect(await screen.findByText(/OA 状态数据截至/)).toBeInTheDocument();
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
