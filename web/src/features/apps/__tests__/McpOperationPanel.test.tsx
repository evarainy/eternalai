import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { McpOperationPanel } from '../McpOperationPanel';
import { useAuthStore } from '../../../stores/authStore';
const api = vi.hoisted(() => ({ get: vi.fn(), resume: vi.fn(), list: vi.fn() }));
vi.mock('../../../api/mcp', async (original) => ({
  ...await original<typeof import('../../../api/mcp')>(), getMcpOperation: api.get, resumeMcpOperation: api.resume, listMcpOperations: api.list,
}));
const id = 'a'.repeat(32);
const operation = { operation_id: id, service_config_id: 'a', revision: 7, expires_at: '2026-10-01T00:00:00Z',
  state: 'UNKNOWN', recovery_action: 'manual_reconcile', review_url: null,
  action: 'talk_preparation_save', service_name: '合成业务平台', argument_preview: { personId: 'synthetic-person' }, preview_digest: 'b'.repeat(64) };
beforeEach(() => { vi.clearAllMocks(); api.list.mockResolvedValue([]); useAuthStore.setState({ generation: 1, status: 'authenticated' }); });
async function query() {
  render(<McpOperationPanel />);
  fireEvent.change(screen.getByRole('textbox', { name: '原操作编号' }), { target: { value: id } });
  fireEvent.click(screen.getByRole('button', { name: '查询状态' }));
  await screen.findByRole('status');
}
describe('MCP operation recovery', () => {
  it('selects owned work and shows the preview before explicit crash recovery', async () => {
    api.list.mockResolvedValue([{ ...operation, state: 'SENDING', recovery_action: 'recover' }]);
    api.resume.mockResolvedValue(operation);
    render(<McpOperationPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /合成业务平台.*talk_preparation_save/ }));
    expect(screen.getByText('synthetic-person')).toBeInTheDocument();
    expect(api.resume).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '检查中断状态' }));
    expect(api.resume).toHaveBeenCalledWith(id, { action: 'recover', expected_revision: 7, preview_digest: operation.preview_digest });
    expect(await screen.findByRole('status')).toHaveTextContent('结果暂不确定');
    expect(api.resume).toHaveBeenCalledTimes(1);
  });
  it('fails closed on an invalid list or missing preview binding', async () => {
    api.list.mockResolvedValue(undefined);
    api.get.mockResolvedValue({ ...operation, preview_digest: undefined });
    render(<McpOperationPanel />);
    expect(await screen.findByRole('alert')).toHaveTextContent('无法读取原操作列表');
    fireEvent.change(screen.getByRole('textbox'), { target: { value: id } });
    fireEvent.click(screen.getByRole('button', { name: '查询状态' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('当前无法继续');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(api.resume).not.toHaveBeenCalled();
  });
  it('requires an explicit takeover for a new login before local confirmation', async () => {
    api.get.mockResolvedValue({ ...operation, state: 'WAITING_LOCAL_CONFIRM', recovery_action: 'takeover' });
    api.resume.mockResolvedValue({ ...operation, state: 'WAITING_LOCAL_CONFIRM', revision: 8, recovery_action: 'confirm' });
    await query();
    expect(screen.queryByRole('button', { name: '确认原操作' })).not.toBeInTheDocument();
    expect(api.resume).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '核验重新授权并接管' }));
    expect(api.resume).toHaveBeenCalledWith(id, { action: 'takeover', expected_revision: 7, preview_digest: operation.preview_digest });
    expect(await screen.findByRole('button', { name: '确认原操作' })).toBeInTheDocument();
    expect(api.resume).toHaveBeenCalledTimes(1);
  });
  it('never automatically retries unknown non-idempotent work', async () => {
    api.get.mockResolvedValue(operation);
    await query();
    expect(screen.getByRole('status')).toHaveTextContent('结果暂不确定');
    expect(screen.queryByRole('button', { name: '确认原操作' })).not.toBeInTheDocument();
    expect(api.resume).not.toHaveBeenCalled();
  });
  it('submits only original reference and revision and waits for external confirmation', async () => {
    api.get.mockResolvedValue({ ...operation, state: 'WAITING_LOCAL_CONFIRM', recovery_action: 'confirm' });
    api.resume.mockResolvedValue({ ...operation, state: 'WAITING_EXTERNAL_CONFIRM', recovery_action: 'read_original',
      review_url: 'https://provider.invalid/review' });
    await query();
    fireEvent.click(screen.getByRole('button', { name: '确认原操作' }));
    expect(api.resume).toHaveBeenCalledWith(id, { action: 'confirm', expected_revision: 7, preview_digest: operation.preview_digest });
    expect(await screen.findByRole('link', { name: '前往业务平台本人确认' })).toHaveAttribute('href', 'https://provider.invalid/review');
    expect(screen.getByRole('status')).toHaveTextContent('等待业务平台本人确认');
  });
  it('drops an old-account result arriving after logout', async () => {
    let resolve!: (value: typeof operation) => void;
    api.get.mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<McpOperationPanel />);
    fireEvent.change(screen.getByRole('textbox'), { target: { value: id } });
    fireEvent.click(screen.getByRole('button', { name: '查询状态' }));
    await act(async () => { useAuthStore.getState().markUnauthenticated(); resolve(operation); });
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});
