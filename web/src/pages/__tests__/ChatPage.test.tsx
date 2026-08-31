import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthenticationEffects, ProtectedRoute } from '../../App';
import { RecordsList } from '../../components/RecordsList';
import {
  projectRecords,
  projectResponse,
} from '../../contracts/runtimeProjection';
import { useAIDockStore } from '../../stores/aiDockStore';
import { useAuthStore } from '../../stores/authStore';
import ChatPage from '../ChatPage';

const runtimeMock = vi.hoisted(() => ({
  action: vi.fn(),
  handle: vi.fn(),
}));

vi.mock('../../generated/runtime/runtime', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../generated/runtime/runtime')>();
  return {
    ...actual,
    handleActionApiV1RuntimeActionPost: (
      ...args: Parameters<typeof actual.handleActionApiV1RuntimeActionPost>
    ) => {
      runtimeMock.action(...args);
      return actual.handleActionApiV1RuntimeActionPost(...args);
    },
    handleApiV1RuntimeHandlePost: (
      ...args: Parameters<typeof actual.handleApiV1RuntimeHandlePost>
    ) => {
      runtimeMock.handle(...args);
      return actual.handleApiV1RuntimeHandlePost(...args);
    },
  };
});

const SESSION_A = '11111111-1111-4111-8111-111111111111';

function response(
  body: unknown,
  init: { ok?: boolean; status?: number; statusText?: string } = {},
): Response {
  const status = init.status ?? (init.ok === false ? 500 : 200);
  return new Response(JSON.stringify(body), {
    status,
    statusText: init.statusText ?? 'OK',
    headers: { 'Content-Type': 'application/json' },
  });
}

function envelope(overrides: Record<string, unknown> = {}) {
  const base = {
    schema_version: 'phase0.sdui.v1',
    response_id: 'response-internal',
    task_id: 'task-internal',
    session_id: 'session-server-internal',
    status: 'completed',
    message: '业务请求已完成',
    fallback_text: 'Operation completed.',
    ui: {
      component_type: 'none',
      action: 'none',
      target_system: null,
      reason_code: null,
      payload: {},
    },
    data: null,
    trace_id: 'trace-internal',
    trace_summary: null,
  };
  return { ...base, ...overrides };
}

function confirmPayload(overrides: Record<string, unknown> = {}) {
  return {
    capability_id: 'oa.approval.confirm',
    operation_summary: '提交 OA 审批同意操作',
    target_system: 'oa',
    field_names: ['decision', 'comment'],
    displayed_argument_values: { decision: '同意' },
    ...overrides,
  };
}

function confirmEnvelope(overrides: Record<string, unknown> = {}) {
  return envelope({
    status: 'waiting_user',
    response_id: 'response-confirm-1',
    message: '请复核后提交操作',
    ui: {
      component_type: 'confirm_card',
      action: 'confirm',
      target_system: 'oa',
      payload: confirmPayload(),
    },
    ...overrides,
  });
}

function pendingWorkflow(overrides: Record<string, unknown> = {}) {
  return {
    todo_id: 'todo-001',
    title: '采购申请审批',
    status: 'pending',
    received_at: '2026-08-29T08:00:00Z',
    created_at: '2026-08-29T07:30:00Z',
    workflow_type_id: 'purchase-approval',
    ...overrides,
  };
}

function pendingWorkflowsData(overrides: Record<string, unknown> = {}) {
  return {
    workflows: [pendingWorkflow()],
    returned_count: 1,
    authoritative_count: 1,
    is_complete: true,
    ...overrides,
  };
}

function systemMessage(overrides: Record<string, unknown> = {}) {
  return {
    message_id: 'message-001',
    title: '流程提醒',
    content: '采购流程已到达审批节点。',
    source_name: 'OA 消息中心',
    occurred_at: '2026-08-29T08:30:00Z',
    business_state: 'unread',
    link: null,
    mobile_link: null,
    ...overrides,
  };
}

function systemMessagesData(overrides: Record<string, unknown> = {}) {
  return {
    messages: [systemMessage()],
    returned_count: 1,
    is_complete: true,
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

function renderChat(client = makeClient()) {
  const rendered = render(
    <ConfigProvider>
      <QueryClientProvider client={client}>
        <ChatPage />
      </QueryClientProvider>
    </ConfigProvider>,
  );
  return { client, ...rendered };
}

function sendMessage(message: string) {
  fireEvent.change(screen.getByLabelText('办理请求'), {
    target: { value: message },
  });
  fireEvent.click(screen.getByRole('button', { name: '发送办理请求' }));
}

function storageText(storage: Storage): string {
  const values: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key !== null) {
      values.push(`${key}:${storage.getItem(key) ?? ''}`);
    }
  }
  return values.join('|');
}

beforeEach(() => {
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

describe('ChatPage request boundary', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    runtimeMock.action.mockClear();
    runtimeMock.handle.mockClear();
    localStorage.clear();
    sessionStorage.clear();
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(SESSION_A);
  });

  it('uses the generated client with the fixed web request and one shared CSRF header', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(envelope()));
    vi.stubGlobal('fetch', fetchMock);
    renderChat();

    sendMessage('  查询我的 OA 待办  ');

    await screen.findByText('办理完成');
    expect(runtimeMock.handle).toHaveBeenCalledTimes(1);
    expect(runtimeMock.handle).toHaveBeenCalledWith({
      channel: 'web',
      client_capabilities: {},
      message: '查询我的 OA 待办',
      session_id: SESSION_A,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/runtime/handle',
      expect.objectContaining({ method: 'POST' }),
    );
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const headers = Object.entries(request.headers as Record<string, string>);
    expect(
      headers.filter(([name]) => name.toLowerCase() === 'x-eternalai-csrf'),
    ).toEqual([['X-EternalAI-CSRF', '1']]);
    expect(
      headers.some(([name]) => name.toLowerCase() === 'x-eternalai-roles'),
    ).toBe(false);
  });

  it('reuses one client session across a normal page remount', async () => {
    vi.mocked(globalThis.crypto.randomUUID).mockReturnValue(SESSION_A);
    const fetchMock = vi.fn().mockResolvedValue(response(envelope()));
    vi.stubGlobal('fetch', fetchMock);
    const firstMount = renderChat();

    sendMessage('第一条请求');
    await waitFor(() => expect(runtimeMock.handle).toHaveBeenCalledTimes(1));
    await screen.findByText('办理完成');
    sendMessage('第二条请求');
    await waitFor(() => expect(runtimeMock.handle).toHaveBeenCalledTimes(2));
    firstMount.unmount();

    renderChat();
    sendMessage('新页面请求');
    await waitFor(() => expect(runtimeMock.handle).toHaveBeenCalledTimes(3));

    expect(runtimeMock.handle.mock.calls.map(([request]) => request.session_id)).toEqual([
      SESSION_A,
      SESSION_A,
      SESSION_A,
    ]);
  });

  it('blocks duplicate submission while the POST is pending', async () => {
    let resolveRequest!: (value: Response) => void;
    const fetchMock = vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveRequest = resolve;
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    renderChat();

    sendMessage('只发送一次');
    expect(await screen.findByRole('status')).toHaveTextContent('正在办理');
    expect(screen.getByLabelText('办理请求')).toBeDisabled();
    const pendingButton = screen.getByText('发送办理请求').closest('button');
    expect(pendingButton).not.toBeNull();
    fireEvent.click(pendingButton as HTMLButtonElement);
    const form = screen.getByLabelText('办理请求').closest('form');
    expect(form).not.toBeNull();
    fireEvent.submit(form as HTMLFormElement);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    act(() => resolveRequest(response(envelope())));
    await screen.findByText('办理完成');
    await waitFor(() => expect(screen.getByLabelText('办理请求')).toBeEnabled());
  });
});

describe('ChatPage response projection', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    runtimeMock.action.mockClear();
    runtimeMock.handle.mockClear();
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(SESSION_A);
  });

  it.each([
    ['completed', 'none', '业务已完成', '办理完成'],
    ['blocked', 'none', '无权限，操作被拒绝', '请求被拒绝'],
    ['waiting_user', 'confirm', '请确认提交操作', '需要确认'],
    ['failed', 'none', '操作超时，请重试', '办理失败'],
    ['no_capability_found', 'none', '暂未接入该能力', '暂不可办理'],
  ])('renders %s with a distinct textual status', async (status, action, message, label) => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          envelope({
            status,
            message,
            ui: {
              component_type: action === 'confirm' ? 'confirm_card' : 'none',
              action,
              target_system: action === 'confirm' ? 'oa' : null,
              payload: {},
            },
          }),
        ),
      ),
    );
    renderChat();

    sendMessage('状态测试');

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it('uses fallback text only when the safe message is empty', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(envelope({ message: '   ', fallback_text: '安全降级文本' })),
      ),
    );
    renderChat();

    sendMessage('需要降级文本');

    expect(await screen.findByText('安全降级文本')).toBeInTheDocument();
  });

  it('treats clarification as a new explicit request without inventing options', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response(
          envelope({
            status: 'blocked',
            message: '请先选择明确的账套后继续',
            ui: {
              component_type: 'operator_handback_card',
              action: 'clarify_scope',
              target_system: 'u8',
              payload: { options: ['RAW_OPTION_A', 'RAW_OPTION_B'] },
            },
          }),
        ),
      )
      .mockResolvedValueOnce(response(envelope({ message: '已查询指定账套' })));
    vi.stubGlobal('fetch', fetchMock);
    renderChat();

    sendMessage('查询单据状态');
    expect(await screen.findByText('请先选择明确的账套后继续')).toBeInTheDocument();
    expect(screen.getByText(/完整重述为一条新请求/)).toBeInTheDocument();
    expect(screen.queryByText(/RAW_OPTION/)).not.toBeInTheDocument();

    sendMessage('查询湖南账套的 U8-AP-0033 单据状态');
    expect(await screen.findByText('已查询指定账套')).toBeInTheDocument();
    expect(runtimeMock.handle).toHaveBeenCalledTimes(2);
    expect(runtimeMock.handle.mock.calls[1]?.[0]).toEqual({
      channel: 'web',
      client_capabilities: {},
      message: '查询湖南账套的 U8-AP-0033 单据状态',
      session_id: SESSION_A,
    });
  });

  it.each([
    [
      'a non-string displayed argument value',
      'confirm_card',
      confirmPayload({ displayed_argument_values: { decision: 1 } }),
    ],
    [
      'a non-string field name',
      'confirm_card',
      confirmPayload({ field_names: ['decision', 2] }),
    ],
    [
      'an empty capability id',
      'confirm_card',
      confirmPayload({ capability_id: '   ' }),
    ],
    [
      'a missing capability id',
      'confirm_card',
      confirmPayload({ capability_id: undefined }),
    ],
    [
      'a non-confirm component type',
      'operator_handback_card',
      confirmPayload(),
    ],
  ])('fails closed for confirmation payload with %s', async (_label, componentType, payload) => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          confirmEnvelope({
            ui: {
              component_type: componentType,
              action: 'confirm',
              target_system: 'oa',
              payload,
            },
          }),
        ),
      ),
    );
    renderChat();

    sendMessage('提交请假流程');

    expect(await screen.findByText('请复核后提交操作')).toBeInTheDocument();
    expect(
      screen.queryByRole('region', { name: '操作提交前复核' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '确认提交这项操作' }),
    ).not.toBeInTheDocument();
  });

  it('shows a field name without looking up or inventing its missing value', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          confirmEnvelope({
            data: { private_note: 'RAW_HIDDEN_ARGUMENT_VALUE' },
            ui: {
              component_type: 'confirm_card',
              action: 'confirm',
              target_system: 'oa',
              payload: confirmPayload({
                field_names: ['decision', 'private_note'],
                displayed_argument_values: { decision: '同意' },
              }),
            },
          }),
        ),
      ),
    );
    const { container } = renderChat();

    sendMessage('复核参数展示');

    expect(await screen.findByText('private_note')).toBeInTheDocument();
    expect(screen.getByText('decision')).toBeInTheDocument();
    expect(screen.getByText('同意')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('RAW_HIDDEN_ARGUMENT_VALUE');
    expect(screen.getByText('未提供可展示值')).toBeInTheDocument();
    const argumentsHeading = screen.getByText('操作参数');
    const argumentList = argumentsHeading.nextElementSibling;
    expect(argumentList?.tagName).toBe('DL');
    expect(
      Array.from(argumentList?.children ?? []).every(
        (child) =>
          child.tagName === 'DIV' &&
          child.querySelector(':scope > dt') !== null &&
          child.querySelector(':scope > dd') !== null,
      ),
    ).toBe(true);
    expect(container.querySelector('dl dt:last-child')).toBeNull();
  });

  it.each([
    ['missing', undefined],
    ['non-string', 42],
  ])('keeps confirmation text but hides the button for a %s response id', async (_label, responseId) => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(confirmEnvelope({ response_id: responseId })),
      ),
    );
    renderChat();

    sendMessage('缺少响应引用');

    expect(await screen.findByText('请复核后提交操作')).toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: '操作提交前复核' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: '确认提交这项操作' }),
    ).not.toBeInTheDocument();
  });

  it('rejects the entire pending-workflow list when a record lacks todo_id', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          envelope({
            message: '待办查询完成',
            data: pendingWorkflowsData({
              workflows: [pendingWorkflow({ todo_id: undefined })],
            }),
          }),
        ),
      ),
    );
    renderChat();

    sendMessage('查询待办');

    expect(await screen.findByText('待办查询完成')).toBeInTheDocument();
    expect(screen.queryByText('待办记录')).not.toBeInTheDocument();
    expect(screen.queryByText('采购申请审批')).not.toBeInTheDocument();
  });

  it('rejects the entire system-message list when link is not string or null', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          envelope({
            message: '消息查询完成',
            data: systemMessagesData({
              messages: [systemMessage({ link: 7 })],
            }),
          }),
        ),
      ),
    );
    renderChat();

    sendMessage('查询消息');

    expect(await screen.findByText('消息查询完成')).toBeInTheDocument();
    expect(screen.queryByText('系统消息')).not.toBeInTheDocument();
    expect(screen.queryByText('流程提醒')).not.toBeInTheDocument();
  });

  it('leaves an unknown data shape as text-only output', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(envelope({ message: '通用完成文本', data: { foo: 1 } })),
      ),
    );
    renderChat();

    sendMessage('未知数据');

    expect(await screen.findByText('通用完成文本')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: /待办记录|系统消息/ })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain('foo');
  });

  it('marks pending workflows incomplete from count mismatches', async () => {
    renderChat();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          envelope({
            data: pendingWorkflowsData({ authoritative_count: 2 }),
          }),
        ),
      ),
    );

    sendMessage('数量不一致');
    expect(await screen.findByText('列表可能不完整')).toBeInTheDocument();
    expect(screen.getByText('当前仅展示已取回的 1 条记录。')).toBeInTheDocument();
    expect(screen.getByText('OA 标示共有 2 条。')).toBeInTheDocument();
    expect(screen.getByText('OA 总记录数与实际展示记录数不一致。')).toBeInTheDocument();
    expect(screen.getByText('下一步：到 OA 查看完整列表或稍后重试。')).toBeInTheDocument();
  });

  it('marks pending workflows incomplete when is_complete is false', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          envelope({ data: pendingWorkflowsData({ is_complete: false }) }),
        ),
      ),
    );
    renderChat();

    sendMessage('查询不完整待办');

    expect(await screen.findByText('列表可能不完整')).toBeInTheDocument();
    expect(screen.getByText('采购申请审批')).toBeInTheDocument();
    expect(screen.getByText('OA 表示本次结果尚未完整返回。')).toBeInTheDocument();
    expect(screen.getByText('下一步：到 OA 查看完整列表或稍后重试。')).toBeInTheDocument();
  });

  it('marks system messages incomplete when returned_count is missing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          envelope({ data: systemMessagesData({ returned_count: undefined }) }),
        ),
      ),
    );
    renderChat();

    sendMessage('查询不完整消息');

    expect(await screen.findByText('列表可能不完整')).toBeInTheDocument();
    expect(screen.getByText('流程提醒')).toBeInTheDocument();
    expect(screen.getByText('OA 未提供本次返回记录数。')).toBeInTheDocument();
    expect(screen.getByText('下一步：到 OA 查看完整列表或稍后重试。')).toBeInTheDocument();
  });

  it('marks system messages incomplete when is_complete is false', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          envelope({ data: systemMessagesData({ is_complete: false }) }),
        ),
      ),
    );
    renderChat();

    sendMessage('查询生产方声明不完整的消息');

    expect(await screen.findByText('列表可能不完整')).toBeInTheDocument();
    expect(screen.getByText('流程提醒')).toBeInTheDocument();
    expect(screen.getByText('OA 表示本次结果尚未完整返回。')).toBeInTheDocument();
    expect(screen.getByText('下一步：到 OA 查看完整列表或稍后重试。')).toBeInTheDocument();
  });

  it('does not render an incomplete zero-row response as a normal empty state', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          envelope({
            data: pendingWorkflowsData({
              workflows: [],
              returned_count: 0,
              authoritative_count: 0,
              is_complete: false,
            }),
          }),
        ),
      ),
    );
    renderChat();

    sendMessage('查询零行不完整待办');

    expect(await screen.findByText('当前仅展示已取回的 0 条记录。')).toBeInTheDocument();
    expect(screen.getByText('OA 表示本次结果尚未完整返回。')).toBeInTheDocument();
    expect(screen.getByText('下一步：到 OA 查看完整列表或稍后重试。')).toBeInTheDocument();
  });

  it('renders only an allowlisted OA href with the required new-window attributes', () => {
    const records = projectRecords(
      systemMessagesData({
        messages: [systemMessage({ link: '/oa/messages/001' })],
      }),
      {
        baseUrl: 'http://oa.synthetic.invalid',
        pathPrefixes: ['/oa'],
      },
    );
    if (records === null) throw new Error('records projection failed');

    const { container } = render(<RecordsList records={records} />);
    const link = screen.getByRole('link', { name: '去 OA 查看（新窗口）' });

    expect(link).toHaveAttribute(
      'href',
      'http://oa.synthetic.invalid/oa/messages/001',
    );
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(link.className).toContain('oaLink');
    expect(container.textContent).not.toContain('/oa/messages/001');
  });

  it('keeps the OA navigation click target at least 44 by 44 CSS pixels', () => {
    const stylesheet = readFileSync(
      resolve(process.cwd(), 'src/components/RuntimeViews.module.css'),
      'utf8',
    );
    const oaLinkRule = /\.oaLink\s*\{(?<body>[^}]*)\}/.exec(stylesheet);

    expect(oaLinkRule?.groups?.body).toMatch(/min-width:\s*44px\s*;/);
    expect(oaLinkRule?.groups?.body).toMatch(/min-height:\s*44px\s*;/);
  });

  it('retains a record but removes an untrusted OA link and gives recovery guidance', () => {
    const untrustedLink = 'https://evil.synthetic.invalid/oa/messages/001';
    const records = projectRecords(
      systemMessagesData({
        messages: [systemMessage({ link: untrustedLink })],
      }),
      {
        baseUrl: 'http://oa.synthetic.invalid',
        pathPrefixes: ['/oa'],
      },
    );
    if (records === null) throw new Error('records projection failed');

    const { container } = render(<RecordsList records={records} />);

    expect(screen.getByText('流程提醒')).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText(/OA 提供的链接未通过安全校验/)).toBeInTheDocument();
    expect(screen.getByText(/到 OA 消息中心查找或联系管理员/)).toBeInTheDocument();
    expect(container.textContent).not.toContain(untrustedLink);
  });

  it('retains a record and gives recovery guidance when OA supplies no link', () => {
    const records = projectRecords(
      systemMessagesData({
        messages: [systemMessage({ link: null })],
      }),
      {
        baseUrl: 'http://oa.synthetic.invalid',
        pathPrefixes: ['/oa'],
      },
    );
    if (records === null) throw new Error('records projection failed');

    render(<RecordsList records={records} />);

    expect(screen.getByText('流程提醒')).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText(/OA 未提供可打开的链接/)).toBeInTheDocument();
    expect(screen.getByText(/到 OA 消息中心查找或联系管理员/)).toBeInTheDocument();
  });

  it('renders long message content as folded plain text and never exposes OA links', async () => {
    const content = `<script>alert(1)</script>${' 正文'.repeat(50)}`;
    const link = '/desktop/messages/001';
    const mobileLink = '/mobile/messages/001';
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          envelope({
            data: systemMessagesData({
              messages: [systemMessage({ content, link, mobile_link: mobileLink })],
            }),
          }),
        ),
      ),
    );
    const { container } = renderChat();

    sendMessage('安全展示消息正文');

    expect(await screen.findByText('展开完整正文')).toBeInTheDocument();
    expect(screen.getByText(content)).toBeInTheDocument();
    expect(container.querySelector('details')).not.toHaveAttribute('open');
    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('a')).toBeNull();
    expect(screen.getByText(/当前部署未配置可信 OA 地址/)).toBeInTheDocument();
    expect(screen.getByText(/到 OA 消息中心查找或联系管理员/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(link);
    expect(document.body.textContent).not.toContain(mobileLink);
  });

  it('fails closed for an action outcome outside the nine-value contract', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          envelope({
            message: 'RAW_FUTURE_OUTCOME',
            data: {
              action_outcome: 'future_outcome',
              result: pendingWorkflowsData(),
            },
          }),
        ),
      ),
    );
    renderChat();

    sendMessage('未知动作结果');

    expect(
      await screen.findByText('当前响应无法安全显示，请稍后重试。'),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/RAW_FUTURE_OUTCOME|采购申请审批/);
  });

  const actionOutcomeCases = [
    ['accepted', '操作已受理，已进入本次执行流程。'],
    [
      'action_gate_unavailable',
      '确认通道暂不可用，无法确认本次操作结果。请先核对业务状态，避免重复提交。',
    ],
    ['no_pending_action', '未找到可继续的待确认操作，本次操作未执行。'],
    ['action_binding_incomplete', '操作绑定信息不完整，本次操作未执行。'],
    ['action_reference_mismatch', '确认引用与当前待办不匹配，本次操作未执行。'],
    ['action_pending_changed', '待确认内容已发生变化，本次操作未执行。'],
    ['action_already_claimed', '这项操作已经处理过，未重复执行；本次未执行新的操作。'],
    ['action_stale', '确认已过期，本次操作未执行。'],
    ['action_version_conflict', '操作版本已变化，本次操作未执行。'],
  ] as const;

  it.each(actionOutcomeCases)(
    'renders the distinct closed message for action outcome %s',
    async (actionOutcome, expected) => {
      const accepted = actionOutcome === 'accepted';
      vi.stubGlobal(
        'fetch',
        vi.fn().mockResolvedValue(
          response(
            envelope({
              status: accepted ? 'completed' : 'failed',
              message: accepted
                ? '操作已完成。'
                : '结构化操作未被受理，本次未执行。',
              data: { action_outcome: actionOutcome, result: null },
            }),
          ),
        ),
      );
      renderChat();

      sendMessage('动作结果文案');

      expect(await screen.findByText(expected)).toBeInTheDocument();
      expect(new Set(actionOutcomeCases.map(([, message]) => message)).size).toBe(9);
      if (actionOutcome === 'action_gate_unavailable') {
        expect(expected).toContain('无法确认');
        expect(expected).not.toContain('未执行');
      } else if (!accepted) {
        expect(expected).toContain('未执行');
      }
      if (actionOutcome === 'action_already_claimed') {
        expect(expected).toContain('已经处理过，未重复执行');
      }
    },
  );

  it('submits one structured action, keeps its pending state independent, and appends the result', async () => {
    let resolveAction!: (value: Response) => void;
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(confirmEnvelope()))
      .mockReturnValueOnce(
        new Promise<Response>((resolve) => {
          resolveAction = resolve;
        }),
      );
    vi.stubGlobal('fetch', fetchMock);
    renderChat();

    sendMessage('提交审批同意');
    const confirmButton = await screen.findByRole('button', {
      name: '确认提交这项操作',
    });
    expect(confirmButton.className).toContain('minimumActionTarget');
    expect(screen.getByText('oa.approval.confirm')).toBeInTheDocument();
    expect(screen.getByText('提交 OA 审批同意操作')).toBeInTheDocument();
    expect(screen.getByText('oa')).toBeInTheDocument();
    expect(screen.getByText('decision')).toBeInTheDocument();
    expect(screen.getByText('同意')).toBeInTheDocument();
    expect(screen.getByText('comment')).toBeInTheDocument();

    fireEvent.click(confirmButton);
    fireEvent.click(confirmButton);

    await waitFor(() => expect(runtimeMock.action).toHaveBeenCalledTimes(1));
    expect(runtimeMock.action).toHaveBeenCalledWith({
      channel: 'web',
      session_id: SESSION_A,
      action: {
        action_type: 'confirm',
        response_id: 'response-confirm-1',
        confirmed: true,
      },
    });
    expect(confirmButton).toBeDisabled();
    expect(screen.getByLabelText('办理请求')).toBeEnabled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/v1/runtime/action');
    expect(fetchMock.mock.calls.slice(1).some(([path]) => path === '/api/v1/runtime/handle')).toBe(false);
    expect(runtimeMock.handle).toHaveBeenCalledTimes(1);

    act(() =>
      resolveAction(
        response(
          envelope({
            response_id: 'response-action-result',
            message: '操作已完成。',
            data: {
              action_outcome: 'accepted',
              result: pendingWorkflowsData(),
            },
          }),
        ),
      ),
    );

    expect(
      await screen.findByText('操作已受理，已进入本次执行流程。'),
    ).toBeInTheDocument();
    expect(screen.getByText('采购申请审批')).toBeInTheDocument();
    await waitFor(() => expect(confirmButton).toBeEnabled());
  });

  it('keeps action result business keys nested and projects only data.result', () => {
    const projected = projectResponse(
      envelope({
        data: {
          action_outcome: 'accepted',
          result: systemMessagesData(),
          workflows: [pendingWorkflow({ title: 'RAW_FLATTENED_WORKFLOW' })],
          returned_count: 1,
          authoritative_count: 1,
          is_complete: true,
        },
      }),
    );

    expect(projected.actionOutcome).toBe('accepted');
    expect(projected.records?.kind).toBe('system_messages');
    expect(projected).not.toHaveProperty('result');
    expect(projected).not.toHaveProperty('workflows');
    expect(JSON.stringify(projected)).not.toContain('RAW_FLATTENED_WORKFLOW');
  });

  it('keeps policy denial distinct from failure, missing capability, and binding', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          envelope({
            status: 'blocked',
            message: '无权限，操作被拒绝',
            ui: {
              component_type: 'operator_handback_card',
              action: 'none',
              reason_code: 'RAW_POLICY_REASON',
              payload: {},
            },
          }),
        ),
      ),
    );
    renderChat();

    sendMessage('查询敏感汇总');

    expect(await screen.findByText('无权限，操作被拒绝')).toBeInTheDocument();
    expect(screen.getByText('请求被拒绝')).toBeInTheDocument();
    expect(screen.queryByText(/办理失败|暂不可办理|需要账号绑定/)).not.toBeInTheDocument();
    expect(screen.queryByText('RAW_POLICY_REASON')).not.toBeInTheDocument();
  });

  it('shows only an allowlisted target-system label for binding', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          envelope({
            status: 'blocked',
            message: '需要绑定账号才能继续',
            ui: {
              component_type: 'operator_handback_card',
              action: 'bind_required',
              target_system: 'oa',
              reason_code: 'RAW_BIND_REASON',
              payload: {},
            },
          }),
        ),
      ),
    );
    renderChat();

    sendMessage('查询 OA 待办');

    expect(await screen.findByText('目标系统：OA')).toBeInTheDocument();
    expect(screen.queryByText('RAW_BIND_REASON')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /绑定|解绑/ })).not.toBeInTheDocument();
  });

  it.each([
    ['unknown schema', { schema_version: 'future.v9', message: 'RAW_SCHEMA' }],
    ['missing schema', { schema_version: undefined, message: 'RAW_MISSING_SCHEMA' }],
    ['unknown status', { status: 'queued', message: 'RAW_STATUS' }],
    ['non-string message', { message: { raw: 'RAW_MESSAGE' } }],
    ['missing UI', { ui: null, message: 'RAW_UI' }],
    [
      'unknown action',
      {
        message: 'RAW_ACTION',
        ui: { component_type: 'none', action: 'execute_now', target_system: null },
      },
    ],
    [
      'unknown target',
      {
        status: 'blocked',
        message: 'RAW_TARGET',
        ui: { component_type: 'none', action: 'bind_required', target_system: 'evil' },
      },
    ],
  ])('fails closed for %s', async (_label, overrides) => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(response(envelope(overrides))),
    );
    renderChat();

    sendMessage('不兼容响应测试');

    expect(
      await screen.findByText('当前响应无法安全显示，请稍后重试。'),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/RAW_/);
  });

  it('fails closed for a non-JSON success response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('RAW_NON_JSON_RESPONSE', {
          status: 200,
          headers: { 'Content-Type': 'text/plain' },
        }),
      ),
    );
    renderChat();

    sendMessage('非 JSON 响应测试');

    expect(
      await screen.findByText('当前响应无法安全显示，请稍后重试。'),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('RAW_NON_JSON_RESPONSE');
  });

  it('retains only approved projections and does not expose other raw envelope fields', async () => {
    const sensitiveMarkers = [
      'RAW_DATA_SECRET',
      'RAW_PAYLOAD_SECRET',
      'RAW_TRACE_SUMMARY',
      'RAW_REASON_CODE',
      'RAW_TASK_ID',
      'RAW_SERVER_SESSION',
      'RAW_TRACE_ID',
    ];
    const consoleSpies = [
      vi.spyOn(console, 'debug').mockImplementation(() => undefined),
      vi.spyOn(console, 'error').mockImplementation(() => undefined),
      vi.spyOn(console, 'info').mockImplementation(() => undefined),
      vi.spyOn(console, 'log').mockImplementation(() => undefined),
      vi.spyOn(console, 'warn').mockImplementation(() => undefined),
    ];
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          envelope({
            response_id: 'RAW_RESPONSE_ID',
            task_id: 'RAW_TASK_ID',
            session_id: 'RAW_SERVER_SESSION',
            trace_id: 'RAW_TRACE_ID',
            trace_summary: 'RAW_TRACE_SUMMARY',
            data: { password: 'RAW_DATA_SECRET' },
            ui: {
              component_type: 'none',
              action: 'none',
              reason_code: 'RAW_REASON_CODE',
              payload: { access_token: 'RAW_PAYLOAD_SECRET' },
            },
          }),
        ),
      ),
    );
    const { client } = renderChat();

    sendMessage('安全投影测试');
    await screen.findByText('办理完成');

    const observableState = [
      document.body.innerHTML,
      storageText(localStorage),
      storageText(sessionStorage),
      window.location.href,
      JSON.stringify(useAuthStore.getState()),
      JSON.stringify(
        client.getMutationCache().getAll().map((mutation) => ({
          data: mutation.state.data,
          variables: mutation.state.variables,
        })),
      ),
      ...consoleSpies.flatMap((spy) => spy.mock.calls.flat().map(String)),
    ].join('|');
    sensitiveMarkers.forEach((marker) => expect(observableState).not.toContain(marker));
    expect(document.body.textContent).not.toContain('RAW_RESPONSE_ID');
    expect(observableState).not.toContain(SESSION_A);
  });
});

describe('ChatPage HTTP failures', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    runtimeMock.action.mockClear();
    runtimeMock.handle.mockClear();
    useAuthStore.setState({ generation: 1, status: 'authenticated' });
    vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(SESSION_A);
  });

  it('uses the unified 401 reauthentication chain without a chat error bubble', async () => {
    const client = makeClient();
    client.setQueryData(['private'], { value: 'cached response' });
    const failedResponse = response(
      { detail: 'RAW_401_BODY' },
      { ok: false, status: 401, statusText: 'Unauthorized' },
    );
    const jsonSpy = vi.spyOn(failedResponse, 'json');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(failedResponse));
    render(
      <ConfigProvider>
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={['/chat']}>
            <AuthenticationEffects />
            <Routes>
              <Route path="/login" element={<div>重新认证</div>} />
              <Route element={<ProtectedRoute />}>
                <Route path="/chat" element={<ChatPage />} />
              </Route>
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      </ConfigProvider>,
    );

    sendMessage('触发重新认证');

    expect(await screen.findByText('重新认证')).toBeInTheDocument();
    expect(useAuthStore.getState().status).toBe('unauthenticated');
    await waitFor(() => expect(client.getQueryCache().getAll()).toHaveLength(0));
    expect(jsonSpy).not.toHaveBeenCalled();
    expect(screen.queryByText(/请求失败|网络异常|RAW_401_BODY/)).not.toBeInTheDocument();
  });

  it('maps CSRF 403 safely without reauthentication or retry', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      response(
        {
          detail: {
            code: 'csrf_validation_failed',
            message: 'RAW_CSRF_BACKEND_DETAIL',
          },
        },
        { ok: false, status: 403, statusText: 'Forbidden' },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    renderChat();

    sendMessage('CSRF 测试');

    expect(
      await screen.findByText('当前请求来源未通过安全校验，请联系管理员检查部署配置。'),
    ).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/RAW_CSRF|csrf_validation_failed/);
    expect(useAuthStore.getState().status).toBe('authenticated');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(runtimeMock.handle).toHaveBeenCalledTimes(1);
  });

  it.each([
    [404, 'Not Found', '当前会话不可用，请刷新页面后重试。'],
    [422, 'Unprocessable Entity', '请求格式未通过校验，请重新输入后再试。'],
    [503, 'Service Unavailable', '办理服务暂时不可用，请稍后再试。'],
  ])('maps HTTP %s to fixed safe text', async (status, statusText, expected) => {
    const fetchMock = vi.fn().mockResolvedValue(
      response(
        { detail: { code: `RAW_HTTP_${status}`, message: 'RAW_BACKEND_BODY' } },
        { ok: false, status, statusText },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    renderChat();

    sendMessage(`HTTP ${status}`);

    expect(await screen.findByText(expected)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/RAW_HTTP|RAW_BACKEND_BODY/);
    expect(useAuthStore.getState().status).toBe('authenticated');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('maps a network rejection to fixed safe text without exposing the error', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error('RAW_NETWORK_DETAIL'));
    vi.stubGlobal('fetch', fetchMock);
    renderChat();

    sendMessage('网络失败测试');

    expect(await screen.findByText('网络连接异常，请稍后再试。')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('RAW_NETWORK_DETAIL');
    expect(useAuthStore.getState().status).toBe('authenticated');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
