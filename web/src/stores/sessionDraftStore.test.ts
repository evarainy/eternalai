import { act, renderHook } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAuthStore } from './authStore';
import { createSessionDraftStore, isCurrentDraftSession, removeLegacyDrafts, useDraftSession,
  LEGACY_DISPATCH_DRAFT_KEY, LEGACY_SOFTWARE_DRAFT_KEY } from './sessionDraftStore';

beforeEach(() => { useAuthStore.getState().markAuthenticated(); });
afterEach(() => vi.restoreAllMocks());
const token = () => { const hook = renderHook(useDraftSession); const value = hook.result.current; hook.unmount(); return value; };

it.each(['unknown', 'unauthenticated'] as const)('rejects_unknown_and_unauthenticated_access: %s', (status) => {
  const slot = createSessionDraftStore((value: string) => value);
  const captured = token()!;
  useAuthStore.setState({ status });
  const forged = { ...captured, generation: useAuthStore.getState().generation };
  expect(slot.save(forged, 'A')).toBe(false);
  expect(slot.read(forged)).toBeNull();
  expect(slot.clear(forged)).toBe(false);
  expect(token()).toBeNull();
  slot.dispose();
});

it.each(['direct', 'batched', 'unknown', 'setState'] as const)('invalidates_both_slots_on_every_authentication_transition: %s', (kind) => {
  const slots = [createSessionDraftStore((v: string) => v), createSessionDraftStore((v: string) => v)];
  const old = token();
  for (const slot of slots) expect(slot.save(old, 'A')).toBe(true);
  act(() => {
    if (kind === 'batched') useAuthStore.getState().markUnauthenticated();
    if (kind === 'unknown') useAuthStore.setState({ status: 'unknown' });
    else if (kind === 'setState') useAuthStore.setState({ generation: useAuthStore.getState().generation + 1 });
    else useAuthStore.getState().markAuthenticated();
  });
  for (const slot of slots) {
    expect(slot.read(token())).toBeNull();
    expect(slot.read(old)).toBeNull();
    expect(slot.save(old, 'late A')).toBe(false);
    slot.dispose();
  }
});

it('rejects_late_save_and_clear_from_an_earlier_token', () => {
  const slot = createSessionDraftStore((v: string) => v);
  const a = token();
  const lateSave = () => slot.save(a, 'A');
  const lateClear = () => slot.clear(a);
  act(() => useAuthStore.getState().markAuthenticated());
  const b = token();
  expect(slot.save(b, 'B')).toBe(true);
  expect(lateSave()).toBe(false);
  expect(lateClear()).toBe(false);
  expect(slot.read(b)).toBe('B');
  expect(token()).toBe(b);
  expect(slot.clear(b)).toBe(true);
  expect(slot.read(b)).toBeNull();
  slot.dispose();
});

it.each(['pagehide', 'pageshow'])('invalidates_on_pagehide_and_persisted_pageshow: %s', (eventName) => {
  const slot = createSessionDraftStore((v: string) => v);
  const a = token();
  expect(slot.save(a, 'A')).toBe(true);
  window.dispatchEvent(new Event('visibilitychange'));
  window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: false }));
  expect(slot.read(a)).toBe('A');
  act(() => window.dispatchEvent(new PageTransitionEvent(eventName, { persisted: true })));
  expect(isCurrentDraftSession(a)).toBe(false);
  expect(slot.read(token())).toBeNull();
  expect(slot.save(a, 'late A')).toBe(false);
  slot.dispose();
});

it('retains_the_previous_snapshot_when_copy_fails', () => {
  let failRead = false;
  const copy = (v: string) => { if (v === 'fail' || failRead) throw new Error('synthetic'); return v; };
  const slot = createSessionDraftStore(copy);
  const a = token();
  expect(slot.save(a, 'original')).toBe(true);
  expect(slot.save(a, 'fail')).toBe(false);
  expect(slot.read(a)).toBe('original');
  failRead = true;
  expect(slot.read(a)).toBeNull();
  failRead = false;
  expect(slot.read(a)).toBe('original');
  slot.dispose();
});

it('rechecks_the_session_after_copy_before_reading_or_committing', () => {
  let switchSession = false;
  const slot = createSessionDraftStore((v: string) => {
    if (switchSession) useAuthStore.getState().markAuthenticated();
    return v;
  });
  let a = token();
  expect(slot.save(a, 'original')).toBe(true);
  switchSession = true;
  expect(slot.read(a)).toBeNull();
  a = token();
  expect(slot.save(a, 'late')).toBe(false);
  switchSession = false;
  expect(slot.read(token())).toBeNull();
  slot.dispose();
});

it('attempts_each_legacy_key_when_removal_is_denied', () => {
  const remove = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation((key) => {
    if (key === LEGACY_DISPATCH_DRAFT_KEY) throw new Error('synthetic');
  });
  expect(removeLegacyDrafts()).toBe(false);
  expect(remove.mock.calls).toEqual([[LEGACY_DISPATCH_DRAFT_KEY], [LEGACY_SOFTWARE_DRAFT_KEY]]);
  remove.mockRestore();
  const getter = vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => { throw new Error('synthetic'); });
  expect(removeLegacyDrafts()).toBe(false);
  expect(getter).toHaveBeenCalledTimes(2);
  for (const label of ['dispatch', 'software']) {
    const slot = createSessionDraftStore((v: string) => v);
    expect(slot.save(token(), label)).toBe(true);
    expect(slot.read(token())).toBe(label);
    slot.dispose();
  }
});

it('never_imports_persist_or_features', () => {
  const source = readFileSync(resolve('src/stores/sessionDraftStore.ts'), 'utf8');
  expect(source).not.toMatch(/(?:from|import\s*\()\s*['"][^'"]*(?:zustand\/middleware|features\/)/);
});
