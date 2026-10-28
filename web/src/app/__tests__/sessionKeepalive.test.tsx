import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAuthStore } from '../../stores/authStore';
import { useAppSessionRenewal, useSessionKeepalive } from '../sessionKeepalive';

const OA_URL = '/api/v1/credential-bindings/oa/keepalive';
const RENEW_URL = '/api/v1/auth/renew';
const SESSION_URL = '/api/v1/auth/session';

function session() {
  const now = Date.now();
  return {
    idle_expires_at: new Date(now + 7_200_000).toISOString(),
    absolute_expires_at: null,
    server_time: new Date(now).toISOString(),
  };
}

const calls = (url: string) => vi.mocked(globalThis.fetch).mock.calls
  .filter(([input]) => String(input) === url).length;

beforeEach(() => {
  vi.useFakeTimers();
  useAuthStore.setState({ status: 'authenticated', generation: 30 });
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const body = url === OA_URL ? { oa: 'disabled' }
      : url === RENEW_URL ? { status: 'renewed', reason: null, session: session() }
        : session();
    return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
  });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function minute() {
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
}

// Real input a little after the previous tick, as a person would type.
async function input() {
  await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
  window.dispatchEvent(new KeyboardEvent('keydown'));
}

it('app_renewal_keeps_running_after_oa_keepalive_reports_disabled', async () => {
  renderHook(() => {
    useSessionKeepalive();
    useAppSessionRenewal();
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(calls(SESSION_URL)).toBe(1);

  // No real input yet: neither scheduler sends anything.
  await minute();
  expect(calls(OA_URL)).toBe(0);
  expect(calls(RENEW_URL)).toBe(0);

  await input();
  await minute();
  expect(calls(OA_URL)).toBe(1);
  expect(calls(RENEW_URL)).toBe(1);

  // OA is now disabled and stops its own timer; input still drives the application renewal.
  await input();
  await minute();
  await input();
  await minute();
  expect(calls(OA_URL)).toBe(1);
  expect(calls(RENEW_URL)).toBe(3);

  // Without new input after the last renew request, no further renew is sent.
  await minute();
  expect(calls(RENEW_URL)).toBe(3);
});
