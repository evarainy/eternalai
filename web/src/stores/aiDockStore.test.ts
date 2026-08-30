import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAIDockStore } from './aiDockStore';

describe('temporary AI Dock state', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    useAIDockStore.setState({
      draft: '',
      lastOpenMode: 'drawer',
      mode: 'closed',
      sessionId: null,
      transcript: [],
    });
  });

  it('keeps mode, draft, session, and transcript in memory only', () => {
    vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(
      '11111111-1111-4111-8111-111111111111',
    );

    useAIDockStore.getState().openDock();
    useAIDockStore.getState().setDraft('临时输入');
    useAIDockStore.getState().appendTranscript({ role: 'user', text: '临时对话' });
    expect(useAIDockStore.getState().ensureSession()).toBe(
      '11111111-1111-4111-8111-111111111111',
    );

    expect(useAIDockStore.getState().mode).toBe('drawer');
    expect(localStorage).toHaveLength(0);
    expect(sessionStorage).toHaveLength(0);
  });

  it('closing only hides the Dock and does not delete the current conversation', () => {
    useAIDockStore.setState({
      mode: 'drawer',
      sessionId: '11111111-1111-4111-8111-111111111111',
      transcript: [{ role: 'user', text: '继续保留' }],
    });

    useAIDockStore.getState().closeDock();

    expect(useAIDockStore.getState().mode).toBe('closed');
    expect(useAIDockStore.getState().sessionId).not.toBeNull();
    expect(useAIDockStore.getState().transcript).toEqual([
      { role: 'user', text: '继续保留' },
    ]);
  });
});
