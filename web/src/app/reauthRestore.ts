import { useEffect, useRef } from 'react';
import { create } from 'zustand';
import { getReturnPath } from '../pages/loginNavigation';
import { useAIDockStore } from '../stores/aiDockStore';
import type { AIDockMode, TranscriptEntry } from '../stores/aiDockStore';
import { parsePageContext } from '../contracts/pageContext';
import type { PageContextDeclaration } from '../contracts/pageContext';
import { useAuthStore } from '../stores/authStore';
import { setBeforeInvalidate } from '../stores/sessionDraftStore';
import type { InvalidateReason } from '../stores/sessionDraftStore';
import type { UserActionOutcome } from '../contracts/userActionOutcome';
import { useIdentityQuery } from './identity';

/**
 * One-shot restore copy for a re-login in this tab (D1: 401 keeps unsent input and the page).
 *
 * The copy lives only in this tab's sessionStorage, is captured just before the draft slots are
 * invalidated, stays sealed until a fresh /me of the *current* generation proves the same
 * account scope, and is deleted on first consumption, mismatch, logout or principal change.
 * It never holds passwords, cookies, tickets or login form content, and nothing is resent.
 */

export const REAUTH_STORAGE_KEY = 'eternalai.reauth.v1';

export const REAUTH_NOTICES = {
  sealed: '登录已失效，请重新登录。重新登录同一账号后，将回到刚才的页面并恢复未发送的内容。',
  expired: '登录已失效，请重新登录。',
  principalChanged: '您的账号权限已变更，请重新登录。为安全起见，未发送的内容不会恢复。',
} as const;

type OpenDockMode = Exclude<AIDockMode, 'closed'>;

interface DockSnapshot {
  draft: string;
  pageContext?: PageContextDeclaration | null;
  mode: AIDockMode;
  lastOpenMode: OpenDockMode;
  sessionContextMode: 'general' | 'page';
  sessionId: string | null;
  transcript: TranscriptEntry[];
  confirmationResults: Record<string, UserActionOutcome>;
}

interface ReauthSnapshot {
  v: 1;
  scope: string;
  generation: number;
  path: string;
  dock: DockSnapshot;
  /** §3.7 unsaved page forms, already normalized by each form's own parser. */
  forms?: Record<string, unknown>;
  returned: string[];
}

export interface UnsentIdentity {
  readonly generation: number;
  readonly sessionId: string;
  readonly requestId: string;
}

interface FormReader {
  read: () => unknown;
  isPristine: () => boolean;
}

let knownScope: { generation: number; scope: string } | null = null;
const formReaders = new Map<string, FormReader>();
// Restored forms wait here until their component is mounted and still untouched.
const usePendingForms = create<{ forms: Record<string, unknown> }>(() => ({ forms: {} }));
// Why the session ended in this tab; null until it ends, 'logged_out' for a deliberate logout.
let notice: 'expired' | 'principal_changed' | 'logged_out' | null = null;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readStored(): ReauthSnapshot | null {
  try {
    const raw = window.sessionStorage.getItem(REAUTH_STORAGE_KEY);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || parsed.v !== 1 || typeof parsed.scope !== 'string'
        || typeof parsed.generation !== 'number' || typeof parsed.path !== 'string'
        || !Array.isArray(parsed.returned) || !isRecord(parsed.dock)) return null;
    const dock = parsed.dock;
    if (typeof dock.draft !== 'string' || !Array.isArray(dock.transcript)
        || !(dock.sessionId === null || typeof dock.sessionId === 'string')
        || !isRecord(dock.confirmationResults)
        || !(parsed.forms === undefined || isRecord(parsed.forms))) return null;
    return parsed as unknown as ReauthSnapshot;
  } catch {
    return null;
  }
}

function writeStored(snapshot: ReauthSnapshot): void {
  try {
    window.sessionStorage.setItem(REAUTH_STORAGE_KEY, JSON.stringify(snapshot));
  } catch {
    removeStored();
  }
}

function removeStored(): void {
  try {
    window.sessionStorage.removeItem(REAUTH_STORAGE_KEY);
  } catch { /* Storage unavailable: nothing was kept either. */ }
}

function captureForReauth(
  reason: InvalidateReason,
  previous: { status: string; generation: number },
): void {
  const next = useAuthStore.getState();
  if (reason === 'pageshow') {
    // Restored from the back/forward cache with live state: the sealed copy is not needed.
    removeStored();
    return;
  }
  if (reason === 'auth' && next.status === 'authenticated') {
    notice = null;
    return;
  }
  if (previous.status !== 'authenticated' || (reason !== 'auth' && reason !== 'pagehide')) return;
  if (reason === 'auth' && notice === null) notice = 'expired';
  const forms = captureForms();
  if (reason === 'auth') usePendingForms.setState({ forms: {} });
  if (knownScope === null || knownScope.generation !== previous.generation) return;
  const dock = useAIDockStore.getState();
  writeStored({
    v: 1,
    scope: knownScope.scope,
    generation: previous.generation,
    path: getReturnPath({ from: `${window.location.pathname}${window.location.search}` }),
    dock: {
      draft: dock.draft,
      pageContext: dock.pageContextDeclaration ?? dock.restoredPageContext,
      mode: dock.mode,
      lastOpenMode: dock.lastOpenMode,
      sessionContextMode: dock.sessionContextMode,
      sessionId: dock.sessionId,
      transcript: dock.transcript,
      confirmationResults: dock.confirmationResults,
    },
    forms,
    returned: [],
  });
}

function captureForms(): Record<string, unknown> {
  const forms = { ...usePendingForms.getState().forms };
  for (const [name, reader] of formReaders) {
    if (!reader.isPristine()) forms[name] = reader.read();
  }
  return forms;
}

setBeforeInvalidate(captureForReauth);
if (import.meta.hot) import.meta.hot.dispose(() => setBeforeInvalidate(null));

/** Logout and principal change: forget the copy before the state transition can capture it. */
export function discardReauthSnapshot(reason: 'logged_out' | 'principal_changed'): void {
  knownScope = null;
  notice = reason;
  usePendingForms.setState({ forms: {} });
  removeStored();
}

export function hasReauthSnapshot(): boolean {
  return readStored() !== null;
}

export function peekReauthPath(): string | undefined {
  return readStored()?.path;
}

export function reauthLoginNotice(): string | null {
  if (notice === 'principal_changed') return REAUTH_NOTICES.principalChanged;
  if (hasReauthSnapshot()) return REAUTH_NOTICES.sealed;
  return notice === 'expired' ? REAUTH_NOTICES.expired : null;
}

/**
 * A 401 for a sent message puts it back, but only into the copy captured for that submission.
 * Idempotent; any other state (consumed, discarded, another account or generation) is untouched.
 */
export function returnUnsentMessage(identity: UnsentIdentity, message: string): void {
  const snapshot = readStored();
  if (snapshot === null || snapshot.generation !== identity.generation
      || snapshot.dock.sessionId !== identity.sessionId
      || snapshot.returned.includes(identity.requestId)) return;
  const index = snapshot.dock.transcript.findIndex((entry) =>
    entry.role === 'user' && entry.requestId === identity.requestId);
  if (index === -1) return;
  const draft = snapshot.dock.draft;
  writeStored({
    ...snapshot,
    dock: {
      ...snapshot.dock,
      draft: draft.length > 0 ? `${message}\n${draft}` : message,
      transcript: snapshot.dock.transcript.filter((_, position) => position !== index),
    },
    returned: [...snapshot.returned, identity.requestId],
  });
}

function parsedPageContext(value: unknown): PageContextDeclaration | null {
  if (value === null || value === undefined) return null;
  try {
    return parsePageContext(value);
  } catch {
    // An unreadable binding never matches a page, so the page's own rule starts a new chat.
    return null;
  }
}

function consumeSnapshot(scope: string | null, generation: number): void {
  const snapshot = readStored();
  removeStored();
  knownScope = scope === null ? null : { generation, scope };
  // Generations restart on a full reload, so only the account scope decides here.
  if (snapshot === null || scope === null || snapshot.scope !== scope) return;
  const current = useAIDockStore.getState();
  current.restoreSession({
    draft: snapshot.dock.draft,
    sessionId: snapshot.dock.sessionId,
    sessionContextMode: snapshot.dock.sessionContextMode === 'general' ? 'general' : 'page',
    transcript: snapshot.dock.transcript,
    confirmationResults: snapshot.dock.confirmationResults,
    pageContext: parsedPageContext(snapshot.dock.pageContext),
  });
  useAIDockStore.setState({
    ...(current.mode === 'closed' && snapshot.dock.mode !== 'closed' ? {
      mode: snapshot.dock.mode === 'pinned' ? 'pinned' : 'drawer',
      lastOpenMode: snapshot.dock.lastOpenMode === 'pinned' ? 'pinned' : 'drawer',
    } : {}),
  });
  usePendingForms.setState({ forms: snapshot.forms ?? {} });
}

/** Unseal only after a fresh /me of the current generation; every outcome consumes the copy. */
export function useReauthRestore(): void {
  const status = useAuthStore((state) => state.status);
  const generation = useAuthStore((state) => state.generation);
  const { data } = useIdentityQuery();

  useEffect(() => {
    const auth = useAuthStore.getState();
    if (status !== 'authenticated' || data === undefined
        || auth.status !== 'authenticated' || auth.generation !== generation) return;
    const scope = typeof data.restore_scope === 'string' && data.restore_scope.length > 0
      ? data.restore_scope : null;
    consumeSnapshot(scope, generation);
  }, [status, generation, data]);
}

interface ReauthForm<T> {
  /** False keeps the form out of capture and restore (e.g. series or prefilled composers). */
  enabled?: boolean;
  read: () => T;
  /** The form's own normalizer; it drops anything outside the draft type. */
  parse: (raw: unknown) => T;
  apply: (value: T) => void;
  isPristine: () => boolean;
}

/**
 * Let an unsaved page form survive a re-login of the same account. A restored value is applied
 * once, only while the form is still untouched; it is never saved to a slot or submitted.
 */
export function useReauthForm<T>(name: string, form: ReauthForm<T>): void {
  const latest = useRef(form);
  const enabled = form.enabled ?? true;
  useEffect(() => { latest.current = form; });
  useEffect(() => {
    if (!enabled) return undefined;
    const reader: FormReader = {
      read: () => latest.current.parse(latest.current.read()),
      isPristine: () => latest.current.isPristine(),
    };
    formReaders.set(name, reader);
    return () => {
      if (formReaders.get(name) === reader) formReaders.delete(name);
    };
  }, [name, enabled]);
  const pending = usePendingForms((state) => (enabled ? state.forms[name] : undefined));
  useEffect(() => {
    if (pending === undefined || !latest.current.isPristine()) return;
    usePendingForms.setState((state) => ({
      forms: Object.fromEntries(Object.entries(state.forms).filter(([key]) => key !== name)),
    }));
    latest.current.apply(latest.current.parse(pending));
  }, [name, pending]);
}
