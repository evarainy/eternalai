import { create } from 'zustand';
import type { SourceResponse } from '../generated/im-context/im-context.schemas';
import {
  createGeneralPageContext,
  PageContextValidationError,
  parsePageContext,
} from '../contracts/pageContext';
import type { PageContextDeclaration } from '../contracts/pageContext';
import type { UserActionOutcome } from '../contracts/userActionOutcome';
import { projectTextResponse } from '../contracts/runtimeProjection';
import type { DFormCollectionReference, ProjectedResponse } from '../contracts/runtimeProjection';

export type AIDockMode = 'closed' | 'drawer' | 'pinned';
type OpenAIDockMode = Exclude<AIDockMode, 'closed'>;
type AIDockSessionContextMode = 'general' | 'page';

/**
 * The reply the last D collection card waits for. Only a reference: the backend re-checks
 * owner, session, version and expiry on every continuation. ``authGeneration`` is the auth
 * generation it is valid under; a restored reference is bound to the generation that restored it.
 */
export interface PendingCollection {
  reference: DFormCollectionReference;
  /** Client conversation id the card belongs to. */
  sessionId: string;
  /** Backend session id that issued the card; a continuation must come back on it. */
  serverSessionId: string;
  ownerKey: string | null;
  authGeneration: number;
}

/** Fixed notice when a restored conversation's pending collection can no longer continue. */
export const COLLECTION_LOST_NOTICE = '原追问已失效，请重新发起报修。';

/**
 * A sealed chat upload of this conversation. Only a reference: the backend re-checks owner,
 * session, state and expiry whenever a message names it. ``sheetNames`` are display only.
 */
export interface ChatAttachment {
  readonly artifact_id: string;
  readonly filename: string;
  readonly sheetNames: readonly string[];
}

/** A conversation brought back by a same-account re-login (see app/reauthRestore.ts). */
export interface RestoredDockSession {
  draft: string;
  sessionId: string | null;
  sessionContextMode: AIDockSessionContextMode;
  transcript: TranscriptEntry[];
  confirmationResults: Record<string, UserActionOutcome>;
  /** The page context the conversation was bound to when it was captured. */
  pageContext: PageContextDeclaration | null;
  /** Pending collection, already bound to the restoring generation; applied only with the rest. */
  collection: PendingCollection | null;
  /** The transcript ends on a collection card whose reference was not kept (older copy). */
  collectionLost: boolean;
  /** Finished uploads waiting to be sent; they belong to the restored conversation only. */
  draftAttachments?: ChatAttachment[];
}

export type TranscriptEntry =
  | {
      role: 'user';
      text: string;
      /** Local-only submission id, so a 401 can withdraw exactly this echo. */
      requestId?: string;
      /** Attachments frozen with this message, shown as download links. */
      attachments?: readonly ChatAttachment[];
    }
  | ProjectedResponse;

interface AIDockState {
  imSource: { source: SourceResponse; authGeneration: number; sessionId: string } | null;
  setIMSource: (source: AIDockState['imSource']) => void;
  contextNotice: string | null;
  draft: string;
  /** Finished uploads of the current conversation that the next message will carry. */
  draftAttachments: ChatAttachment[];
  lastOpenMode: OpenAIDockMode;
  mode: AIDockMode;
  pageContextDeclaration: PageContextDeclaration | null;
  /** Context of a restored conversation, held until the page declares its own context again. */
  restoredPageContext: PageContextDeclaration | null;
  sessionContextMode: AIDockSessionContextMode;
  sessionId: string | null;
  transcript: TranscriptEntry[];
  confirmationResults: Record<string, UserActionOutcome>;
  collection: PendingCollection | null;
  applyConfirmationResult: (sessionId: string, responseId: string | null, result: ProjectedResponse) => void;
  appendTranscript: (entry: TranscriptEntry) => void;
  /** Settles a sheet fill plan card of the current transcript: accepted job or cancelled. */
  settleSheetFill: (responseId: string, outcome: { readonly jobId: string } | 'cancelled') => void;
  clearSession: () => void;
  clearPageContext: (expectedSurfaceId?: string) => void;
  closeDock: () => void;
  dismissContextNotice: () => void;
  ensureSession: () => string;
  openDock: () => void;
  registerPageContext: (candidate: unknown) => void;
  restoreSession: (restored: RestoredDockSession) => void;
  setCollection: (collection: PendingCollection | null) => void;
  setDraft: (draft: string) => void;
  setDraftAttachments: (attachments: ChatAttachment[]) => void;
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
  imSource: null,
  setIMSource: (imSource) => set({ imSource }),
  contextNotice: null,
  draft: '',
  draftAttachments: [],
  lastOpenMode: 'drawer',
  mode: 'closed',
  pageContextDeclaration: null,
  restoredPageContext: null,
  sessionContextMode: 'page',
  sessionId: null,
  transcript: [],
  confirmationResults: {},
  collection: null,
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
  settleSheetFill: (responseId, outcome) =>
    set((state) => ({
      transcript: state.transcript.map((entry) =>
        entry.role === 'assistant' && entry.responseId === responseId
          && entry.sheetFillPlan !== undefined && entry.reportJobId === undefined
          && entry.sheetFillCancelled === undefined
          ? outcome === 'cancelled'
            ? { ...entry, sheetFillCancelled: true }
            : { ...entry, reportJobId: outcome.jobId }
          : entry),
    })),
  clearSession: () =>
    set({
      imSource: null,
      draft: '',
      draftAttachments: [],
      lastOpenMode: 'drawer',
      mode: 'closed',
      contextNotice: null,
      pageContextDeclaration: null,
      restoredPageContext: null,
      sessionContextMode: 'page',
      sessionId: null,
      transcript: [],
      confirmationResults: {},
      collection: null,
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
            draftAttachments: [],
            pageContextDeclaration: next,
            sessionId: null,
            transcript: [],
            confirmationResults: {},
            collection: null,
          };
        }
        return { contextNotice: null, pageContextDeclaration: next };
      }
      if (authorityScopeChanged(previous, next)) {
        return {
          contextNotice: '权限或数据范围已切换；已默认开始新的临时对话。',
          draft: '',
          draftAttachments: [],
          pageContextDeclaration: next,
          restoredPageContext: null,
          sessionId: null,
          transcript: [],
          confirmationResults: {},
          collection: null,
        };
      }
      if (pageBindingChanged(previous, next)) {
        return {
          contextNotice: '页面对象或筛选已切换；已默认开始新的临时对话。',
          draft: '',
          draftAttachments: [],
          pageContextDeclaration: next,
          restoredPageContext: null,
          sessionId: null,
          transcript: [],
          confirmationResults: {},
          collection: null,
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
      // The reference lands with the conversation it belongs to, or not at all.
      const collection = restored.collection !== null
        && restored.collection.sessionId === restored.sessionId ? restored.collection : null;
      return {
        ...draft,
        sessionId: restored.sessionId,
        sessionContextMode: restored.sessionContextMode,
        transcript: restored.collectionLost && collection === null
          ? [...restored.transcript, projectTextResponse(COLLECTION_LOST_NOTICE, 'session')]
          : restored.transcript,
        confirmationResults: restored.confirmationResults,
        restoredPageContext: current === null ? restored.pageContext : null,
        collection,
        draftAttachments: restored.draftAttachments ?? [],
      };
    }),
  setCollection: (collection) => set({ collection }),
  setDraft: (draft) => set({ draft }),
  setDraftAttachments: (draftAttachments) => set({ draftAttachments }),
  setMode: (mode) =>
    set((state) => ({
      lastOpenMode: mode === 'closed' ? state.lastOpenMode : mode,
      mode,
    })),
  startNewSession: () =>
    set((state) => ({
      imSource: null,
      contextNotice: '已新建通用会话；当前上下文不带工作事项引用。',
      draft: '',
      draftAttachments: [],
      pageContextDeclaration:
        state.pageContextDeclaration === null
          ? null
          : createGeneralPageContext(state.pageContextDeclaration),
      restoredPageContext: null,
      sessionContextMode: 'general',
      sessionId: newSessionId(),
      transcript: [],
      confirmationResults: {},
      collection: null,
    })),
}));
