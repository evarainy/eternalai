import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { MeResponse } from '../../generated/me/me.schemas';
import { useAuthStore } from '../../stores/authStore';
import { useIdentityRevalidation } from '../identity';

const identity: MeResponse = {
  authenticated: true,
  display_name: 'Synthetic revalidation identity',
  org: null,
  org_status: 'unavailable',
  avatar_path: null,
};

class SyntheticAuthChannel {
  static instances: SyntheticAuthChannel[] = [];
  onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
  closed = false;
  constructor(public name: string) { SyntheticAuthChannel.instances.push(this); }
  close() { this.closed = true; }
  static notify() {
    for (const channel of this.instances) {
      if (!channel.closed) channel.onmessage?.({ data: 'recheck-identity' } as MessageEvent);
    }
  }
}

beforeEach(() => {
  useAuthStore.setState({ status: 'authenticated', generation: 40 });
  SyntheticAuthChannel.instances = [];
  vi.stubGlobal('BroadcastChannel', SyntheticAuthChannel);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('late_successful_revalidation_cannot_clear_new_generation_failure', async () => {
  let release!: (response: Response) => void;
  const fetchSpy = vi.spyOn(globalThis, 'fetch')
    .mockReturnValueOnce(new Promise<Response>((resolve) => { release = resolve; }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      detail: { code: 'authentication_unavailable', message: 'Synthetic current failure' },
    }), { status: 503 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(identity)));
  const { result } = renderHook(useIdentityRevalidation);
  expect(result.current).toBe(false);
  act(() => SyntheticAuthChannel.notify());
  expect(fetchSpy).toHaveBeenCalledTimes(1);
  act(() => {
    useAuthStore.getState().markUnauthenticated();
    useAuthStore.getState().markAuthenticated();
  });
  await act(async () => { SyntheticAuthChannel.notify(); });
  expect(fetchSpy).toHaveBeenCalledTimes(2);
  expect(result.current).toBe(true);
  expect(useAuthStore.getState()).toMatchObject({ status: 'authenticated', generation: 42 });

  const oldResponse = new Response(JSON.stringify(identity));
  const oldJson = vi.spyOn(oldResponse, 'json');
  await act(async () => { release(oldResponse); });
  expect(oldJson).toHaveBeenCalledTimes(1);
  expect(result.current, 'late old 200 must preserve the current generation error').toBe(true);
  expect(useAuthStore.getState()).toMatchObject({ status: 'authenticated', generation: 42 });
  expect(fetchSpy).toHaveBeenCalledTimes(2);

  // Positive control: a successful recheck belonging to the current generation clears the error.
  await act(async () => { SyntheticAuthChannel.notify(); });
  expect(result.current).toBe(false);
  expect(fetchSpy).toHaveBeenCalledTimes(3);
  for (const call of fetchSpy.mock.calls) {
    expect(call).toEqual(['/api/v1/me', expect.objectContaining({ method: 'GET' })]);
  }
});

it.each([200, 401, 503])('mounted_focus_without_channel_rechecks_server %s', async (status) => {
  vi.stubGlobal('BroadcastChannel', undefined);
  const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(
    status === 401 ? '' : JSON.stringify(status === 200 ? identity : {
      detail: { code: 'authentication_unavailable', message: 'Synthetic focus failure' },
    }), { status },
  ));
  const { result, unmount } = renderHook(useIdentityRevalidation);
  expect(fetchSpy).not.toHaveBeenCalled();
  await act(async () => { window.dispatchEvent(new Event('focus')); });

  expect(fetchSpy, 'mounted focus must issue a real generated-client GET without BroadcastChannel')
    .toHaveBeenCalledExactlyOnceWith('/api/v1/me', expect.objectContaining({ method: 'GET' }));
  if (status === 401) {
    expect(useAuthStore.getState()).toMatchObject({ status: 'unauthenticated', generation: 41 });
    expect(result.current).toBe(false);
  } else {
    expect(useAuthStore.getState()).toMatchObject({ status: 'authenticated', generation: 40 });
    expect(result.current).toBe(status === 503);
  }

  unmount();
  act(() => { window.dispatchEvent(new Event('focus')); });
  expect(fetchSpy).toHaveBeenCalledTimes(1);
});
