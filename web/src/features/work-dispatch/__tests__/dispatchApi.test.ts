import { afterEach, expect, it, vi } from 'vitest';
import { postDispatch, readOptions, validOptions, validReceipt, optionKey } from '../dispatchApi';

afterEach(() => vi.unstubAllGlobals());
it('P1 uses_existing_transport_and_one_key', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], kind: 'department', has_more: false, next_cursor: null, snapshot_version: 1, unselectable_count: 0 })));
  vi.stubGlobal('fetch', fetch);
  const body = { kind: '通知' as const, title: 'synthetic', requirement: '', receipt_requirement: '', due_at: null, reminder_choices: [], targets: [{ kind: 'department' as const, department_id: 'd1' }] };
  const key = '00000000-0000-4000-8000-000000000001';
  await postDispatch(body, key);
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/work-objects/dispatch', {
    method: 'POST', headers: { 'Idempotency-Key': key, 'Content-Type': 'application/json', 'X-EternalAI-CSRF': '1' }, body: JSON.stringify(body), signal: undefined,
  });
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({ items: [], kind: 'department', has_more: false, next_cursor: null, snapshot_version: 1, unselectable_count: 0 })));
  await readOptions({ kind: 'department' });
  expect(fetch).toHaveBeenLastCalledWith('/api/v1/work-objects/dispatch-options?kind=department&limit=50', { method: 'GET', headers: {}, body: undefined, signal: undefined });
});
it('C7 rejects user fields that could select the wrong identity', () => {
  const user = { kind: 'user' as const, department_id: 'd1', department_display_name: 'D', directory_user_id: 'u1', display_name: 'N' };
  const page = { kind: 'user', items: [user], next_cursor: null, has_more: false, snapshot_version: 1, unselectable_count: 2 };
  expect(validOptions(page, { kind: 'user', department_id: 'd1' }, 50)).toBe(true);
  for (const bad of [{ directory_user_id: '' }, { directory_user_id: 1 }, { display_name: null }, { department_id: 'd2' }, { kind: 'department' }, { department_display_name: null }]) {
    expect(validOptions({ ...page, items: [{ ...user, ...bad }] }, { kind: 'user', department_id: 'd1' }, 50)).toBe(false);
  }
  for (const bad of [{ unselectable_count: 0.5 }, { snapshot_version: undefined }, { next_cursor: 3 }, { next_cursor: 'x', has_more: false }, { items: {} }]) {
    expect(validOptions({ ...page, ...bad }, { kind: 'user', department_id: 'd1' }, 50)).toBe(false);
  }
  expect(optionKey(user)).not.toBe(optionKey({ ...user, directory_user_id: 'u2' }));
});
it('P3 rejects duplicate receipt identities and mismatched counts', () => {
  const item = { work_object_id: 'x', state_authority: 'internal', handling_action: 'view_only' };
  expect(validReceipt({ created_count: 2, replayed: false, items: [item, { ...item, work_object_id: 'y' }] }, 2)).toBe(true);
  expect(validReceipt({ created_count: 2, replayed: false, items: [item, item] }, 2)).toBe(false);
  expect(validReceipt({ created_count: 1, replayed: false, items: [item] }, 2)).toBe(false);
});
