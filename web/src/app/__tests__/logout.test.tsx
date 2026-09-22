import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAuthStore } from '../../stores/authStore';
import { useLogout } from '../logout';

const posted = vi.fn();

beforeEach(() => {
  useAuthStore.setState({ status: 'authenticated', generation: 40 });
  posted.mockReset();
  vi.stubGlobal('BroadcastChannel', class {
    constructor(public name: string) {}
    postMessage(message: unknown) { posted(this.name, message); }
    close() {}
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it.each([
  { authenticated: false, extra: 1 },
  { authenticated: 'false' },
  { authenticated: 0 },
  { authenticated: null },
  { authenticated: [] },
  {},
  null,
])('logout_rejects_unconfirmed_payload_without_broadcast %j', async (payload) => {
  const fetchSpy = vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify(payload)));
  const { result } = renderHook(useLogout);
  await act(async () => { await result.current.logout(); });

  expect(result.current.failed, 'malformed payload must leave logout unconfirmed').toBe(true);
  expect(result.current.pending).toBe(false);
  expect(useAuthStore.getState()).toMatchObject({ status: 'authenticated', generation: 40 });
  expect(posted).not.toHaveBeenCalled();
  expect(fetchSpy).toHaveBeenCalledTimes(1);
});

it('current_confirmed_logout_broadcasts_exactly_once', async () => {
  let release!: (response: Response) => void;
  const fetchSpy = vi.spyOn(globalThis, 'fetch').mockReturnValueOnce(
    new Promise<Response>((resolve) => { release = resolve; }),
  );
  const { result } = renderHook(useLogout);
  let operation!: Promise<void>;
  act(() => { operation = result.current.logout(); });
  expect(result.current.pending).toBe(true);
  expect(posted).not.toHaveBeenCalled();
  expect(useAuthStore.getState().status).toBe('authenticated');
  await act(async () => {
    release(new Response(JSON.stringify({ authenticated: false })));
    await operation;
  });

  expect(useAuthStore.getState()).toMatchObject({ status: 'unauthenticated', generation: 41 });
  expect(result.current.failed).toBe(false);
  expect(posted).toHaveBeenCalledExactlyOnceWith('eternalai-auth', 'recheck-identity');
  expect(fetchSpy).toHaveBeenCalledExactlyOnceWith('/api/v1/auth/logout',
    expect.objectContaining({ method: 'POST', headers: expect.objectContaining({ 'X-EternalAI-CSRF': '1' }) }));
});

it.each([200, 401, 503])('old_generation_logout_result_never_broadcasts %s', async (status) => {
  let release!: (response: Response) => void;
  const fetchSpy = vi.spyOn(globalThis, 'fetch').mockReturnValueOnce(
    new Promise<Response>((resolve) => { release = resolve; }),
  );
  const { result } = renderHook(useLogout);
  let operation!: Promise<void>;
  act(() => { operation = result.current.logout(); });
  act(() => {
    useAuthStore.getState().markUnauthenticated();
    useAuthStore.getState().markAuthenticated();
  });
  expect(useAuthStore.getState()).toMatchObject({ status: 'authenticated', generation: 42 });
  await act(async () => {
    release(new Response(JSON.stringify(status === 200 ? { authenticated: false } : {
      detail: { code: 'logout_unavailable', message: 'Synthetic old failure' },
    }), { status }));
    await operation;
  });

  expect(posted, 'an old generation must not send a confirmed-logout broadcast').not.toHaveBeenCalled();
  expect(useAuthStore.getState()).toMatchObject({ status: 'authenticated', generation: 42 });
  expect(result.current.failed).toBe(false);
  expect(result.current.pending).toBe(false);
  expect(fetchSpy).toHaveBeenCalledTimes(1);
});

it.each([403, 503, 'network'] as const)('failed_logout_never_broadcasts %s', async (failure) => {
  const fetchSpy = vi.spyOn(globalThis, 'fetch');
  if (failure === 'network') fetchSpy.mockRejectedValueOnce(new TypeError('Synthetic network failure'));
  else fetchSpy.mockResolvedValueOnce(new Response(JSON.stringify({
    detail: { code: 'logout_unavailable', message: 'Synthetic failure' },
  }), { status: failure }));
  const { result } = renderHook(useLogout);
  await act(async () => { await result.current.logout(); });

  expect(result.current.failed).toBe(true);
  expect(result.current.pending).toBe(false);
  expect(posted).not.toHaveBeenCalled();
  expect(useAuthStore.getState()).toMatchObject({ status: 'authenticated', generation: 40 });
  expect(fetchSpy).toHaveBeenCalledTimes(1);
});
