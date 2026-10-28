import { create } from 'zustand';
import { useAuthStore } from './authStore';
import type { AuthenticationStatus } from './authStore';

export const LEGACY_DISPATCH_DRAFT_KEY = 'eternalai.work-dispatch.draft';
export const LEGACY_SOFTWARE_DRAFT_KEY = 'eternalai.apps.new-software-draft';

export type DraftSessionToken = Readonly<{ generation: number; revision: number }>;

let revision = 0;
let disposed = false;
function currentToken(): DraftSessionToken | null {
  const { status, generation } = useAuthStore.getState();
  return status === 'authenticated' ? Object.freeze({ generation, revision }) : null;
}

const useSession = create<{ token: DraftSessionToken | null }>(() => ({ token: currentToken() }));
const invalidators = new Set<() => void>();

export type InvalidateReason = 'auth' | 'pagehide' | 'pageshow' | 'dispose';
type AuthSnapshot = Readonly<{ status: AuthenticationStatus; generation: number }>;
let beforeInvalidate: ((reason: InvalidateReason, previous: AuthSnapshot) => void) | null = null;

/** The single pre-clear hook: slots and the Dock are still intact when it runs. */
export function setBeforeInvalidate(
  hook: ((reason: InvalidateReason, previous: AuthSnapshot) => void) | null,
) {
  beforeInvalidate = hook;
}

function invalidate(reason: InvalidateReason, previous: AuthSnapshot = useAuthStore.getState()) {
  try {
    beforeInvalidate?.(reason, previous);
  } catch { /* Capture is best effort; invalidation must still happen. */ }
  revision += 1;
  // Clear snapshots before notifying React, including batched auth transitions.
  for (const clear of invalidators) clear();
  useSession.setState({ token: currentToken() });
}

const unsubscribeAuth = useAuthStore.subscribe((state, previous) => {
  if (state.status !== previous.status || state.generation !== previous.generation) {
    invalidate('auth', { status: previous.status, generation: previous.generation });
  }
});
const onPageHide = () => invalidate('pagehide');
const onPageShow = (event: PageTransitionEvent) => {
  if (event.persisted) invalidate('pageshow');
};
window.addEventListener('pagehide', onPageHide);
window.addEventListener('pageshow', onPageShow);

/** Also used by isolated module tests; disposed tokens can never become valid again. */
export function disposeDraftSession() {
  disposed = true;
  unsubscribeAuth();
  window.removeEventListener('pagehide', onPageHide);
  window.removeEventListener('pageshow', onPageShow);
  invalidate('dispose');
  invalidators.clear();
}
if (import.meta.hot) import.meta.hot.dispose(disposeDraftSession);

export function useDraftSession(): DraftSessionToken | null {
  return useSession((state) => state.token);
}

export function isCurrentDraftSession(token: DraftSessionToken | null): token is DraftSessionToken {
  const auth = useAuthStore.getState();
  return !disposed && token !== null && auth.status === 'authenticated'
    && token.generation === auth.generation && token.revision === revision;
}

/** Two feature-owned slots share lifecycle logic, never persistence or feature types. */
export function createSessionDraftStore<T>(copy: (value: T) => T) {
  let snapshot: T | null = null;
  const clearSnapshot = () => { snapshot = null; };
  invalidators.add(clearSnapshot);
  return {
    read(token: DraftSessionToken | null): T | null {
      if (!isCurrentDraftSession(token) || snapshot === null) return null;
      try {
        const result = copy(snapshot);
        return isCurrentDraftSession(token) ? result : null;
      } catch {
        return null;
      }
    },
    save(token: DraftSessionToken | null, value: T): boolean {
      if (!isCurrentDraftSession(token)) return false;
      try {
        const result = copy(value);
        if (!isCurrentDraftSession(token)) return false;
        snapshot = result;
        return true;
      } catch {
        return false;
      }
    },
    clear(token: DraftSessionToken | null): boolean {
      if (!isCurrentDraftSession(token)) return false;
      clearSnapshot();
      return true;
    },
    dispose() {
      clearSnapshot();
      invalidators.delete(clearSnapshot);
    },
  };
}

/** Never inspect unowned content; each exact removal is attempted independently. */
export function removeLegacyDrafts(): boolean {
  let removed = true;
  for (const key of [LEGACY_DISPATCH_DRAFT_KEY, LEGACY_SOFTWARE_DRAFT_KEY]) {
    try {
      window.localStorage.removeItem(key);
    } catch {
      removed = false;
    }
  }
  return removed;
}
