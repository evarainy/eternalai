import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { describe, expect, it, vi } from 'vitest';
import {
  parseBrowserRunProjection,
  type ParsedBrowserRunView,
} from '../../contracts/browserRunProjection';
import { BrowserRunCard } from './BrowserRunCard';

const DIGEST = 'a'.repeat(64);

function running() {
  return {
    schema_version: 'browser.run.v1',
    task_id: 'task_1',
    run_id: 'run_1',
    state_revision: 2,
    status: 'running',
    progress: { phase: 'running', completed_steps: 1, total_steps: 3 },
    cancel: { requested: false, acknowledged: false },
    result: null,
    artifacts: [],
    draft: null,
  };
}

function completed() {
  return {
    ...running(),
    status: 'completed',
    state_revision: 4,
    progress: null,
    result: {
      business: 'completed', effect: 'acknowledged', verification: 'verified',
      cleanup: 'failed', error_code: null, dispatch_failure_code: null,
      terminal_revision: 4, automatic_replay: false,
    },
  };
}

function unknown() {
  return {
    ...completed(),
    status: 'failed',
    result: {
      ...completed().result,
      business: 'failed', effect: 'unknown', verification: 'incomplete',
      cleanup: 'quarantined', error_code: 'browser_effect_unknown',
    },
  };
}

function view(raw: unknown): ParsedBrowserRunView {
  const parsed = parseBrowserRunProjection(raw);
  expect(parsed).not.toBeNull();
  if (parsed === null) throw new Error('synthetic_view_invalid');
  return parsed;
}

function renderCard(
  parsedView: ParsedBrowserRunView | null,
  options: {
    taskId?: string;
    runId?: string;
    requestGeneration?: number;
    currentGeneration?: number;
    onCancel?: (taskId: string, runId: string, stateRevision: number) => void | Promise<void>;
    onArtifact?: (artifactId: string) => void | Promise<void>;
  } = {},
) {
  const props = {
    parsedView,
    taskId: options.taskId ?? 'task_1',
    runId: options.runId ?? 'run_1',
    requestGeneration: options.requestGeneration ?? 3,
    currentGeneration: options.currentGeneration ?? 3,
    onCancel: options.onCancel,
    onArtifact: options.onArtifact,
  };
  const rendered = render(
    <ConfigProvider theme={{ token: { motion: false } }}>
      <BrowserRunCard {...props} />
    </ConfigProvider>,
  );
  return {
    ...rendered,
    update: (
      next: ParsedBrowserRunView | null,
      changes: Partial<typeof props> = {},
    ) => rendered.rerender(
      <ConfigProvider theme={{ token: { motion: false } }}>
        <BrowserRunCard {...props} {...changes} parsedView={next} />
      </ConfigProvider>,
    ),
  };
}

describe('unwired BrowserRunCard', () => {
  it('renders progress and throttles cancellation without claiming acknowledgement', async () => {
    let resolveCancel!: () => void;
    const pending = new Promise<void>((resolve) => { resolveCancel = resolve; });
    const onCancel = vi.fn().mockReturnValue(pending);
    const card = renderCard(view(running()), { onCancel });
    expect(screen.getByText('执行中 · 1/3 步')).toBeInTheDocument();
    const button = screen.getByRole('button', { name: '请求取消' });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledWith('task_1', 'run_1', 2);
    expect(screen.getByText('取消请求已提交，等待状态更新')).toBeInTheDocument();
    await act(async () => { resolveCancel(); await pending; });
    expect(button).toBeDisabled();
    expect(screen.queryByText('已取消')).not.toBeInTheDocument();

    card.update(view({ ...running(), state_revision: 3,
      cancel: { requested: true, acknowledged: false } }));
    expect(screen.getByText('取消已请求，等待确认')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '请求取消' })).not.toBeInTheDocument();
    card.update(view({ ...running(), state_revision: 4,
      cancel: { requested: true, acknowledged: true } }));
    expect(screen.getByText('取消请求已确认，等待最终结果')).toBeInTheDocument();
    expect(screen.queryByText('已取消')).not.toBeInTheDocument();
  });

  it('allows another cancel request only after a rejected handoff', async () => {
    const onCancel = vi.fn()
      .mockRejectedValueOnce(new Error('synthetic_private_message'))
      .mockResolvedValueOnce(undefined);
    renderCard(view(running()), { onCancel });
    fireEvent.click(screen.getByRole('button', { name: /请求取消$/ }));
    expect(await screen.findByText('取消请求未送达，请稍后重试')).toBeInTheDocument();
    expect(screen.queryByText('synthetic_private_message')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /请求取消$/ }));
    await waitFor(() => expect(onCancel).toHaveBeenCalledTimes(2));
  });

  it('ignores an old generation cancellation rejection while a newer request is pending', async () => {
    let rejectOld!: (reason: Error) => void;
    let resolveNew!: () => void;
    const oldRequest = new Promise<void>((_, reject) => { rejectOld = reject; });
    const newRequest = new Promise<void>((resolve) => { resolveNew = resolve; });
    const onCancel = vi.fn()
      .mockReturnValueOnce(oldRequest).mockReturnValueOnce(newRequest);
    const card = renderCard(view(running()), { onCancel });
    fireEvent.click(screen.getByRole('button', { name: /请求取消$/ }));
    card.update(view({ ...running(), state_revision: 3 }), {
      requestGeneration: 4, currentGeneration: 4,
    });
    fireEvent.click(screen.getByRole('button', { name: /请求取消$/ }));
    expect(onCancel).toHaveBeenNthCalledWith(2, 'task_1', 'run_1', 3);
    await act(async () => { rejectOld(new Error('synthetic_old_failure')); await oldRequest.catch(() => {}); });
    expect(screen.queryByText('取消请求未送达，请稍后重试')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /请求取消$/ })).toBeDisabled();
    await act(async () => { resolveNew(); await newRequest; });
    expect(screen.getByRole('button', { name: /请求取消$/ })).toBeDisabled();
  });

  it('ignores an old cancellation completion before a newer rejection', async () => {
    let resolveOld!: () => void;
    let rejectNew!: (reason: Error) => void;
    const oldRequest = new Promise<void>((resolve) => { resolveOld = resolve; });
    const newRequest = new Promise<void>((_, reject) => { rejectNew = reject; });
    const onCancel = vi.fn()
      .mockReturnValueOnce(oldRequest).mockReturnValueOnce(newRequest);
    const card = renderCard(view(running()), { onCancel });
    fireEvent.click(screen.getByRole('button', { name: /请求取消$/ }));
    card.update(view({ ...running(), state_revision: 3 }), {
      requestGeneration: 4, currentGeneration: 4,
    });
    fireEvent.click(screen.getByRole('button', { name: /请求取消$/ }));
    await act(async () => { resolveOld(); await oldRequest; });
    expect(screen.getByRole('button', { name: /请求取消$/ })).toBeDisabled();
    await act(async () => { rejectNew(new Error('synthetic_new_failure')); await newRequest.catch(() => {}); });
    expect(screen.getByText('取消请求未送达，请稍后重试')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /请求取消$/ })).toBeEnabled();
  });

  it('shows unknown effect without any automatic retry affordance', () => {
    const onCancel = vi.fn();
    renderCard(view(unknown()), { onCancel });
    expect(screen.getByText('执行效果暂不明确，请人工核验；不会自动重试')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /重试|继续执行|请求取消/ })).not.toBeInTheDocument();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('preserves business success alongside failed cleanup', () => {
    renderCard(view(completed()));
    expect(screen.getByText('业务已完成', { selector: '.ant-tag' })).toBeInTheDocument();
    expect(screen.getByText('业务已完成，但资源清理失败，需人工处理')).toBeInTheDocument();
    expect(screen.queryByText('执行未完成')).not.toBeInTheDocument();
  });

  it('refuses stale generation, wrong Task/Run and unparsed input', () => {
    const onCancel = vi.fn();
    const fresh = view(running());
    const first = renderCard(fresh, { currentGeneration: 4, onCancel });
    expect(screen.getByText('当前运行信息暂不可用')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '请求取消' })).not.toBeInTheDocument();
    first.unmount();
    const second = renderCard(fresh, { runId: 'run_2', onCancel });
    expect(screen.getByText('当前运行信息暂不可用')).toBeInTheDocument();
    second.unmount();
    renderCard(running() as unknown as ParsedBrowserRunView, { onCancel });
    expect(screen.getByText('当前运行信息暂不可用')).toBeInTheDocument();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('offers artifact metadata only and invokes its callback with the ID', async () => {
    const onArtifact = vi.fn();
    const report = {
      artifact_id: 'report_1', kind: 'report', media_type: 'application/pdf',
      size_bytes: 1024, expires_at: '2026-10-03T12:00:00Z', availability: 'available',
    };
    const expired = { ...report, artifact_id: 'report_2', availability: 'expired' };
    const card = renderCard(view({ ...running(), artifacts: [report, expired] }), {
      onArtifact,
    });
    expect(screen.getAllByText('报告 · 1024 字节')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: '查看资料' }));
    await waitFor(() => expect(onArtifact).toHaveBeenCalledWith('report_1'));
    expect(onArtifact.mock.calls[0]).toHaveLength(1);
    expect(card.container.querySelector('a[href]')).toBeNull();
    expect(card.container.innerHTML).not.toContain('https://');
  });

  it('ignores an old artifact rejection after request generation changes', async () => {
    let rejectOld!: (reason: Error) => void;
    const oldRequest = new Promise<void>((_, reject) => { rejectOld = reject; });
    const onArtifact = vi.fn().mockReturnValue(oldRequest);
    const report = {
      artifact_id: 'report_1', kind: 'report', media_type: 'application/pdf',
      size_bytes: 1024, expires_at: '2026-10-03T12:00:00Z', availability: 'available',
    };
    const current = view({ ...running(), artifacts: [report] });
    const card = renderCard(current, { onArtifact });
    fireEvent.click(screen.getByRole('button', { name: '查看资料' }));
    card.update(current, { requestGeneration: 4, currentGeneration: 4 });
    await act(async () => {
      rejectOld(new Error('synthetic_old_artifact_failure'));
      await oldRequest.catch(() => {});
    });
    expect(screen.queryByText('资料暂不可用')).not.toBeInTheDocument();
    expect(screen.queryByText('synthetic_old_artifact_failure')).not.toBeInTheDocument();
  });

  it('keeps structurally validated drafts informational', () => {
    renderCard(view({
      ...running(),
      draft: {
        draft_id: 'draft_1', draft_revision: 1, base_revision: 0,
        base_publication_digest: DIGEST, draft_digest: DIGEST,
        state: 'validated', rejection: null,
        parameter_names: ['business_key'], executable: false,
      },
    }));
    expect(screen.getByText(/草稿结构已校验，仍需人工审阅/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /执行|发布|确认草稿/ })).not.toBeInTheDocument();
    expect(screen.queryByText('business_key')).not.toBeInTheDocument();
  });
});
