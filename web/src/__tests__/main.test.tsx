import { afterEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ render: vi.fn(), createRoot: vi.fn() }));
vi.mock('react-dom/client', () => ({ createRoot: mocks.createRoot }));
vi.mock('../App', () => ({ default: () => null }));
let lifecycle: typeof import('../stores/sessionDraftStore') | undefined;
afterEach(() => { lifecycle?.disposeDraftSession(); vi.restoreAllMocks(); });

it.each(['{broken', '{"title":"synthetic A"}'])('removes_both_legacy_keys_before_the_first_render_without_reading_them: %s', async (content) => {
  vi.resetModules();
  const keys = ['eternalai.work-dispatch.draft', 'eternalai.apps.new-software-draft'];
  keys.forEach((key) => localStorage.setItem(key, content));
  localStorage.setItem('unrelated.synthetic', 'keep');
  const read = Storage.prototype.getItem;
  const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('must not read'); });
  const set = vi.spyOn(Storage.prototype, 'setItem');
  const enumerate = vi.spyOn(Storage.prototype, 'key');
  const clear = vi.spyOn(Storage.prototype, 'clear');
  const remove = vi.spyOn(Storage.prototype, 'removeItem');
  mocks.createRoot.mockImplementation(() => {
    expect(remove.mock.calls).toEqual(keys.map((key) => [key]));
    return { render: mocks.render };
  });
  mocks.render.mockReset();
  mocks.render.mockImplementation(() => {
    expect(keys.map((key) => read.call(localStorage, key))).toEqual([null, null]);
    expect(read.call(localStorage, 'unrelated.synthetic')).toBe('keep');
  });
  await import('../main');
  lifecycle = await import('../stores/sessionDraftStore');
  expect(mocks.render).toHaveBeenCalledTimes(1);
  for (const spy of [get, set, enumerate, clear]) expect(spy).not.toHaveBeenCalled();
});

it('continues_bootstrap_when_legacy_storage_is_unavailable', async () => {
  vi.resetModules();
  const getter = vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => { throw new Error('synthetic'); });
  mocks.createRoot.mockReturnValue({ render: mocks.render });
  mocks.render.mockReset();
  const log = vi.spyOn(console, 'log');
  const error = vi.spyOn(console, 'error');
  await import('../main');
  lifecycle = await import('../stores/sessionDraftStore');
  expect(getter).toHaveBeenCalledTimes(2);
  expect(mocks.render).toHaveBeenCalledTimes(1);
  expect(log).not.toHaveBeenCalled();
  expect(error).not.toHaveBeenCalled();
});
