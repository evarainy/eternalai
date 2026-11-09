import { useAuthStore } from '../../stores/authStore';

/**
 * Same-tab recovery records (report job ids, unconfirmed sheet fill submissions) carry the
 * /me `restore_scope` (opaque tenant + user + roles alias, the same key as the re-login
 * snapshot) of the account that wrote them. They are read back only for the scope proven by
 * a /me of the current login; any other scope or an unreadable copy is removed at once.
 */

/** The account scope of a successful /me; null when the server keeps no restore scope. */
export function recoveryScopeOf(me: unknown): string | null {
  if (typeof me !== 'object' || me === null) return null;
  const scope = (me as Record<string, unknown>).restore_scope;
  return typeof scope === 'string' && scope.length > 0 ? scope : null;
}

export function clearRecovery(key: string): void {
  try {
    window.sessionStorage.removeItem(key);
  } catch { /* Storage may be unavailable; nothing is kept in memory here. */ }
}

export function loadRecovery(key: string, scope: string): unknown[] {
  if (useAuthStore.getState().status !== 'authenticated') return [];
  try {
    const raw = window.sessionStorage.getItem(key);
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
      const { scope: stored, items } = parsed as Record<string, unknown>;
      if (stored === scope && Array.isArray(items)) return items as unknown[];
    }
  } catch { /* Unreadable: removed below. */ }
  clearRecovery(key);
  return [];
}

/**
 * Rewrite this scope's records. A copy written under another account scope is left untouched:
 * a late callback of an earlier login must never remove the current account's records.
 */
export function updateRecovery(
  key: string, scope: string, update: (items: unknown[]) => readonly unknown[],
): void {
  if (useAuthStore.getState().status !== 'authenticated') return;
  let items: unknown[] = [];
  try {
    const raw = window.sessionStorage.getItem(key);
    if (raw !== null) {
      const parsed: unknown = JSON.parse(raw);
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        clearRecovery(key);
      } else {
        const { scope: stored, items: storedItems } = parsed as Record<string, unknown>;
        if (stored !== scope) return;
        if (Array.isArray(storedItems)) items = storedItems as unknown[];
      }
    }
  } catch {
    clearRecovery(key);
  }
  storeRecovery(key, scope, update(items));
}

export function storeRecovery(key: string, scope: string, items: readonly unknown[]): void {
  if (items.length === 0) {
    clearRecovery(key);
    return;
  }
  try {
    window.sessionStorage.setItem(key, JSON.stringify({ scope, items }));
  } catch { /* Storage may be unavailable; the card still works until this tab reloads. */ }
}
