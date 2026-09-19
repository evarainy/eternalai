import { afterEach, describe, expect, it, vi } from 'vitest';
import { useAuthStore } from '../../../stores/authStore';
import { readLifecycle, sendLifecycleCommand, lifecycleErrorText } from '../workObjectLifecycleApi';
import { ApiError } from '../../../api/mutator';

const etag = `"wolc-${'a'.repeat(64)}"`;
const view = { work_object_id: 'internal-one', status: 'assigned', version: 1, accepted_at: null,
  completed_at: null, available_commands: ['accept'], unavailable_reason: null };
afterEach(() => vi.unstubAllGlobals());

describe('lifecycle transport', () => {
  it('uses_same_origin_etag_get_and_generated_csrf_command', async () => {
    const event = { event_id: '12345678-1234-1234-1234-123456789abc', work_object_id: 'internal-one',
      operation: 'accept', from_status: 'assigned', to_status: 'in_progress', result_version: 2,
      occurred_at: '2026-09-20T01:00:00.000001Z', text: null, actor_role: 'assignee' };
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(view), { headers: { ETag: etag } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ event, replayed: false })));
    vi.stubGlobal('fetch', fetcher);
    const signal = new AbortController().signal;
    expect(await readLifecycle('internal-one', signal)).toEqual({ view, etag });
    expect(fetcher.mock.calls[0]).toEqual(['/api/v1/work-objects/internal-one/lifecycle', {
      credentials: 'same-origin', cache: 'no-store', signal,
    }]);
    const key = crypto.randomUUID();
    expect(await sendLifecycleCommand('internal-one', { body: { operation: 'accept' }, key, etag })).toEqual({ event, replayed: false });
    expect(fetcher.mock.calls[1]![0]).toBe('/api/v1/work-objects/internal-one/lifecycle/commands');
    expect(fetcher.mock.calls[1]![1]).toMatchObject({ method: 'POST', body: '{"operation":"accept"}',
      headers: { 'Content-Type': 'application/json', 'X-EternalAI-CSRF': '1', 'If-Match': etag, 'Idempotency-Key': key } });
  });
  it.each([null, 'W/"tag"', '*', '"bad"'])('rejects invalid ETag %s', async (tag) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(view), { headers: tag ? { ETag: tag } : {} })));
    await expect(readLifecycle('internal-one')).rejects.toMatchObject({ code: 'lifecycle_response_invalid' });
  });
  it('old 401 does not invalidate a newer authentication generation', async () => {
    let resolve!: (value: Response) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
    useAuthStore.getState().markAuthenticated();
    const request = readLifecycle('internal-one');
    useAuthStore.getState().markAuthenticated();
    const generation = useAuthStore.getState().generation;
    resolve(new Response('', { status: 401 }));
    await expect(request).rejects.toMatchObject({ code: 'authentication_required' });
    expect(useAuthStore.getState()).toMatchObject({ generation, status: 'authenticated' });
  });
  it('maps_directory_errors_by_code_only', () => {
    for (const message of ['Old English message.', 'New English message.']) {
      expect(lifecycleErrorText(new ApiError(403, 'directory_membership_missing', message))).toBe('当前没有可用的部门成员身份。');
    }
  });
});
