import type { PropsWithChildren } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { MeResponse } from '../../generated/me/me.schemas';
import { useAIDockStore } from '../../stores/aiDockStore';
import { useAuthStore } from '../../stores/authStore';
import { AIDock } from '../AIDock';
import { identityQueryKey } from '../identity';
import { REAUTH_STORAGE_KEY, returnUnsentMessage, useReauthRestore } from '../reauthRestore';

const me = (scope: string): MeResponse => ({
  authenticated: true, display_name: 'Synthetic restore identity', org: null,
  org_status: 'unavailable', avatar_path: null, restore_scope: scope,
});

let client: QueryClient;

beforeEach(() => {
  window.sessionStorage.clear();
  useAIDockStore.getState().clearSession();
  useAuthStore.setState({ status: 'authenticated', generation: 10 });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  client.setQueryData(identityQueryKey(10), me('scope-a'));
  // Identity reads stay pending: each test supplies /me of a generation explicitly.
  vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise<Response>(() => {}));
});

afterEach(() => {
  cleanup();
  client.clear();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

function Wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function signInWith(scope: string) {
  act(() => {
    client.setQueryData(identityQueryKey(useAuthStore.getState().generation + 1), me(scope));
    useAuthStore.getState().markAuthenticated();
  });
}

function stored(): Record<string, unknown> | null {
  const raw = window.sessionStorage.getItem(REAUTH_STORAGE_KEY);
  return raw === null ? null : JSON.parse(raw) as Record<string, unknown>;
}

function reloadWithSealedCopy(): void {
  window.dispatchEvent(new Event('pagehide'));
  const survived = window.sessionStorage.getItem(REAUTH_STORAGE_KEY);
  expect(survived).not.toBeNull();
  // A new page instance: fresh stores, generations restart, only this tab's storage survives.
  cleanup();
  act(() => {
    useAuthStore.setState({ status: 'unknown', generation: 0 });
    useAIDockStore.getState().clearSession();
  });
  window.sessionStorage.setItem(REAUTH_STORAGE_KEY, survived!);
  renderHook(() => { useReauthRestore(); }, { wrapper: Wrapper });
}

it('full_reload_keeps_the_copy_sealed_until_fresh_me_and_restores_on_same_scope', async () => {
  renderHook(() => { useReauthRestore(); }, { wrapper: Wrapper });
  act(() => {
    useAIDockStore.setState({
      draft: 'half typed', sessionId: 'session-a', mode: 'drawer',
      transcript: [{ role: 'user', text: 'earlier question', requestId: 'r-0' }],
    });
  });
  reloadWithSealedCopy();

  // Authenticated, but /me of the new generation has not arrived: nothing is shown yet.
  act(() => { useAuthStore.getState().markAuthenticated(); });
  expect(useAIDockStore.getState().draft).toBe('');
  expect(useAIDockStore.getState().transcript).toEqual([]);
  expect(stored()).not.toBeNull();

  act(() => { client.setQueryData(identityQueryKey(useAuthStore.getState().generation), me('scope-a')); });
  await waitFor(() => {
    expect(useAIDockStore.getState()).toMatchObject({
      draft: 'half typed', sessionId: 'session-a', mode: 'drawer',
      transcript: [{ role: 'user', text: 'earlier question', requestId: 'r-0' }],
    });
  });
  expect(stored()).toBeNull();
});

it('full_reload_with_a_different_scope_clears_the_copy_without_showing_it', () => {
  renderHook(() => { useReauthRestore(); }, { wrapper: Wrapper });
  act(() => { useAIDockStore.setState({ draft: 'previous account text', sessionId: 'session-a' }); });
  reloadWithSealedCopy();

  signInWith('scope-b');
  expect(stored()).toBeNull();
  expect(useAIDockStore.getState()).toMatchObject({ draft: '', sessionId: null, transcript: [] });
});

it('401_returns_the_message_to_the_input_and_late_old_generation_callbacks_change_nothing', async () => {
  vi.mocked(globalThis.fetch).mockImplementation(async (input) => String(input) === '/api/v1/runtime/handle'
    ? new Response(null, { status: 401 })
    : new Promise<Response>(() => {}));
  render(<Wrapper><AIDock /></Wrapper>);
  renderHook(() => { useReauthRestore(); }, { wrapper: Wrapper });
  act(() => { useAIDockStore.setState({ mode: 'drawer' }); });

  fireEvent.change(screen.getByLabelText('要 AI 帮什么'), { target: { value: 'please summarize' } });
  fireEvent.click(screen.getByRole('button', { name: /发\s*送/ }));
  await waitFor(() => { expect(useAuthStore.getState().status).toBe('unauthenticated'); });
  await waitFor(() => { expect(stored()).toMatchObject({ dock: { draft: 'please summarize' } }); });
  const first = stored()!;
  const firstDock = first.dock as { sessionId: string; transcript: unknown[] };
  const [requestId] = first.returned as string[];
  expect(firstDock.transcript).toEqual([]);
  const withdrawn = { generation: 10, sessionId: firstDock.sessionId, requestId: requestId! };
  const beforeRepeat = window.sessionStorage.getItem(REAUTH_STORAGE_KEY);
  returnUnsentMessage(withdrawn, 'please summarize');
  expect(window.sessionStorage.getItem(REAUTH_STORAGE_KEY)).toBe(beforeRepeat);
  act(() => { useAIDockStore.getState().clearSession(); });

  signInWith('scope-a');
  expect(screen.getByLabelText('要 AI 帮什么')).toHaveValue('please summarize');
  expect(useAIDockStore.getState().transcript).toEqual([]);

  // The restored conversation ends again; a late 401 of the old generation must not edit it.
  act(() => {
    useAIDockStore.getState().appendTranscript({ role: 'user', text: 'newer', requestId: 'r-new' });
    useAuthStore.getState().markUnauthenticated();
  });
  const newer = window.sessionStorage.getItem(REAUTH_STORAGE_KEY);
  expect(JSON.parse(newer!)).toMatchObject({ generation: 12 });
  returnUnsentMessage({ ...withdrawn, requestId: 'r-new' }, 'newer');
  returnUnsentMessage(withdrawn, 'please summarize');
  expect(window.sessionStorage.getItem(REAUTH_STORAGE_KEY)).toBe(newer);
});

function workObjectsContext(workObjectId = 'work-1') {
  return {
    surface_id: 'work-objects',
    organization_scope: null,
    work_object_refs: [{ work_object_id: workObjectId }],
    source_refs: [],
    filters: [{ field: 'view', operator: 'equals', value: 'today', source: 'visible_control' }],
    selected_metric: null,
    allowed_capabilities: ['oa.work.read'],
    freshness: { state: 'reported', observed_at: '2026-10-28T09:00:00Z' },
    visibility: 'principal',
  };
}

it('relogin_on_a_page_context_page_keeps_the_draft_when_the_page_declares_the_same_context', () => {
  renderHook(() => { useReauthRestore(); }, { wrapper: Wrapper });
  act(() => {
    useAIDockStore.getState().registerPageContext(workObjectsContext());
    useAIDockStore.setState({
      draft: 'half typed', sessionId: 'session-w',
      transcript: [{ role: 'user', text: 'earlier question', requestId: 'r-w' }],
    });
    useAuthStore.getState().markUnauthenticated();
    useAIDockStore.getState().clearSession();
  });

  signInWith('scope-a');
  // The work-objects page declares its context only after its data loads, i.e. after restore.
  act(() => { useAIDockStore.getState().registerPageContext(workObjectsContext()); });
  expect(useAIDockStore.getState()).toMatchObject({
    draft: 'half typed', sessionId: 'session-w', contextNotice: null,
    transcript: [{ role: 'user', text: 'earlier question', requestId: 'r-w' }],
  });

  // Isolation is unchanged: another object binding still starts a new conversation.
  act(() => { useAIDockStore.getState().registerPageContext(workObjectsContext('work-2')); });
  expect(useAIDockStore.getState()).toMatchObject({ draft: '', sessionId: null, transcript: [] });
});
