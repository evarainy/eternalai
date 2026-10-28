import { create } from 'zustand';
import {
  createGeneralPageContext,
  PageContextValidationError,
  parsePageContext,
} from '../contracts/pageContext';
import type { PageContextDeclaration } from '../contracts/pageContext';
import type { UserActionOutcome } from '../contracts/userActionOutcome';
import type { ProjectedResponse } from '../contracts/runtimeProjection';

export type AIDockMode = 'closed' | 'drawer' | 'pinned';
type OpenAIDockMode = Exclude<AIDockMode, 'closed'>;
type AIDockSessionContextMode = 'general' | 'page';

/** A conversation brought back by a same-account re-login (see app/reauthRestore.ts). */
export interface RestoredDockSession {
  draft: string;
  sessionId: string | null;
  sessionContextMode: AIDockSessionContextMode;
  transcript: TranscriptEntry[];
  confirmationResults: Record<string, UserActionOutcome>;
  /** The page context the conversation was bound to when it was captured. */
  pageContext: PageContextDeclaration | null;
}

export type TranscriptEntry =
  | {
      role: 'user';
      text: string;
      /** Local-only submission id, so a 401 can withdraw exactly this echo. */
      requestId?: string;
    }
  | ProjectedResponse;

interface AIDockState {
  contextNotice: string | null;
  draft: string;
  lastOpenMode: OpenAIDockMode;
  mode: AIDockMode;
  pageContextDeclaration: PageContextDeclaration | null;
  /** Context of a restored conversation, held until the page declares its own context again. */
  restoredPageContext: PageContextDeclaration | null;
  sessionContextMode: AIDockSessionContextMode;
  sessionId: string | null;
  transcript: TranscriptEntry[];
  confirmationResults: Record<string, UserActionOutcome>;
  applyConfirmationResult: (sessionId: string, responseId: string | null, result: ProjectedResponse) => void;
  appendTranscript: (entry: TranscriptEntry) => void;
  clearSession: () => void;
  clearPageContext: (expectedSurfaceId?: string) => void;
  closeDock: () => void;
  dismissContextNotice: () => void;
  ensureSession: () => string;
  openDock: () => void;
  registerPageContext: (candidate: unknown) => void;
  restoreSession: (restored: RestoredDockSession) => void;
  setDraft: (draft: string) => void;
  setMode: (mode: AIDockMode) => void;
  startNewSession: () => void;
}

function newSessionId(): string {
  return crypto.randomUUID();
}

function authorityScopeChanged(
  previous: PageContextDeclaration,
  next: PageContextDeclaration,
): boolean {
  return (
    previous.visibility !== next.visibility ||
    previous.organization_scope?.tenant_id !== next.organization_scope?.tenant_id ||
    previous.organization_scope?.organization_id !==
      next.organization_scope?.organization_id ||
    previous.organization_scope?.department_id !==
      next.organization_scope?.department_id
  );
}

function pageBindingChanged(
  previous: PageContextDeclaration,
  next: PageContextDeclaration,
): boolean {
  return (
    previous.surface_id !== next.surface_id ||
    previous.selected_metric !== next.selected_metric ||
    JSON.stringify(previous.work_object_refs) !==
      JSON.stringify(next.work_object_refs) ||
    JSON.stringify(previous.source_refs) !== JSON.stringify(next.source_refs) ||
    JSON.stringify(previous.filters) !== JSON.stringify(next.filters) ||
    JSON.stringify(previous.allowed_capabilities) !==
      JSON.stringify(next.allowed_capabilities)
  );
}

export const useAIDockStore = create<AIDockState>((set, get) => ({
  contextNotice: null,
  draft: '',
  lastOpenMode: 'drawer',
  mode: 'closed',
  pageContextDeclaration: null,
  restoredPageContext: null,
  sessionContextMode: 'page',
  sessionId: null,
  transcript: [],
  confirmationResults: {},
  applyConfirmationResult: (sessionId, responseId, result) =>
    set((state) => {
      if (state.sessionId !== sessionId) return state;
      const outcome = result.actionOutcome;
      const terminal = outcome !== null && outcome !== 'action_gate_unavailable';
      const known = responseId !== null && state.transcript.some(
        (entry) => entry.role === 'assistant' && entry.responseId === responseId && entry.confirm !== null,
      );
      return {
        transcript: [...state.transcript, result],
        confirmationResults: terminal && known
          ? { ...state.confirmationResults, [responseId]: outcome }
          : state.confirmationResults,
      };
    }),
  appendTranscript: (entry) =>
    set((state) => ({ transcript: [...state.transcript, entry] })),
  clearSession: () =>
    set({
      draft: '',
      lastOpenMode: 'drawer',
      mode: 'closed',
      contextNotice: null,
      pageContextDeclaration: null,
      restoredPageContext: null,
      sessionContextMode: 'page',
      sessionId: null,
      transcript: [],
      confirmationResults: {},
    }),
  clearPageContext: (expectedSurfaceId) =>
    set((state) => {
      const current = state.pageContextDeclaration;
      if (current === null && state.restoredPageContext !== null && (expectedSurfaceId === undefined
          || state.restoredPageContext.surface_id === expectedSurfaceId)) {
        // Left before the page declared again: the restored binding no longer applies.
        return { restoredPageContext: null };
      }
      if (current === null) {
        return expectedSurfaceId === undefined && state.sessionContextMode === 'general'
          ? { sessionContextMode: 'page' }
          : state;
      }
      if (
        expectedSurfaceId !== undefined &&
        current.surface_id !== expectedSurfaceId
      ) {
        return state;
      }
      return {
        pageContextDeclaration: null,
        contextNotice: '页面上下文已移除；AI 不会继续读取离开的页面。',
        sessionContextMode: 'page',
      };
    }),
  closeDock: () => set({ mode: 'closed' }),
  dismissContextNotice: () => set({ contextNotice: null }),
  ensureSession: () => {
    const existing = get().sessionId;
    if (existing !== null) {
      return existing;
    }
    const sessionId = newSessionId();
    set({ sessionId });
    return sessionId;
  },
  openDock: () => set((state) => ({ mode: state.lastOpenMode })),
  registerPageContext: (candidate) => {
    let parsed: PageContextDeclaration;
    try {
      parsed = parsePageContext(candidate);
    } catch (error) {
      if (!(error instanceof PageContextValidationError)) {
        throw error;
      }
      set({
        contextNotice: '当前页面上下文不可用；AI 不会读取本页数据。',
        pageContextDeclaration: null,
      });
      return;
    }
    set((state) => {
      const next =
        state.sessionContextMode === 'general'
          ? createGeneralPageContext(parsed)
          : parsed;
      // A restored conversation is checked against the context it was captured under, with the
      // same isolation rules as a live one; only an unchanged context keeps it.
      const previous = state.pageContextDeclaration ?? state.restoredPageContext;
      if (previous === null) {
        if (
          state.sessionId !== null ||
          state.transcript.length > 0 ||
          state.draft.length > 0
        ) {
          return {
            contextNotice: '页面上下文已重新建立；已默认开始新的临时对话。',
            draft: '',
            pageContextDeclaration: next,
            sessionId: null,
            transcript: [],
            confirmationResults: {},
          };
        }
        return { contextNotice: null, pageContextDeclaration: next };
      }
      if (authorityScopeChanged(previous, next)) {
        return {
          contextNotice: '权限或数据范围已切换；已默认开始新的临时对话。',
          draft: '',
          pageContextDeclaration: next,
          restoredPageContext: null,
          sessionId: null,
          transcript: [],
          confirmationResults: {},
        };
      }
      if (pageBindingChanged(previous, next)) {
        return {
          contextNotice: '页面对象或筛选已切换；已默认开始新的临时对话。',
          draft: '',
          pageContextDeclaration: next,
          restoredPageContext: null,
          sessionId: null,
          transcript: [],
          confirmationResults: {},
        };
      }
      return { pageContextDeclaration: next, restoredPageContext: null };
    });
  },
  restoreSession: (restored) =>
    set((state) => {
      const draft = state.draft.length === 0 ? { draft: restored.draft } : {};
      if (state.sessionId !== null) return draft;
      const current = state.pageContextDeclaration;
      if (current !== null && (restored.pageContext === null
          || authorityScopeChanged(restored.pageContext, current)
          || pageBindingChanged(restored.pageContext, current))) {
        // The page already declared a different context: same outcome as a live context switch.
        return { contextNotice: '页面上下文已重新建立；已默认开始新的临时对话。' };
      }
      return {
        ...draft,
        sessionId: restored.sessionId,
        sessionContextMode: restored.sessionContextMode,
        transcript: restored.transcript,
        confirmationResults: restored.confirmationResults,
        restoredPageContext: current === null ? restored.pageContext : null,
      };
    }),
  setDraft: (draft) => set({ draft }),
  setMode: (mode) =>
    set((state) => ({
      lastOpenMode: mode === 'closed' ? state.lastOpenMode : mode,
      mode,
    })),
  startNewSession: () =>
    set((state) => ({
      contextNotice: '已新建通用会话；当前上下文不带工作事项引用。',
      draft: '',
      pageContextDeclaration:
        state.pageContextDeclaration === null
          ? null
          : createGeneralPageContext(state.pageContextDeclaration),
      restoredPageContext: null,
      sessionContextMode: 'general',
      sessionId: newSessionId(),
      transcript: [],
      confirmationResults: {},
    })),
}));
