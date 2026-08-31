import { create } from 'zustand';
import {
  createGeneralPageContext,
  PageContextValidationError,
  parsePageContext,
} from '../contracts/pageContext';
import type { PageContextDeclaration } from '../contracts/pageContext';
import type { ProjectedResponse } from '../contracts/runtimeProjection';

export type AIDockMode = 'closed' | 'drawer' | 'pinned';
type OpenAIDockMode = Exclude<AIDockMode, 'closed'>;
type AIDockSessionContextMode = 'general' | 'page';

export type TranscriptEntry =
  | {
      role: 'user';
      text: string;
    }
  | ProjectedResponse;

interface AIDockState {
  contextNotice: string | null;
  draft: string;
  lastOpenMode: OpenAIDockMode;
  mode: AIDockMode;
  pageContextDeclaration: PageContextDeclaration | null;
  sessionContextMode: AIDockSessionContextMode;
  sessionId: string | null;
  transcript: TranscriptEntry[];
  appendTranscript: (entry: TranscriptEntry) => void;
  clearSession: () => void;
  clearPageContext: (expectedSurfaceId?: string) => void;
  closeDock: () => void;
  dismissContextNotice: () => void;
  ensureSession: () => string;
  openDock: () => void;
  registerPageContext: (candidate: unknown) => void;
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
  sessionContextMode: 'page',
  sessionId: null,
  transcript: [],
  appendTranscript: (entry) =>
    set((state) => ({ transcript: [...state.transcript, entry] })),
  clearSession: () =>
    set({
      draft: '',
      lastOpenMode: 'drawer',
      mode: 'closed',
      contextNotice: null,
      pageContextDeclaration: null,
      sessionContextMode: 'page',
      sessionId: null,
      transcript: [],
    }),
  clearPageContext: (expectedSurfaceId) =>
    set((state) => {
      const current = state.pageContextDeclaration;
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
      const previous = state.pageContextDeclaration;
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
          };
        }
        return { contextNotice: null, pageContextDeclaration: next };
      }
      if (authorityScopeChanged(previous, next)) {
        return {
          contextNotice: '权限或数据范围已切换；已默认开始新的临时对话。',
          draft: '',
          pageContextDeclaration: next,
          sessionId: null,
          transcript: [],
        };
      }
      if (pageBindingChanged(previous, next)) {
        return {
          contextNotice: '页面对象或筛选已切换；已默认开始新的临时对话。',
          draft: '',
          pageContextDeclaration: next,
          sessionId: null,
          transcript: [],
        };
      }
      return { pageContextDeclaration: next };
    });
  },
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
      sessionContextMode: 'general',
      sessionId: newSessionId(),
      transcript: [],
    })),
}));
