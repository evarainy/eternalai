import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { ApiError } from '../../../api/mutator';
import type { AdminTaskView } from '../../../generated/admin/admin.schemas';
import TasksPage from '../TasksPage';

const apiMocks = vi.hoisted(() => ({
  listTasks: vi.fn(),
  listTaskEvents: vi.fn(),
}));

vi.mock('../../../generated/admin/admin', () => apiMocks);

const tasks: AdminTaskView[] = [
  {
    task_id: 'task-1',
    session_id: 'session-1',
    ai_user_id: 'user-1',
    status: 'completed',
    capability_id: 'cap.query',
    error_code: null,
  },
  {
    task_id: 'task-2',
    session_id: 'session-1',
    ai_user_id: 'user-2',
    status: 'failed',
    capability_id: null,
    error_code: 'adapter_timeout',
  },
];

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return render(
    <ConfigProvider>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <TasksPage />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
}

function submitSessionFilter(sessionId = 'session-1') {
  fireEvent.change(screen.getByLabelText('session_id'), {
    target: { value: sessionId },
  });
  fireEvent.click(screen.getByRole('button', { name: /查\s*询/ }));
}

describe('TasksPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.listTasks.mockResolvedValue({ items: tasks });
    apiMocks.listTaskEvents.mockResolvedValue({ items: [] });
  });

  it('renders Task rows and key view fields after a filtered query', async () => {
    renderPage();
    submitSessionFilter();

    expect(await screen.findByText('task-1')).toBeInTheDocument();
    expect(screen.getByText('completed')).toBeInTheDocument();
    expect(screen.getByText('cap.query')).toBeInTheDocument();
    expect(screen.getByText('task-2')).toBeInTheDocument();
    expect(screen.getByText('adapter_timeout')).toBeInTheDocument();
  });

  it('calls listTasks with only the submitted non-empty filter keys', async () => {
    renderPage();
    submitSessionFilter('  session-exact  ');

    await waitFor(() => {
      expect(apiMocks.listTasks).toHaveBeenCalledWith({ session_id: 'session-exact' });
    });
  });

  it('does not call listTasks when both filters are empty', async () => {
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /查\s*询/ }));

    expect(
      await screen.findByText('至少填 session_id 或 ai_user_id'),
    ).toBeInTheDocument();
    expect(apiMocks.listTasks).not.toHaveBeenCalled();
  });

  it('loads the selected Task events and renders only allowlisted evidence fields', async () => {
    apiMocks.listTaskEvents.mockResolvedValueOnce({
      items: [
        {
          event_id: 'event-1',
          task_id: 'task-1',
          event_type: 'workflow_step_completed',
          timestamp: '2026-07-23T01:02:03Z',
          evidence: {
            workflow_id: 'workflow-1',
            step_id: 'step-1',
            completed_step_ids: ['step-1'],
            step_output_keys: { 'step-1': ['document_id'] },
            raw_payload: 'must-not-render',
            credential: 'must-not-render-either',
          },
        },
      ],
    });
    renderPage();
    submitSessionFilter();
    await screen.findByText('task-1');

    const viewEvidenceButton = screen.getAllByRole('button', { name: '查看证据' })[0];
    if (!viewEvidenceButton) {
      throw new Error('Missing evidence button for task-1');
    }
    fireEvent.click(viewEvidenceButton);

    await waitFor(() => {
      expect(apiMocks.listTaskEvents).toHaveBeenCalledWith('task-1');
    });
    expect(await screen.findByText('workflow-1')).toBeInTheDocument();
    expect(screen.getByText('step-1')).toBeInTheDocument();
    expect(screen.getByText('["step-1"]')).toBeInTheDocument();
    expect(screen.getByText('{"step-1":["document_id"]}')).toBeInTheDocument();
    expect(screen.queryByText('raw_payload')).not.toBeInTheDocument();
    expect(screen.queryByText('must-not-render')).not.toBeInTheDocument();
    expect(screen.queryByText('credential')).not.toBeInTheDocument();
    expect(screen.queryByText('must-not-render-either')).not.toBeInTheDocument();
  });

  it('shows the backend 403 code and message for the Task list', async () => {
    apiMocks.listTasks.mockRejectedValueOnce(
      new ApiError(403, 'role_not_allowed', 'Management role is required.'),
    );
    renderPage();
    submitSessionFilter();

    expect(
      await screen.findByText('role_not_allowed: Management role is required.'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /查\s*询/ }));

    expect(await screen.findByText('task-1')).toBeInTheDocument();
    expect(apiMocks.listTasks).toHaveBeenCalledTimes(2);
    expect(apiMocks.listTasks).toHaveBeenNthCalledWith(2, { session_id: 'session-1' });
  });

  it('shows the backend 422 task_filter_required response', async () => {
    apiMocks.listTasks.mockRejectedValueOnce(
      new ApiError(422, 'task_filter_required', 'session_id or ai_user_id is required.'),
    );
    renderPage();
    submitSessionFilter();

    expect(
      await screen.findByText(
        'task_filter_required: session_id or ai_user_id is required.',
      ),
    ).toBeInTheDocument();
  });

  it('shows task_not_found when the selected Task events no longer exist', async () => {
    apiMocks.listTaskEvents.mockRejectedValueOnce(
      new ApiError(404, 'task_not_found', 'Task was not found.'),
    );
    renderPage();
    submitSessionFilter();
    await screen.findByText('task-1');

    const viewEvidenceButton = screen.getAllByRole('button', { name: '查看证据' })[0];
    if (!viewEvidenceButton) {
      throw new Error('Missing evidence button for task-1');
    }
    fireEvent.click(viewEvidenceButton);

    expect(
      await screen.findByText('task_not_found: Task was not found.'),
    ).toBeInTheDocument();
  });
});
