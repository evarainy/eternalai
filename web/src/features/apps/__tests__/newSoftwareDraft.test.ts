import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useAuthStore } from '../../../stores/authStore';
import { useDraftSession } from '../../../stores/sessionDraftStore';
import { loadNewSoftwareDraft, saveNewSoftwareDraft, parseNewSoftwareDraft, EMPTY_NEW_SOFTWARE_DRAFT, NEW_SOFTWARE_DRAFT_KEY } from '../newSoftwareDraft';

beforeEach(() => useAuthStore.getState().markAuthenticated());
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
const token = () => { const hook = renderHook(useDraftSession); const value = hook.result.current; hook.unmount(); return value; };

it('round_trips_an_independent_current_session_snapshot', () => {
  const a = token();
  const value = { ...EMPTY_NEW_SOFTWARE_DRAFT, name: 'A', visibleTo: ['one'] };
  expect(saveNewSoftwareDraft(value, a)).toBe(true);
  value.visibleTo.push('mutated source');
  const first = loadNewSoftwareDraft(a);
  (first.visibleTo as string[]).push('mutated read');
  expect(loadNewSoftwareDraft(a)).toEqual({ ...EMPTY_NEW_SOFTWARE_DRAFT, name: 'A', visibleTo: ['one'] });
  act(() => useAuthStore.getState().markAuthenticated());
  const fresh = loadNewSoftwareDraft(token());
  (fresh.visibleTo as string[]).push('mutated default');
  expect(loadNewSoftwareDraft(token())).toEqual(EMPTY_NEW_SOFTWARE_DRAFT);
  expect(loadNewSoftwareDraft(null)).toEqual(EMPTY_NEW_SOFTWARE_DRAFT);
  expect(saveNewSoftwareDraft(value, null)).toBe(false);
});

it.each(['{broken', JSON.stringify({ name: 'unowned A', visibleTo: ['A'] })])('never_reads_or_writes_legacy_browser_storage: %s', (legacy) => {
  localStorage.setItem(NEW_SOFTWARE_DRAFT_KEY, legacy);
  const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('synthetic'); });
  const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('synthetic'); });
  const push = vi.spyOn(history, 'pushState');
  const replace = vi.spyOn(history, 'replaceState');
  const open = vi.fn(() => { throw new Error('synthetic'); });
  vi.stubGlobal('indexedDB', { open });
  const beforeUrl = location.href;
  expect(loadNewSoftwareDraft(token())).toEqual(EMPTY_NEW_SOFTWARE_DRAFT);
  expect(saveNewSoftwareDraft({ ...EMPTY_NEW_SOFTWARE_DRAFT, name: 'B' }, token())).toBe(true);
  expect(loadNewSoftwareDraft(token()).name).toBe('B');
  expect(get).not.toHaveBeenCalled();
  expect(set).not.toHaveBeenCalled();
  expect(push).not.toHaveBeenCalled();
  expect(replace).not.toHaveBeenCalled();
  expect(open).not.toHaveBeenCalled();
  expect(location.href).toBe(beforeUrl);
});

it('starts_empty_in_a_new_document_even_when_generation_repeats', async () => {
  localStorage.setItem(NEW_SOFTWARE_DRAFT_KEY, JSON.stringify({ name: 'unowned A' }));
  const originalGet = Storage.prototype.getItem;
  for (const first of [true, false]) {
    vi.resetModules();
    const auth = await import('../../../stores/authStore');
    const lifecycle = await import('../../../stores/sessionDraftStore');
    const draft = await import('../newSoftwareDraft');
    auth.useAuthStore.getState().markAuthenticated();
    expect(auth.useAuthStore.getState().generation).toBe(1);
    const hook = renderHook(lifecycle.useDraftSession);
    expect(draft.loadNewSoftwareDraft(hook.result.current)).toEqual(EMPTY_NEW_SOFTWARE_DRAFT);
    if (first) {
      expect(draft.saveNewSoftwareDraft({ ...EMPTY_NEW_SOFTWARE_DRAFT, name: 'A' }, hook.result.current)).toBe(true);
      expect(draft.loadNewSoftwareDraft(hook.result.current).name).toBe('A');
    }
    hook.unmount();
    lifecycle.disposeDraftSession();
  }
  expect(originalGet.call(localStorage, NEW_SOFTWARE_DRAFT_KEY)).toBe(JSON.stringify({ name: 'unowned A' }));
});

it('preserves_supported_software_choices', () => {
  for (const source of ['existing_system', 'published_software']) {
    for (const binding of ['required', 'not_required']) {
      for (const risk of ['read_only', 'writes_data']) {
        expect(parseNewSoftwareDraft({ source, binding, risk, openMode: 'embedded', visibleTo: [' one ', 'two', 'one', '', 4] }))
          .toEqual({ ...EMPTY_NEW_SOFTWARE_DRAFT, source, binding, risk, openMode: 'new_window', visibleTo: ['one', 'two'] });
      }
    }
  }
  expect(parseNewSoftwareDraft({ source: 'bad', binding: 'bad', risk: 'bad', name: 4 })).toEqual(EMPTY_NEW_SOFTWARE_DRAFT);
  expect(parseNewSoftwareDraft('{broken')).toEqual(EMPTY_NEW_SOFTWARE_DRAFT);
});
