import { useLayoutEffect } from 'react';
import type { PropsWithChildren } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { MeResponse } from '../../generated/me/me.schemas';
import { useAuthStore } from '../../stores/authStore';
import { identityQueryKey, useIdentityBootstrap } from '../identity';

const identity: MeResponse = {
  authenticated: true,
  display_name: 'Synthetic bootstrap identity',
  org: null,
  org_status: 'unavailable',
  avatar_path: null,
};

let client: QueryClient;
let unsubscribe: () => void;

beforeEach(() => {
  useAuthStore.setState({ status: 'unknown', generation: 900 });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  client.setQueryData(identityQueryKey(900), identity);
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(identity)));
  unsubscribe = () => {};
});

afterEach(() => {
  cleanup();
  unsubscribe();
  client.clear();
  vi.restoreAllMocks();
});

function Wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function recordTransitions() {
  const snapshot = () => {
    const { status, generation } = useAuthStore.getState();
    return { status, generation };
  };
  const states = [snapshot()];
  unsubscribe = useAuthStore.subscribe(() => states.push(snapshot()));
  return states;
}

it('late_identity_response_cannot_restore_logged_out_state', async () => {
  const states = recordTransitions();
  const mounted = renderHook(() => {
    useIdentityBootstrap();
    // The render has a successful old query. Logout lands before that render's passive effect.
    useLayoutEffect(() => {
      expect(useAuthStore.getState().status).toBe('unknown');
      expect(client.getQueryData(identityQueryKey(900))).toEqual(identity);
      useAuthStore.getState().markUnauthenticated();
    }, []);
  }, { wrapper: Wrapper });

  const assertStillLoggedOut = () => {
    expect(states[1]).toEqual({ status: 'unauthenticated', generation: 901 });
    expect(states.slice(1).some((state) => state.status === 'authenticated'),
      'logout must never be followed by authenticated, including transient transitions').toBe(false);
    expect(useAuthStore.getState().status).toBe('unauthenticated');
    expect(useAuthStore.getState().generation).toBe(901);
    expect(client.getQueryData(identityQueryKey(useAuthStore.getState().generation))).toBeUndefined();
  };
  assertStillLoggedOut();
  await act(async () => { mounted.rerender(); });
  assertStillLoggedOut();
  expect(globalThis.fetch).not.toHaveBeenCalled();
});

it('same_bootstrap_timing_authenticates_a_valid_identity_without_logout', async () => {
  const states = recordTransitions();
  renderHook(() => {
    useIdentityBootstrap();
    useLayoutEffect(() => {
      expect(useAuthStore.getState().status).toBe('unknown');
      expect(client.getQueryData(identityQueryKey(900))).toEqual(identity);
    }, []);
  }, { wrapper: Wrapper });

  expect(states).toEqual([
    { status: 'unknown', generation: 900 },
    { status: 'authenticated', generation: 901 },
  ]);
  await waitFor(() => expect(client.getQueryData(identityQueryKey(901))).toEqual(identity));
  expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  expect(globalThis.fetch).toHaveBeenCalledWith('/api/v1/me', expect.objectContaining({ method: 'GET' }));
});
