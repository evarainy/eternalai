import { act, fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { StrictMode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import InternalWorkLifecyclePanel from '../InternalWorkLifecyclePanel';
import { useAuthStore } from '../../../stores/authStore';
import { AuthenticationEffects } from '../../../App';
import { customInstance } from '../../../api/mutator';

const etag = `"wolc-${'a'.repeat(64)}"`;
const remarkEtag = `"worm-${'b'.repeat(64)}"`;
const relation = '12345678-1234-1234-1234-123456789abc';
const moment = '2026-09-20T01:00:00.000001Z';
type Operation = 'accept' | 'feedback' | 'submit';
class JsonResponse extends Response {
  constructor(body: BodyInit | null, init: ResponseInit = {}) { super(body, { ...init, headers: { 'Content-Type': 'application/json', ...Object.fromEntries(new Headers(init.headers)) } }); }
}
const contentLabel = '办理进展 / 正式结果';
function view(state: string, version: number) {
  return { work_object_id: 'internal-one', status: state, version, title: '合成事项', requirement: '', receipt_requirement: '', due_at: null,
    accepted_at: state === 'assigned' ? null : moment, completed_at: state === 'completed' ? moment : null,
    dispatch_mode: 'independent', completion_mode: 'submit_completes', frozen: state === 'completed',
    relations: [{ relation_id: relation, target: { kind: 'user', directory_user_id: 'u1', department_id: 'd1' }, role: 'single', status: state === 'completed' ? 'submitted' : state,
      assignee_directory_user_id: 'u1', accepted_at: null, submitted_at: null, submitted_result: null, reviewed_at: null, review_result: null, review_reason: null,
      available_commands: state === 'assigned' ? ['accept'] : state === 'in_progress' ? ['feedback', 'submit'] : [] }], available_commands: [], unavailable_reason: null };
}
function readFixture(url: string, state: string, version: number, events: object[] = []) {
  if (url.includes('/remarks/history')) return new JsonResponse(JSON.stringify({ items: [], next_cursor: null }));
  if (url.endsWith('/remarks')) return new JsonResponse(JSON.stringify({ work_object_id: 'internal-one', text: '', version: 0, updated_by: null, updated_at: null, editable: true }), { headers: { ETag: remarkEtag } });
  if (url.endsWith('/attachments')) return new JsonResponse('[]');
  if (url.includes('/events')) return new JsonResponse(JSON.stringify({ items: events, next_after_version: null }));
  return new JsonResponse(JSON.stringify(view(state, version)), { headers: { ETag: etag } });
}
function AuthenticatedLifecycle() {
  const status = useAuthStore((value) => value.status);
  return <><AuthenticationEffects />{status === 'authenticated' ? <InternalWorkLifecyclePanel workObjectId="internal-one" /> : <p>合成已退出</p>}</>;
}
function harness(initial = 'assigned', strict = false, authentication = false) {
  let state = initial; let version = initial === 'assigned' ? 1 : 2;
  const events: object[] = []; const writes: RequestInit[] = [];
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    await Promise.resolve();
    if (options?.signal?.aborted) throw new DOMException('Synthetic aborted read', 'AbortError');
    if (url.endsWith('/commands')) {
      writes.push(options!);
      const body = JSON.parse(options!.body as string) as { operation: Operation; text?: string };
      const event = { event_id: crypto.randomUUID(), work_object_id: 'internal-one', actor_ai_user_id: 'synthetic-ai',
        operation: body.operation, from_status: state, to_status: body.operation === 'submit' ? 'completed' : 'in_progress', result_version: ++version,
        occurred_at: moment, text: body.text ?? null, handling_relation_id: relation, relation_from_status: state, relation_to_status: 'in_progress', payload: null };
      state = event.to_status; events.push(event); return new JsonResponse(JSON.stringify({ event, replayed: false }));
    }
    return readFixture(url, state, version, events);
  });
  vi.stubGlobal('fetch', fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const element = <QueryClientProvider client={client}>{authentication ? <AuthenticatedLifecycle /> : <InternalWorkLifecyclePanel workObjectId="internal-one" />}</QueryClientProvider>;
  render(strict ? <StrictMode>{element}</StrictMode> : element); return { fetcher, writes };
}
async function openFeedback() { fireEvent.click(await screen.findByRole('button', { name: '反馈进展' })); return screen.getByRole('textbox', { name: contentLabel }); }
async function sendFeedback(text: string) { fireEvent.change(await openFeedback(), { target: { value: text } }); fireEvent.click(screen.getByRole('button', { name: '确认反馈进展' })); }
beforeEach(() => { useAuthStore.getState().markAuthenticated(); vi.stubGlobal('ResizeObserver', class { observe = vi.fn(); unobserve = vi.fn(); disconnect = vi.fn(); }); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('does not show the discarded StrictMode read as a network error', async () => {
  harness('assigned', true); await screen.findByRole('button', { name: /^接\s*单$/ }); expect(screen.queryByText('网络请求失败。')).toBeNull();
});
it('shows_commands_and_posts_exact_feedback_and_completion', async () => {
  const { writes } = harness();
  fireEvent.click(await screen.findByRole('button', { name: /^接\s*单$/ })); fireEvent.click(screen.getByRole('button', { name: '确认接单' }));
  await sendFeedback('合成进展'); await screen.findByText('合成进展', { selector: 'p' });
  fireEvent.click(screen.getByRole('button', { name: '提交结果' })); fireEvent.change(screen.getByRole('textbox', { name: contentLabel }), { target: { value: '合成办结说明' } });
  expect(writes).toHaveLength(2); expect(screen.getByText(/独立办理.*正式提交即完成/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '确认提交结果' })); await screen.findByRole('heading', { name: '已办结' });
  expect(writes.map((request) => JSON.parse(request.body as string))).toEqual([
    { operation: 'accept', handling_relation_id: relation }, { operation: 'feedback', handling_relation_id: relation, text: '合成进展' },
    { operation: 'submit', handling_relation_id: relation, text: '合成办结说明', attachment_ids: [] },
  ]);
  expect(screen.queryByRole('textbox', { name: contentLabel })).toBeNull(); expect(screen.queryByRole('button', { name: '提交结果' })).toBeNull();
  expect(screen.getByText(`办结时间：${moment}`)).toBeTruthy();
  for (const request of writes) expect(request.headers).toMatchObject({ 'If-Match': etag, 'X-EternalAI-CSRF': '1' });
});
it('keeps_uncertain_request_and_never_retries_with_new_key', async () => {
  const { fetcher } = harness('in_progress'); fireEvent.change(await openFeedback(), { target: { value: '合成说明' } });
  fetcher.mockRejectedValueOnce(new TypeError('Synthetic network failure')); fireEvent.click(screen.getByRole('button', { name: '确认反馈进展' }));
  await screen.findByText('结果待确认，请重试原请求。'); const first = fetcher.mock.calls.find(([url]) => url.endsWith('/commands'))!;
  fireEvent.click(screen.getByRole('button', { name: '重试原办理请求' })); await screen.findByText('合成说明', { selector: 'p' });
  const calls = fetcher.mock.calls.filter(([url]) => url.endsWith('/commands')); expect(calls).toHaveLength(2); expect(calls[1]).toEqual(first);
});
it('clears_private_text_and_discards_late_results_on_generation_change', async () => {
  const { fetcher } = harness('in_progress'); fireEvent.change(await openFeedback(), { target: { value: '上一身份的正文' } });
  let reject!: (reason: unknown) => void; fetcher.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
  fireEvent.click(screen.getByRole('button', { name: '确认反馈进展' })); act(() => useAuthStore.getState().markAuthenticated());
  await waitFor(() => expect(screen.queryByRole('textbox', { name: contentLabel })).toBeNull());
  await act(async () => { reject(new Error('Late synthetic failure')); });
  expect(screen.queryByText('结果待确认，请重试原请求。')).toBeNull(); expect(screen.queryByDisplayValue('上一身份的正文')).toBeNull();
});
it('logout_discards_pending_key_body_etag_and_events', async () => {
  useAuthStore.setState({ generation: 80, status: 'authenticated' }); const { fetcher } = harness('in_progress', false, true);
  fireEvent.change(await openFeedback(), { target: { value: 'Synthetic old private body' } });
  let reject!: (reason: unknown) => void; fetcher.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
  fireEvent.click(screen.getByRole('button', { name: '确认反馈进展' })); const oldRequest = fetcher.mock.calls.find(([url]) => url.endsWith('/commands'))!;
  expect(JSON.parse(oldRequest[1]!.body as string)).toEqual({ operation: 'feedback', handling_relation_id: relation, text: 'Synthetic old private body' });
  fetcher.mockResolvedValueOnce(new JsonResponse('', { status: 401 }));
  await act(async () => { await expect(customInstance({ url: '/api/v1/me', method: 'GET' })).rejects.toMatchObject({ status: 401 }); });
  expect(screen.getByText('合成已退出')).toBeVisible(); expect(screen.queryByDisplayValue('Synthetic old private body')).toBeNull();
  act(() => useAuthStore.getState().markAuthenticated()); await screen.findByRole('button', { name: '反馈进展' });
  await act(async () => { reject(new Error('Synthetic old failure')); });
  expect(screen.queryByText('结果待确认，请重试原请求。')).toBeNull(); expect(screen.queryByRole('button', { name: '重试原办理请求' })).toBeNull();
  expect(screen.queryByDisplayValue('Synthetic old private body')).toBeNull(); expect(document.querySelectorAll('article.event')).toHaveLength(0);
  await sendFeedback('Synthetic new body'); await screen.findByText('Synthetic new body', { selector: 'p' });
  const commands = fetcher.mock.calls.filter(([url]) => url.endsWith('/commands')); expect(commands).toHaveLength(2);
  expect(commands[1]![1]!.body).not.toEqual(oldRequest[1]!.body);
  expect((commands[1]![1]!.headers as Record<string, string>)['Idempotency-Key']).not.toEqual((oldRequest[1]!.headers as Record<string, string>)['Idempotency-Key']);
});
it('loads_later_events_and_renders_feedback_as_text', async () => {
  const { fetcher } = harness('completed'); await screen.findByRole('heading', { name: '已办结' });
  const event = (version: number) => ({ event_id: `12345678-1234-1234-1234-${String(version).padStart(12, '0')}`, actor_ai_user_id: 'synthetic-ai',
    work_object_id: 'internal-one', operation: version === 52 ? 'submit' : 'feedback', from_status: 'in_progress', to_status: version === 52 ? 'completed' : 'in_progress',
    result_version: version, occurred_at: moment, text: version === 52 ? '<b>最终证据</b>' : `进展${version}`, payload: null });
  fetcher.mockImplementation(async (url: string) => url.includes('/events') ? new JsonResponse(JSON.stringify(url.includes('after_version=51')
    ? { items: [event(51), event(52)], next_after_version: null } : { items: Array.from({ length: 50 }, (_, i) => event(i + 2)), next_after_version: 51 })) : readFixture(url, 'completed', 52));
  fireEvent.click(screen.getByRole('button', { name: '重新读取办理' })); fireEvent.click(await screen.findByRole('button', { name: '加载更多办理记录' }));
  expect(await screen.findByText('<b>最终证据</b>')).toBeVisible(); expect(screen.getAllByText('进展51')).toHaveLength(1);
  expect(document.querySelector('article b')).toBeNull(); expect(document.querySelectorAll('article').length).toBe(52);
  expect(fetcher.mock.calls.some(([url]) => url.includes('after_version=51&limit=50'))).toBe(true); expect(screen.queryByRole('button', { name: '加载更多办理记录' })).toBeNull();
});
it.each(['service', 'invalid-success'])('preserves the exact unresolved request after %s', async (mode) => {
  const { fetcher } = harness('in_progress'); fireEvent.change(await openFeedback(), { target: { value: '不可重复的正文' } });
  fetcher.mockResolvedValueOnce(mode === 'service' ? new JsonResponse(JSON.stringify({ detail: { code: 'work_object_lifecycle_failed', message: 'synthetic' } }), { status: 503 }) : new JsonResponse(JSON.stringify({ event: {}, replayed: false })));
  fireEvent.click(screen.getByRole('button', { name: '确认反馈进展' })); await screen.findByText('结果待确认，请重试原请求。');
  const first = fetcher.mock.calls.find(([url]) => url.endsWith('/commands'))!; fireEvent.click(screen.getByRole('button', { name: '重新读取办理' }));
  await screen.findByRole('heading', { name: '办理中' }); expect(screen.getByRole('textbox', { name: contentLabel })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '重试原办理请求' })); await screen.findByText('不可重复的正文', { selector: 'p' });
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/commands'))).toEqual([first, first]);
});
it('refreshes after 412 without automatically posting again', async () => {
  const { fetcher } = harness('in_progress'); fireEvent.change(await openFeedback(), { target: { value: '需再次确认' } });
  fetcher.mockResolvedValueOnce(new JsonResponse(JSON.stringify({ detail: { code: 'work_object_version_conflict', message: 'synthetic' } }), { status: 412 }));
  fireEvent.click(screen.getByRole('button', { name: '确认反馈进展' })); await screen.findByText('事项已更新，请核对最新状态后再操作。');
  await screen.findByRole('button', { name: '反馈进展' }); expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/commands'))).toHaveLength(1);
  expect(screen.queryByRole('button', { name: '重试原办理请求' })).toBeNull();
});
it('does not deny a committed result when its readback fails', async () => {
  const { fetcher } = harness('in_progress'); fireEvent.change(await openFeedback(), { target: { value: '已提交的正文' } }); const original = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (url, options) => { if (url.endsWith('/commands')) return original(url, options); throw new TypeError('Synthetic readback failure'); });
  fireEvent.click(screen.getByRole('button', { name: '确认反馈进展' })); await screen.findByText('操作已提交，最新状态暂未读取，请重新读取核对。');
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/commands'))).toHaveLength(1); expect(screen.queryByRole('button', { name: '重试原办理请求' })).toBeNull();
});
it('drops a late success and never persists private command data', async () => {
  const get = vi.spyOn(Storage.prototype, 'getItem'); const set = vi.spyOn(Storage.prototype, 'setItem'); const { fetcher } = harness('in_progress');
  fireEvent.change(await openFeedback(), { target: { value: '旧身份秘密正文' } }); let resolve!: (value: Response) => void;
  fetcher.mockImplementationOnce(() => new Promise<Response>((done) => { resolve = done; })); fireEvent.click(screen.getByRole('button', { name: '确认反馈进展' }));
  act(() => useAuthStore.getState().markAuthenticated()); await screen.findByRole('button', { name: '反馈进展' });
  await act(async () => resolve(new JsonResponse(JSON.stringify({ replayed: false, event: { event_id: crypto.randomUUID(), work_object_id: 'internal-one', operation: 'feedback',
    from_status: 'in_progress', to_status: 'in_progress', result_version: 3, occurred_at: moment, text: '旧身份秘密正文', actor_ai_user_id: 'synthetic-ai' } }))));
  expect(screen.queryByText('操作已提交。')).toBeNull(); expect(screen.queryByDisplayValue('旧身份秘密正文')).toBeNull();
  expect(get).not.toHaveBeenCalled(); expect(set).not.toHaveBeenCalled(); get.mockRestore(); set.mockRestore();
});
