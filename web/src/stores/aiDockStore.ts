import { create } from 'zustand';
import type { ProjectedResponse } from '../contracts/runtimeProjection';

export type AIDockMode = 'closed' | 'drawer' | 'pinned';
type OpenAIDockMode = Exclude<AIDockMode, 'closed'>;

export type TranscriptEntry =
  | {
      role: 'user';
      text: string;
    }
  | ProjectedResponse;

interface AIDockState {
  draft: string;
  lastOpenMode: OpenAIDockMode;
  mode: AIDockMode;
  sessionId: string | null;
  transcript: TranscriptEntry[];
  appendTranscript: (entry: TranscriptEntry) => void;
  clearSession: () => void;
  closeDock: () => void;
  ensureSession: () => string;
  openDock: () => void;
  setDraft: (draft: string) => void;
  setMode: (mode: AIDockMode) => void;
  startNewSession: () => void;
}

function newSessionId(): string {
  return crypto.randomUUID();
}

export const useAIDockStore = create<AIDockState>((set, get) => ({
  draft: '',
  lastOpenMode: 'drawer',
  mode: 'closed',
  sessionId: null,
  transcript: [],
  appendTranscript: (entry) =>
    set((state) => ({ transcript: [...state.transcript, entry] })),
  clearSession: () =>
    set({
      draft: '',
      lastOpenMode: 'drawer',
      mode: 'closed',
      sessionId: null,
      transcript: [],
    }),
  closeDock: () => set({ mode: 'closed' }),
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
  setDraft: (draft) => set({ draft }),
  setMode: (mode) =>
    set((state) => ({
      lastOpenMode: mode === 'closed' ? state.lastOpenMode : mode,
      mode,
    })),
  startNewSession: () =>
    set({ draft: '', sessionId: newSessionId(), transcript: [] }),
}));
