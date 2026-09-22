import { QueryClient, QueryObserver } from '@tanstack/react-query';
import type { QueryKey } from '@tanstack/react-query';
import { afterEach, describe, expect, it } from 'vitest';
import { createStore } from 'zustand/vanilla';
import type { AuthenticationStatus } from '../stores/authStore';
import { logoutCacheViolations, trackAuthGenerations } from './logoutCache';

const checkpoints = [
  { name: 'cleanup', phase: 'unauthenticated', currentGeneration: 41 },
  { name: 'pending', phase: 'identity-pending', currentGeneration: 42 },
  { name: 'ready', phase: 'identity-ready', currentGeneration: 42 },
  { name: 'late-settled', phase: 'identity-ready', currentGeneration: 42 },
] as const;
const disposals: (() => void)[] = [];
afterEach(() => { for (const dispose of disposals.splice(0).reverse()) dispose(); });
function fixture(checkpoint: typeof checkpoints[number]) {
  const store = createStore(() => ({ status: 'authenticated' as AuthenticationStatus, generation: 40 }));
  const generations = trackAuthGenerations(store);
  disposals.push(generations.stop);
  store.setState({ status: 'unauthenticated', generation: 41 });
  if (checkpoint.currentGeneration === 42) store.setState({ status: 'authenticated', generation: 42 });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  disposals.push(() => client.clear());
  return { client, store, generations, inspect: () => logoutCacheViolations(client, { ...checkpoint, generations }) };
}
function emptyEntry(client: QueryClient, key: QueryKey) {
  const query = client.getQueryCache().build(client, { queryKey: key });
  expect(client.getQueryCache().getAll()).toContain(query);
  expect(query.state.data).toBeUndefined();
  return query;
}
function observe(client: QueryClient, key: QueryKey, enabled: boolean | (() => boolean)) {
  const observer = new QueryObserver(client, {
    queryKey: key, enabled, queryFn: () => new Promise<unknown>(() => undefined),
  });
  const unsubscribe = observer.subscribe(() => undefined);
  disposals.push(() => { unsubscribe(); observer.destroy(); });
  return observer;
}

describe('logout cache predicate', () => {
  it('records initial and both sides of synchronous transitions without guessing generations', () => {
    const { store, generations } = fixture(checkpoints[0]);
    expect(generations.initial).toEqual({ status: 'authenticated', generation: 40 });
    expect(generations.transitions).toEqual([{ before: generations.initial,
      after: { status: 'unauthenticated', generation: 41 } }]);
    store.setState({ status: 'authenticated', generation: 80 });
    store.setState({ status: 'unauthenticated', generation: 120 });
    expect(generations.authenticated).toEqual(new Set([40, 80]));
    expect(generations.unauthenticated).toEqual(new Set([41, 120]));
    expect(generations.unknown).toEqual(new Set());
  });

  describe.each(checkpoints)('$name', (checkpoint) => {
    it.each(['none', 'disabled', 'disabled-function'] as const)('allows empty transition with %s observers', (mode) => {
      const { client, inspect } = fixture(checkpoint);
      const query = emptyEntry(client, ['me', 41]);
      if (mode !== 'none') observe(client, query.queryKey, mode === 'disabled' ? false : () => false);
      expect(query.isActive()).toBe(false);
      expect(query.state.fetchStatus).toBe('idle');
      expect(inspect()).toEqual([]);
    });

    it.each([null, false, 0, '', {}, { owner: 'A' }])('rejects transition data %j', (data) => {
      const { client, inspect } = fixture(checkpoint);
      observe(client, ['me', 41], false);
      expect(inspect()).toEqual([]);
      client.setQueryData(['me', 41], data);
      expect(client.getQueryData(['me', 41])).toEqual(data);
      expect(inspect()).toContainEqual({ kind: 'data', key: ['me', 41] });
    });

    it.each(['enabled', 'enabled-function'] as const)('rejects %s transition observers even while idle', (mode) => {
      const { client, inspect } = fixture(checkpoint);
      const query = emptyEntry(client, ['me', 41]);
      const observer = observe(client, query.queryKey, false);
      observer.setOptions({ ...observer.options, enabled: mode === 'enabled' ? true : () => true });
      query.setState({ fetchStatus: 'idle' });
      expect(query.isActive()).toBe(true);
      expect(inspect()).toContainEqual({ kind: 'enabled-identity-observer', key: ['me', 41] });
    });

    it.each(['fetching', 'paused'] as const)('rejects %s transition with no observers', (fetchStatus) => {
      const { client, inspect } = fixture(checkpoint);
      const query = emptyEntry(client, ['me', 41]);
      query.setState({ fetchStatus });
      expect(query.getObserversCount()).toBe(0);
      expect(inspect()).toContainEqual({ kind: 'transition-fetch', key: ['me', 41] });
    });

    it.each([
      ['me', ['me', 40]],
      ['me-extra', ['me', 40, 'extra']],
      ['work-objects-root', ['work-objects', 40]],
      ['work-objects-list', ['work-objects', 40, 'list', 'unlisted-view']],
      ['work-objects-detail', ['work-objects', 40, 'detail', 'unlisted-id']],
      ['work-objects-search', ['work-objects', 40, 'search', 'all', 'unlisted-term']],
      ['work-objects-future', ['work-objects', 40, 'unlisted-subpath']],
      ['credential-binding', ['credential-binding', 40, 'unlisted-binding']],
      ['private', ['private', 'unlisted-private']],
      ['admin', ['admin', 'unlisted-admin']],
    ] satisfies [string, QueryKey][])('rejects extra empty %s independently', (_name, key) => {
      const { client, inspect } = fixture(checkpoint);
      emptyEntry(client, key);
      expect(client.getQueryCache().getAll()).toHaveLength(1);
      expect(inspect()).toContainEqual({ kind: 'old-private-key', key });
    });

    it.each([
      ['me', 41, 'extra'], ['work-objects', 41, 'search', 'all', 'term'],
      ['credential-binding', 41, 'oa'], ['private', 41], ['admin', 41],
    ])('rejects non-exempt transition key %j', (...key) => {
      const { client, inspect } = fixture(checkpoint);
      emptyEntry(client, key);
      expect(inspect()).toContainEqual({
        kind: checkpoint.phase === 'unauthenticated' ? 'unexpected-private-key' : 'old-private-key', key,
      });
    });

    it.each(['unknown', 'unobserved', 'previously-authenticated'] as const)('does not exempt %s provenance', (origin) => {
      const { client, store, generations, inspect } = fixture(checkpoint);
      const generation = origin === 'unobserved' ? 39 : 41;
      if (origin !== 'unobserved') {
        store.setState({ generation, status: origin === 'unknown' ? 'unknown' : 'authenticated' });
        store.setState({ generation, status: 'unauthenticated' });
        if (checkpoint.currentGeneration === 42) store.setState({ generation: 42, status: 'authenticated' });
      }
      emptyEntry(client, ['me', generation]);
      expect(generations.unknown.has(generation)).toBe(origin === 'unknown');
      expect(inspect()).toContainEqual({ kind: checkpoint.phase === 'unauthenticated' && generation === 41
        ? 'unexpected-private-key' : 'old-private-key', key: ['me', generation] });
    });
  });

  it.each(checkpoints.filter((checkpoint) => checkpoint.phase !== 'unauthenticated'))(
    'allows current authenticated query in $name', (checkpoint) => {
      const { client, inspect } = fixture(checkpoint);
      observe(client, ['me', 42], true);
      if (checkpoint.phase === 'identity-ready') client.setQueryData(['me', 42], { owner: 'B' });
      expect(client.getQueryCache().find({ queryKey: ['me', 42] })?.isActive()).toBe(true);
      expect(inspect()).toEqual([]);
      if (checkpoint.phase === 'identity-ready') expect(client.getQueryData(['me', 42])).toEqual({ owner: 'B' });
    },
  );

  it.each(['unknown', 'unobserved'] as const)('does not trust a current generation number with %s origin', (origin) => {
    const { client, store, generations } = fixture(checkpoints[0]);
    store.setState({ generation: origin === 'unknown' ? 42 : 80, status: 'unknown' });
    emptyEntry(client, ['me', 42]);
    expect(logoutCacheViolations(client, { ...checkpoints[2], generations }))
      .toContainEqual({ kind: 'old-private-key', key: ['me', 42] });
  });

  describe.each(checkpoints.slice(0, 2))('global data barrier $name', (checkpoint) => {
    it.each([null, false, 0, '', {}, { owner: 'A' }])('rejects unknown-prefix data %j', (data) => {
      const { client, inspect } = fixture(checkpoint);
      client.setQueryData(['unlisted-prefix'], data);
      expect(inspect()).toContainEqual({ kind: 'data', key: ['unlisted-prefix'] });
    });
  });

  it.each(['fetchQuery', 'refetch'] as const)('rechecks transition after %s writes despite a disabled observer', async (method) => {
    const { client, inspect } = fixture(checkpoints[2]);
    const observer = observe(client, ['me', 41], false);
    expect(inspect()).toEqual([]);
    const queryFn = async () => ({ owner: 'A' });
    if (method === 'fetchQuery') await client.fetchQuery({ queryKey: ['me', 41], queryFn });
    else {
      observer.setOptions({ ...observer.options, queryFn });
      await observer.refetch();
    }
    expect(observer.options.enabled).toBe(false);
    expect(inspect()).toContainEqual({ kind: 'data', key: ['me', 41] });
  });
});
