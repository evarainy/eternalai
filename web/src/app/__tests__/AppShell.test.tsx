import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider } from 'antd';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { useAIDockStore } from '../../stores/aiDockStore';
import { AppShell } from '../AppShell';
import { workbenchTheme } from '../theme';

function workObjectsPageContext() {
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
    allowed_capabilities: [],
    freshness: { state: 'reported', observed_at: '2026-08-30T09:00:00Z' },
    visibility: 'principal',
  };
}

function renderShell(initialPath = '/work-objects') {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return render(
    <ConfigProvider theme={workbenchTheme}>
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<div>开始新工作页面</div>} />
              <Route
                path="/work-objects"
                element={
                  <div>
                    事项页面
                    <Link to="/admin/tasks">去任务证据</Link>
                  </div>
                }
              />
              <Route path="/admin/tasks" element={<div>证据页面</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ConfigProvider>,
  );
}

describe('AppShell and singleton AI Dock', () => {
  beforeEach(() => {
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

  it('mounts exactly one Dock instance for the whole shell', () => {
    renderShell();

    expect(screen.getAllByTestId('ai-dock')).toHaveLength(1);
  });

  it('keeps drawer mode out of the content grid and lets pinned mode offset it', () => {
    useAIDockStore.setState({ mode: 'drawer', lastOpenMode: 'drawer' });
    renderShell();

    expect(screen.getByTestId('app-main').parentElement).toHaveAttribute(
      'data-dock-offset',
      'false',
    );
    expect(screen.getByTestId('ai-dock')).toHaveAttribute('data-mode', 'drawer');

    act(() => useAIDockStore.getState().setMode('pinned'));

    expect(screen.getByTestId('app-main').parentElement).toHaveAttribute(
      'data-dock-offset',
      'true',
    );
    expect(screen.getByTestId('ai-dock')).toHaveAttribute('data-mode', 'pinned');
  });

  it('keeps the same temporary session and conversation across SPA navigation', () => {
    useAIDockStore.setState({
      draft: '尚未发送的补充内容',
      lastOpenMode: 'drawer',
      mode: 'drawer',
      sessionId: '11111111-1111-4111-8111-111111111111',
      transcript: [{ role: 'user', text: '请继续处理这一项' }],
    });
    renderShell();

    expect(screen.getByText('请继续处理这一项')).toBeInTheDocument();
    expect(screen.getByLabelText('要 AI 帮什么')).toHaveValue('尚未发送的补充内容');
    fireEvent.click(screen.getByRole('link', { name: '去任务证据' }));

    expect(screen.getByText('证据页面')).toBeInTheDocument();
    expect(screen.getByText('请继续处理这一项')).toBeInTheDocument();
    expect(useAIDockStore.getState().sessionId).toBe(
      '11111111-1111-4111-8111-111111111111',
    );
    expect(useAIDockStore.getState().mode).toBe('drawer');
    expect(screen.getByText('任务证据', { selector: 'strong' })).toBeInTheDocument();
  });

  it('provides visible input guidance, a textual state, and current-location semantics', () => {
    useAIDockStore.setState({ mode: 'drawer' });
    useAIDockStore.getState().registerPageContext(workObjectsPageContext());
    renderShell();

    expect(screen.getByLabelText('要 AI 帮什么')).toBeInTheDocument();
    expect(screen.getByText('写清对象、时间和要得到的结果')).toBeInTheDocument();
    expect(screen.getByText('正在协助：工作事项')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '工作事项' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    expect(screen.queryByRole('button', { name: /^⚙$/ })).not.toBeInTheDocument();
  });

  it('keeps the landing page context-free without crashing the singleton Dock', async () => {
    useAIDockStore.setState({ mode: 'drawer' });
    useAIDockStore.getState().registerPageContext(workObjectsPageContext());

    renderShell('/');

    expect(screen.getByText('开始新工作页面')).toBeInTheDocument();
    expect(screen.getByTestId('ai-dock')).not.toBeVisible();
    await waitFor(() =>
      expect(useAIDockStore.getState().pageContextDeclaration).toBeNull(),
    );
  });
});
