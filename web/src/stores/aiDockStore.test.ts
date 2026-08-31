import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useAIDockStore } from './aiDockStore';

function validPageContext() {
  return {
    surface_id: 'work-objects',
    organization_scope: null,
    work_object_refs: [{ work_object_id: 'work-1' }],
    source_refs: [{ source_system: 'oa', source_ref: 'OA-WF-001' }],
    filters: [
      {
        field: 'view',
        operator: 'equals',
        value: 'today',
        source: 'visible_control',
      },
    ],
    selected_metric: null,
    allowed_capabilities: ['oa.work.read'],
    freshness: { state: 'reported', observed_at: '2026-08-30T09:00:00Z' },
    visibility: 'principal',
  };
}

describe('temporary AI Dock state', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    useAIDockStore.setState({
      contextNotice: null,
      draft: '',
      lastOpenMode: 'drawer',
      mode: 'closed',
      pageContextDeclaration: null,
      sessionContextMode: 'page',
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

  it('registers atomically and keeps the previous context when validation fails', () => {
    useAIDockStore.getState().registerPageContext(validPageContext());
    const accepted = useAIDockStore.getState().pageContextDeclaration;

    expect(() =>
      useAIDockStore.getState().registerPageContext({
        ...validPageContext(),
        page_snapshot: { synthetic: 'value' },
      }),
    ).toThrow();

    expect(useAIDockStore.getState().pageContextDeclaration).toBe(accepted);
  });

  it('starts a visible new session when page context returns after being absent', () => {
    useAIDockStore.setState({
      draft: '离开页面前的草稿',
      sessionId: '11111111-1111-4111-8111-111111111111',
      transcript: [{ role: 'user', text: '离开页面前的对话' }],
    });

    useAIDockStore.getState().registerPageContext(validPageContext());

    expect(useAIDockStore.getState().pageContextDeclaration).toEqual(
      validPageContext(),
    );
    expect(useAIDockStore.getState().sessionId).toBeNull();
    expect(useAIDockStore.getState().transcript).toEqual([]);
    expect(useAIDockStore.getState().draft).toBe('');
    expect(useAIDockStore.getState().contextNotice).toContain(
      '已默认开始新的临时对话',
    );
  });

  it('starts a general session with a type-valid empty work-object list', () => {
    vi.spyOn(globalThis.crypto, 'randomUUID').mockReturnValue(
      '22222222-2222-4222-8222-222222222222',
    );
    useAIDockStore.getState().registerPageContext(validPageContext());

    useAIDockStore.getState().startNewSession();

    expect(
      useAIDockStore.getState().pageContextDeclaration?.work_object_refs,
    ).toEqual([]);
    expect(useAIDockStore.getState().sessionId).toBe(
      '22222222-2222-4222-8222-222222222222',
    );
  });

  it('keeps a general session free of work-object refs after page re-registration', () => {
    useAIDockStore.getState().registerPageContext(validPageContext());
    useAIDockStore.getState().startNewSession();

    useAIDockStore.getState().registerPageContext({
      ...validPageContext(),
      work_object_refs: [{ work_object_id: 'work-2' }],
      freshness: { state: 'reported', observed_at: '2026-08-30T10:00:00Z' },
    });

    expect(useAIDockStore.getState().sessionContextMode).toBe('general');
    expect(
      useAIDockStore.getState().pageContextDeclaration?.work_object_refs,
    ).toEqual([]);
  });

  it('fails closed into a new session when organization scope changes', () => {
    useAIDockStore.getState().registerPageContext({
      ...validPageContext(),
      organization_scope: {
        tenant_id: 'default',
        organization_id: 'org-1',
        department_id: 'dept-1',
      },
    });
    useAIDockStore.setState({
      draft: '旧范围草稿',
      sessionId: '11111111-1111-4111-8111-111111111111',
      transcript: [{ role: 'user', text: '旧范围内容' }],
    });

    useAIDockStore.getState().registerPageContext({
      ...validPageContext(),
      organization_scope: {
        tenant_id: 'default',
        organization_id: 'org-2',
        department_id: 'dept-2',
      },
    });

    expect(useAIDockStore.getState().sessionId).toBeNull();
    expect(useAIDockStore.getState().transcript).toEqual([]);
    expect(useAIDockStore.getState().draft).toBe('');
    expect(useAIDockStore.getState().contextNotice).toContain('默认开始新的临时对话');
  });

  it('starts a visible new session when the object changes on the same surface', () => {
    useAIDockStore.getState().registerPageContext(validPageContext());
    useAIDockStore.setState({
      draft: '上一事项草稿',
      sessionId: '11111111-1111-4111-8111-111111111111',
      transcript: [{ role: 'user', text: '上一事项内容' }],
    });

    useAIDockStore.getState().registerPageContext({
      ...validPageContext(),
      work_object_refs: [{ work_object_id: 'work-2' }],
      source_refs: [{ source_system: 'oa', source_ref: 'OA-WF-002' }],
    });

    expect(useAIDockStore.getState().sessionId).toBeNull();
    expect(useAIDockStore.getState().transcript).toEqual([]);
    expect(useAIDockStore.getState().draft).toBe('');
    expect(useAIDockStore.getState().contextNotice).toContain(
      '页面对象或筛选已切换',
    );
  });
});
