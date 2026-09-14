import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAuthStore } from '../../../stores/authStore';
import { useDraftSession } from '../../../stores/sessionDraftStore';
import { loadDraft, saveDraft, parseDraft, EMPTY_DRAFT, DRAFT_STORAGE_KEY } from '../dispatchDraft';

beforeEach(() => useAuthStore.getState().markAuthenticated());
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const token = () => { const hook = renderHook(useDraftSession); const value = hook.result.current; hook.unmount(); return value; };

it('round_trips_an_independent_current_session_snapshot', () => {
  const a = token();
  const value = { ...EMPTY_DRAFT, title: 'A', targets: ['one'] };
  expect(saveDraft(value, a)).toBe(true);
  value.targets.push('mutated source');
  const first = loadDraft(a);
  (first.targets as string[]).push('mutated read');
  expect(loadDraft(a)).toEqual({ ...EMPTY_DRAFT, title: 'A', targets: ['one'] });
  act(() => useAuthStore.getState().markAuthenticated());
  const fresh = loadDraft(token());
  (fresh.targets as string[]).push('mutated default');
  expect(loadDraft(token())).toEqual(EMPTY_DRAFT);
  expect(loadDraft(null)).toEqual(EMPTY_DRAFT);
  expect(saveDraft(value, null)).toBe(false);
});

it.each(['{broken', JSON.stringify({ title: 'unowned A', targets: ['A'] })])('never_reads_or_writes_legacy_browser_storage: %s', (legacy) => {
  localStorage.setItem(DRAFT_STORAGE_KEY, legacy);
  const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('synthetic'); });
  const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('synthetic'); });
  const push = vi.spyOn(history, 'pushState');
  const replace = vi.spyOn(history, 'replaceState');
  const open = vi.fn(() => { throw new Error('synthetic'); });
  vi.stubGlobal('indexedDB', { open });
  const beforeUrl = location.href;
  expect(loadDraft(token())).toEqual(EMPTY_DRAFT);
  expect(saveDraft({ ...EMPTY_DRAFT, title: 'B' }, token())).toBe(true);
  expect(loadDraft(token()).title).toBe('B');
  expect(get).not.toHaveBeenCalled();
  expect(set).not.toHaveBeenCalled();
  expect(push).not.toHaveBeenCalled();
  expect(replace).not.toHaveBeenCalled();
  expect(open).not.toHaveBeenCalled();
  expect(location.href).toBe(beforeUrl);
});

it('starts_empty_in_a_new_document_even_when_generation_repeats', async () => {
  localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify({ title: 'unowned A' }));
  const originalGet = Storage.prototype.getItem;
  for (const first of [true, false]) {
    vi.resetModules();
    const auth = await import('../../../stores/authStore');
    const lifecycle = await import('../../../stores/sessionDraftStore');
    const draft = await import('../dispatchDraft');
    auth.useAuthStore.getState().markAuthenticated();
    expect(auth.useAuthStore.getState().generation).toBe(1);
    const hook = renderHook(lifecycle.useDraftSession);
    expect(draft.loadDraft(hook.result.current)).toEqual(EMPTY_DRAFT);
    if (first) {
      expect(draft.saveDraft({ ...EMPTY_DRAFT, title: 'A' }, hook.result.current)).toBe(true);
      expect(draft.loadDraft(hook.result.current).title).toBe('A');
    }
    hook.unmount();
    lifecycle.disposeDraftSession();
  }
  expect(originalGet.call(localStorage, DRAFT_STORAGE_KEY)).toBe(JSON.stringify({ title: 'unowned A' }));
});

it('preserves_closed_choices_and_deduplication', () => {
  for (const kind of ['通知', '督办令', '工作任务', '提醒']) {
    expect(parseDraft({ kind, targets: [' one ', 'two', 'one', '', 4], reminders: ['提前 7 天', '提前 7 天', 'bad'] }))
      .toEqual({ ...EMPTY_DRAFT, kind, targets: ['one', 'two'], reminders: ['提前 7 天'] });
  }
  expect(parseDraft({ kind: 'bad', reminders: 'bad', title: 5 })).toEqual(EMPTY_DRAFT);
  expect(parseDraft('{broken')).toEqual(EMPTY_DRAFT);
  expect(parseDraft({ reminders: [] }).reminders).toEqual([]);
  const defaults = parseDraft(null);
  (defaults.reminders as string[]).push('pollution');
  expect(parseDraft(null)).toEqual(EMPTY_DRAFT);
});

it('P5 keeps_content_without_submission_state and retains legacy intent for confirmation', () => {
  const parsed = parseDraft({ title: 'synthetic', assignee: 'old-owner', visibility: 'old-visibility', targets: ['old-target'], dueAt: '2026-09-11T01:30', dueInstant: '2026-09-10T16:30:00Z', dueZone: 'Asia/Tokyo', dueOffset: 'UTC+09:00', key: 'private-key', body: {}, receipt: 'text', selected: ['private-person'] });
  expect(parsed).toMatchObject({ assignee: 'old-owner', visibility: 'old-visibility', targets: ['old-target'], dueInstant: '2026-09-10T16:30:00Z', dueZone: 'Asia/Tokyo', dueOffset: 'UTC+09:00' });
  expect(Object.keys(parsed).sort()).toEqual(['assignee', 'brief', 'dueAt', 'dueInstant', 'dueOffset', 'dueZone', 'kind', 'receipt', 'reminders', 'requirement', 'targets', 'title', 'visibility']);
  expect(parseDraft({ dueInstant: 'bad' })).not.toHaveProperty('dueInstant');
});
