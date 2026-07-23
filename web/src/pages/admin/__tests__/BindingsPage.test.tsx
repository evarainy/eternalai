import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App as AntApp, ConfigProvider } from 'antd';
import { ApiError } from '../../../api/mutator';
import type { AdminBindingView } from '../../../generated/admin/admin.schemas';
import BindingsPage from '../BindingsPage';

const apiMocks = vi.hoisted(() => ({
  listBindings: vi.fn(),
}));

vi.mock('../../../generated/admin/admin', () => apiMocks);

const bindings: AdminBindingView[] = [
  {
    binding_id: 'binding-1',
    target_system: 'u8',
    execution_identity: 'user_delegated',
    bind_status: 'active',
    binding_scope: 'company-a',
    account_set_id: 'account-set-1',
    device_domain_id: null,
    reason_code: null,
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
          <BindingsPage />
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>,
  );
}

function submitAiUser(aiUserId = 'user-1') {
  fireEvent.change(screen.getByLabelText('ai_user_id'), {
    target: { value: aiUserId },
  });
  fireEvent.click(screen.getByRole('button', { name: /查\s*询/ }));
}

describe('BindingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.listBindings.mockResolvedValue({ ai_user_id: 'user-1', items: bindings });
  });

  it('does not call listBindings when ai_user_id is empty', async () => {
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /查\s*询/ }));

    expect(await screen.findByText('ai_user_id 必填')).toBeInTheDocument();
    expect(apiMocks.listBindings).not.toHaveBeenCalled();
  });

  it('renders Binding rows, response AI user, and key view fields', async () => {
    renderPage();
    submitAiUser();

    expect(await screen.findByText('binding-1')).toBeInTheDocument();
    expect(screen.getByText('绑定：user-1')).toBeInTheDocument();
    expect(screen.getByText('u8')).toBeInTheDocument();
    expect(screen.getByText('user_delegated')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('company-a')).toBeInTheDocument();
  });

  it('calls listBindings with the exact sparse filter shape', async () => {
    renderPage();
    fireEvent.change(screen.getByLabelText('ai_user_id'), {
      target: { value: '  user-exact  ' },
    });
    fireEvent.mouseDown(screen.getByLabelText('target_system'));
    fireEvent.click(
      await screen.findByText('u8', { selector: '.ant-select-item-option-content' }),
    );
    await waitFor(() => {
      expect(document.querySelector('.ant-select-selection-item')).toHaveTextContent('u8');
    });
    fireEvent.click(screen.getByRole('button', { name: /查\s*询/ }));

    await waitFor(() => {
      expect(apiMocks.listBindings).toHaveBeenCalledWith({
        ai_user_id: 'user-exact',
        target_system: 'u8',
      });
    });
  });

  it.each([
    [403, 'role_not_allowed', 'Management role is required.'],
    [422, 'binding_query_invalid', 'Binding query parameters are invalid.'],
  ])('shows backend error %s with code %s', async (status, code, message) => {
    apiMocks.listBindings.mockRejectedValueOnce(new ApiError(status, code, message));
    renderPage();
    submitAiUser();

    expect(await screen.findByText(`${code}: ${message}`)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /查\s*询/ }));

    expect(await screen.findByText('binding-1')).toBeInTheDocument();
    expect(apiMocks.listBindings).toHaveBeenCalledTimes(2);
    expect(apiMocks.listBindings).toHaveBeenNthCalledWith(2, { ai_user_id: 'user-1' });
  });
});
