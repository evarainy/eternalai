import type { QueryClient, QueryKey } from '@tanstack/react-query';
import { expect } from 'vitest';
import type { AuthenticationStatus } from '../stores/authStore';

interface AuthSnapshot { status: AuthenticationStatus; generation: number }
interface AuthSource {
  getState: () => AuthSnapshot;
  subscribe: (listener: (state: AuthSnapshot, previous: AuthSnapshot) => void) => () => void;
}

/** Start before the auth transition; never infer an unobserved generation's origin. */
export function trackAuthGenerations(source: AuthSource) {
  const initial = { ...source.getState() };
  const authenticated = new Set<number>();
  const unauthenticated = new Set<number>();
  const unknown = new Set<number>();
  const transitions: { before: AuthSnapshot; after: AuthSnapshot }[] = [];
  const snapshot = ({ status, generation }: AuthSnapshot): AuthSnapshot => ({ status, generation });
  const record = (state: AuthSnapshot) => {
    if (state.status === 'authenticated') authenticated.add(state.generation);
    else if (state.status === 'unauthenticated') unauthenticated.add(state.generation);
    else unknown.add(state.generation);
  };
  record(initial);
  const stop = source.subscribe((state, previous) => {
    transitions.push({ before: snapshot(previous), after: snapshot(state) });
    record(previous);
    record(state);
  });
  return { initial: snapshot(initial), transitions, authenticated, unauthenticated, unknown, stop };
}

export interface LogoutCacheCheckpoint {
  phase: 'unauthenticated' | 'identity-pending' | 'identity-ready';
  currentGeneration: number;
  generations: ReturnType<typeof trackAuthGenerations>;
}

interface CacheViolation {
  kind: 'data' | 'old-private-key' | 'unexpected-private-key' | 'enabled-identity-observer' | 'transition-fetch';
  key: QueryKey;
}

/** Inspect all entries, including empty/unobserved queries and arbitrary subpaths. */
export function logoutCacheViolations(client: QueryClient, checkpoint: LogoutCacheCheckpoint): CacheViolation[] {
  const violations: CacheViolation[] = [];
  for (const query of client.getQueryCache().getAll()) {
    const key = query.queryKey;
    const versioned = ['me', 'work-objects', 'credential-binding'].includes(String(key[0]));
    const unversioned = key[0] === 'private' || key[0] === 'admin';
    const generation = key[1];
    const history = checkpoint.generations;
    const latest = history.transitions.at(-1)?.after ?? history.initial;
    const currentIdentity = checkpoint.phase !== 'unauthenticated' &&
      latest.status === 'authenticated' && latest.generation === checkpoint.currentGeneration &&
      generation === checkpoint.currentGeneration;
    const transition = key.length === 2 && key[0] === 'me' && typeof generation === 'number' &&
      history.unauthenticated.has(generation) && !history.authenticated.has(generation) &&
      !history.unknown.has(generation);
    // Disabled observers are not write protection. Recheck all conditions at every checkpoint.
    const enabled = query.isActive();
    const exempt = transition && query.state.data === undefined && !enabled && query.state.fetchStatus === 'idle';
    if ((checkpoint.phase !== 'identity-ready' || transition) && query.state.data !== undefined) {
      violations.push({ kind: 'data', key });
    }
    if (unversioned || (versioned && !currentIdentity && !exempt)) {
      violations.push({ kind: 'old-private-key', key });
    }
    if (transition && enabled) violations.push({ kind: 'enabled-identity-observer', key });
    if (transition && query.state.fetchStatus !== 'idle') violations.push({ kind: 'transition-fetch', key });
    if (checkpoint.phase === 'unauthenticated' && (versioned || unversioned)) {
      if (!exempt) {
        violations.push({ kind: 'unexpected-private-key', key });
      }
    }
  }
  return violations;
}

/** Ready-phase callers additionally check each populated entry against the new identity's fixture. */
export function assertLogoutCache(client: QueryClient, checkpoint: LogoutCacheCheckpoint): void {
  for (const violation of logoutCacheViolations(client, checkpoint)) {
    // Separate failures preserve both data and private-key evidence in production mutations.
    expect.soft(violation, `${checkpoint.phase}: ${violation.kind} in ${JSON.stringify(violation.key)}`).toBeUndefined();
  }
}
