import { isSafeBrowserId } from '../../contracts/browserRunProjection';
import { useAuthStore } from '../../stores/authStore';
import { isClientConversationId, type BrowserAccepted } from './api';

const STORAGE_KEY = 'eternalai.browser.accepted.v1';

export interface BrowserOwnerScope {
  /** Stable opaque /me cache aliases; these are never authorization identities. */
  readonly tenant_id: string;
  readonly user_id: string;
}

export interface StoredBrowserAccepted {
  readonly owner: BrowserOwnerScope;
  readonly conversation_id: string;
  readonly accepted: BrowserAccepted;
}

function plainRecord(value: unknown): Record<string, unknown> | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const prototype: unknown = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null
    ? value as Record<string, unknown> : null;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Reflect.ownKeys(value);
  return actual.length === keys.length && actual.every((key) =>
    typeof key === 'string' && keys.includes(key));
}

/** Cache aliases must come from authenticated /me, never a display label or storage. */
export function parseBrowserOwnerScope(me: unknown): BrowserOwnerScope | null {
  try {
    const response = plainRecord(me);
    const scope = plainRecord(response?.browser_owner_scope);
    if (scope === null || !exactKeys(scope, ['tenant_id', 'user_id'])
        || !isSafeBrowserId(scope.tenant_id) || !isSafeBrowserId(scope.user_id)) return null;
    return { tenant_id: scope.tenant_id, user_id: scope.user_id };
  } catch {
    return null;
  }
}

export function parseBrowserSkillId(me: unknown): string | null {
  try {
    const response = plainRecord(me);
    return isSafeBrowserId(response?.browser_skill_id) ? response.browser_skill_id : null;
  } catch {
    return null;
  }
}

export function clearBrowserAccepted(): void {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch { /* Storage may be unavailable; no in-memory authority is retained here. */ }
}

function parsedStored(value: unknown): StoredBrowserAccepted | null {
  const record = plainRecord(value);
  const owner = plainRecord(record?.owner);
  const accepted = plainRecord(record?.accepted);
  if (record === null || !exactKeys(record, ['owner', 'conversation_id', 'accepted'])
      || owner === null || !exactKeys(owner, ['tenant_id', 'user_id'])
      || !isSafeBrowserId(owner.tenant_id) || !isSafeBrowserId(owner.user_id)
      || !isClientConversationId(record.conversation_id)
      || accepted === null || !exactKeys(accepted, ['kind', 'task_id', 'run_id', 'state_revision'])
      || accepted.kind !== 'accepted' || !isSafeBrowserId(accepted.task_id)
      || !isSafeBrowserId(accepted.run_id) || typeof accepted.state_revision !== 'number'
      || !Number.isSafeInteger(accepted.state_revision) || accepted.state_revision < 0) return null;
  return {
    owner: { tenant_id: owner.tenant_id, user_id: owner.user_id },
    conversation_id: record.conversation_id,
    accepted: {
      kind: 'accepted', task_id: accepted.task_id, run_id: accepted.run_id,
      state_revision: accepted.state_revision,
    },
  };
}

export function loadBrowserAccepted(owner: BrowserOwnerScope): StoredBrowserAccepted | null {
  if (!isSafeBrowserId(owner.tenant_id) || !isSafeBrowserId(owner.user_id)
      || useAuthStore.getState().status !== 'authenticated') return null;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (raw === null) return null;
    const stored = parsedStored(JSON.parse(raw) as unknown);
    if (stored === null || stored.owner.tenant_id !== owner.tenant_id
        || stored.owner.user_id !== owner.user_id) {
      clearBrowserAccepted();
      return null;
    }
    return stored;
  } catch {
    clearBrowserAccepted();
    return null;
  }
}

export function saveBrowserAccepted(
  owner: BrowserOwnerScope, conversationId: string, accepted: BrowserAccepted,
): boolean {
  if (!isSafeBrowserId(owner.tenant_id) || !isSafeBrowserId(owner.user_id)
      || !isClientConversationId(conversationId) || !isSafeBrowserId(accepted.task_id)
      || !isSafeBrowserId(accepted.run_id)
      || accepted.kind !== 'accepted' || !Number.isSafeInteger(accepted.state_revision)
      || accepted.state_revision < 0
      || useAuthStore.getState().status !== 'authenticated') return false;
  try {
    const value: StoredBrowserAccepted = {
      owner: { tenant_id: owner.tenant_id, user_id: owner.user_id },
      conversation_id: conversationId,
      accepted: {
        kind: 'accepted', task_id: accepted.task_id, run_id: accepted.run_id,
        state_revision: accepted.state_revision,
      },
    };
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

// Authentication changes invalidate client recovery metadata synchronously.
useAuthStore.subscribe((state, previous) => {
  if (state.status === 'unauthenticated'
      || (previous.status === 'authenticated'
        && (state.status !== 'authenticated' || state.generation !== previous.generation))) {
    clearBrowserAccepted();
  }
});
