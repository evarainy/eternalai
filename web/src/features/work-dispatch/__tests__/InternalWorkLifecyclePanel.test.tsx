import { act, fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { StrictMode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import InternalWorkLifecyclePanel from '../InternalWorkLifecyclePanel';
import { useAuthStore } from '../../../stores/authStore';

const etag = `"wolc-${'a'.repeat(64)}"`;
const moment = '2026-09-20T01:00:00.000001Z';
type Operation = 'accept' | 'feedback' | 'complete';
function harness(initial = 'assigned', strict = false) {
  let state = initial;
  let version = initial === 'assigned' ? 1 : 2;
  const events: object[] = [];
  const writes: RequestInit[] = [];
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    await Promise.resolve();
    if (options?.signal?.aborted) throw new DOMException('Synthetic aborted read', 'AbortError');
    if (url.endsWith('/commands')) {
      writes.push(options!);
      const body = JSON.parse(options!.body as string) as { operation: Operation; text?: string };
      const event = { event_id: crypto.randomUUID(), work_object_id: 'internal-one',
        operation: body.operation, from_status: state,
        to_status: body.operation === 'complete' ? 'completed' : 'in_progress', result_version: ++version,
        occurred_at: moment, text: body.text ?? null, actor_role: 'assignee' };
      state = event.to_status; events.push(event);
      return new Response(JSON.stringify({ event, replayed: false }));
    }
    if (url.includes('/events')) return new Response(JSON.stringify({ items: events, has_more: false, next_after_version: null }));
    return new Response(JSON.stringify({ work_object_id: 'internal-one', status: state, version,
      accepted_at: state === 'assigned' ? null : moment, completed_at: state === 'completed' ? moment : null,
      available_commands: state === 'assigned' ? ['accept'] : state === 'in_progress' ? ['feedback', 'complete'] : [],
      unavailable_reason: null }), { headers: { ETag: etag } });
  });
  vi.stubGlobal('fetch', fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const element = <QueryClientProvider client={client}><InternalWorkLifecyclePanel workObjectId="internal-one" /></QueryClientProvider>;
  render(strict ? <StrictMode>{element}</StrictMode> : element);
  return { fetcher, writes };
}
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe = vi.fn(); unobserve = vi.fn(); disconnect = vi.fn(); });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('does not show the discarded StrictMode read as a network error', async () => {
  harness('assigned', true);
  await screen.findByRole('button', { name: /^接\s*单$/ });
  expect(screen.queryByText('网络请求失败。')).toBeNull();
});

it('shows_commands_and_posts_exact_feedback_and_completion', async () => {
  const { writes } = harness();
  fireEvent.click(await screen.findByRole('button', { name: /^接\s*单$/ }));
  const input = await screen.findByRole('textbox', { name: '办理说明' });
  fireEvent.change(input, { target: { value: '合成进展' } });
  fireEvent.click(screen.getByRole('button', { name: '反馈进展' }));
  await screen.findByText('合成进展', { selector: 'p' });
  fireEvent.change(await screen.findByRole('textbox', { name: '办理说明' }), { target: { value: '合成办结说明' } });
  fireEvent.click(screen.getByRole('button', { name: '办结事项' }));
  expect(writes).toHaveLength(2);
  expect(screen.getByText('办结后不能继续修改')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '确认办结' }));
  await screen.findByRole('heading', { name: '已办结' });
  expect(writes.map((request) => JSON.parse(request.body as string))).toEqual([
    { operation: 'accept' }, { operation: 'feedback', text: '合成进展' }, { operation: 'complete', text: '合成办结说明' },
  ]);
  expect(screen.queryByRole('textbox')).toBeNull();
  expect(screen.queryByRole('button', { name: '办结事项' })).toBeNull();
  expect(screen.getByText(`办结时间：${moment}`)).toBeTruthy();
  for (const request of writes) expect(request.headers).toMatchObject({ 'If-Match': etag, 'X-EternalAI-CSRF': '1' });
});

it('keeps_uncertain_request_and_never_retries_with_new_key', async () => {
  const { fetcher } = harness('in_progress');
  fireEvent.change(await screen.findByRole('textbox'), { target: { value: '合成说明' } });
  fetcher.mockRejectedValueOnce(new TypeError('Synthetic network failure'));
  fireEvent.click(screen.getByRole('button', { name: '反馈进展' }));
  await screen.findByText('结果待确认，请重试同一次请求');
  const first = fetcher.mock.calls.find(([url]) => url.endsWith('/commands'))!;
  fireEvent.click(screen.getByRole('button', { name: '重试同一次请求' }));
  await screen.findByText('合成说明', { selector: 'p' });
  const calls = fetcher.mock.calls.filter(([url]) => url.endsWith('/commands'));
  expect(calls).toHaveLength(2);
  expect(calls[1]).toEqual(first);
});

it('clears_private_text_and_discards_late_results_on_generation_change', async () => {
  const { fetcher } = harness('in_progress');
  fireEvent.change(await screen.findByRole('textbox'), { target: { value: '上一身份的正文' } });
  let reject!: (reason: unknown) => void;
  fetcher.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
  fireEvent.click(screen.getByRole('button', { name: '反馈进展' }));
  act(() => useAuthStore.getState().markAuthenticated());
  await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(''));
  await act(async () => { reject(new Error('Late synthetic failure')); });
  expect(screen.queryByText('结果待确认，请重试同一次请求')).toBeNull();
  expect(screen.queryByDisplayValue('上一身份的正文')).toBeNull();
});

it('loads_later_events_and_renders_feedback_as_text', async () => {
  const { fetcher } = harness('completed');
  await screen.findByRole('heading', { name: '已办结' });
  const event = (version: number) => ({ event_id: `12345678-1234-1234-1234-${String(version).padStart(12, '0')}`,
    work_object_id: 'internal-one', operation: version === 52 ? 'complete' : 'feedback',
    from_status: 'in_progress', to_status: version === 52 ? 'completed' : 'in_progress',
    result_version: version, occurred_at: moment, text: version === 52 ? '<b>最终证据</b>' : `进展${version}`, actor_role: 'assignee' });
  fetcher.mockImplementation(async (url: string) => new Response(JSON.stringify(url.includes('/events')
    ? url.includes('after_version=51')
      ? { items: [event(51), event(52)], has_more: false, next_after_version: null }
      : { items: Array.from({ length: 50 }, (_, i) => event(i + 2)), has_more: true, next_after_version: 51 }
    : { work_object_id: 'internal-one', status: 'completed', version: 52, accepted_at: moment,
      completed_at: moment, available_commands: [], unavailable_reason: null }), { headers: { ETag: etag } }));
  fireEvent.click(screen.getByRole('button', { name: '重新读取' }));
  fireEvent.click(await screen.findByRole('button', { name: '加载更多' }));
  expect(await screen.findByText('<b>最终证据</b>')).toBeVisible();
  expect(screen.getAllByText('进展51')).toHaveLength(1);
  expect(document.querySelector('article b')).toBeNull();
  expect(document.querySelectorAll('article')).toHaveLength(51);
  expect(fetcher.mock.calls.some(([url]) => url.includes('after_version=51&limit=50'))).toBe(true);
  expect(screen.queryByRole('button', { name: '加载更多' })).toBeNull();
});

it.each(['service', 'invalid-success'])('preserves the exact unresolved request after %s', async (mode) => {
  const { fetcher } = harness('in_progress');
  fireEvent.change(await screen.findByRole('textbox'), { target: { value: '不可重复的正文' } });
  fetcher.mockResolvedValueOnce(mode === 'service'
    ? new Response(JSON.stringify({ detail: { code: 'work_object_lifecycle_failed', message: 'synthetic' } }), { status: 503 })
    : new Response(JSON.stringify({ event: {}, replayed: false })));
  fireEvent.click(screen.getByRole('button', { name: '反馈进展' }));
  await screen.findByText('结果待确认，请重试同一次请求');
  const first = fetcher.mock.calls.find(([url]) => url.endsWith('/commands'))!;
  fireEvent.click(screen.getByRole('button', { name: '重新读取' }));
  await screen.findByRole('heading', { name: '办理中' });
  expect(screen.queryByRole('textbox')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '重试同一次请求' }));
  await screen.findByText('不可重复的正文', { selector: 'p' });
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/commands'))).toEqual([first, first]);
});

it('refreshes after 412 without automatically posting again', async () => {
  const { fetcher } = harness('in_progress');
  fireEvent.change(await screen.findByRole('textbox'), { target: { value: '需再次确认' } });
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ detail: {
    code: 'work_object_version_conflict', message: 'synthetic',
  } }), { status: 412 }));
  fireEvent.click(screen.getByRole('button', { name: '反馈进展' }));
  await screen.findByText('事项已更新，请核对最新状态后再操作。');
  await screen.findByRole('textbox');
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/commands'))).toHaveLength(1);
  expect(screen.queryByRole('button', { name: '重试同一次请求' })).toBeNull();
});

it('does not deny a committed result when its readback fails', async () => {
  const { fetcher } = harness('in_progress');
  fireEvent.change(await screen.findByRole('textbox'), { target: { value: '已提交的正文' } });
  const original = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (url, options) => {
    if (url.endsWith('/commands')) return original(url, options);
    throw new TypeError('Synthetic readback failure');
  });
  fireEvent.click(screen.getByRole('button', { name: '反馈进展' }));
  await screen.findByText('操作已提交，最新状态暂未读取');
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/commands'))).toHaveLength(1);
  expect(screen.queryByRole('button', { name: '重试同一次请求' })).toBeNull();
});

it('drops a late success and never persists private command data', async () => {
  const get = vi.spyOn(Storage.prototype, 'getItem');
  const set = vi.spyOn(Storage.prototype, 'setItem');
  const { fetcher } = harness('in_progress');
  fireEvent.change(await screen.findByRole('textbox'), { target: { value: '旧身份秘密正文' } });
  let resolve!: (value: Response) => void;
  fetcher.mockImplementationOnce(() => new Promise<Response>((done) => { resolve = done; }));
  fireEvent.click(screen.getByRole('button', { name: '反馈进展' }));
  act(() => useAuthStore.getState().markAuthenticated());
  await screen.findByRole('textbox');
  await act(async () => resolve(new Response(JSON.stringify({ replayed: false, event: {
    event_id: crypto.randomUUID(), work_object_id: 'internal-one', operation: 'feedback',
    from_status: 'in_progress', to_status: 'in_progress', result_version: 3,
    occurred_at: moment, text: '旧身份秘密正文', actor_role: 'assignee',
  } }))));
  expect(screen.queryByText('操作已提交')).toBeNull();
  expect(screen.queryByDisplayValue('旧身份秘密正文')).toBeNull();
  expect(get).not.toHaveBeenCalled(); expect(set).not.toHaveBeenCalled();
  get.mockRestore(); set.mockRestore();
});
