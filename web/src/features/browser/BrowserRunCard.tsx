/** Unwired browser run presentation. Callbacks only hand IDs to future owner-checked APIs. */

import { useRef, useState } from 'react';
import { Alert, Button, Card, Flex, Space, Tag, Typography } from 'antd';
import {
  isParsedBrowserRunView,
  isSafeBrowserId,
  type BrowserArtifactView,
  type ParsedBrowserRunView,
} from '../../contracts/browserRunProjection';

const { Text, Title } = Typography;

interface BrowserRunCardProps {
  parsedView: ParsedBrowserRunView | null;
  taskId: string;
  runId: string;
  requestGeneration: number;
  currentGeneration: number;
  resultValue?: Record<string, unknown> | null;
  onCancel?: (taskId: string, runId: string, stateRevision: number) => void | Promise<void>;
  onArtifact?: (artifactId: string) => void | Promise<void>;
}

const phaseLabels = {
  queued: '等待开始',
  acquiring: '准备中',
  running: '执行中',
  verifying: '核验中',
  waiting_user: '等待人工处理',
} as const;

const artifactLabels = {
  result: '结果',
  report: '报告',
  download: '资料',
  draft: '草稿',
} as const;

function artifactItem(
  artifact: BrowserArtifactView,
  onArtifact: BrowserRunCardProps['onArtifact'],
  reportError: () => void,
) {
  return (
    <li key={artifact.artifact_id}>
      <Space wrap>
        <Text>{artifactLabels[artifact.kind]} · {artifact.size_bytes} 字节</Text>
        {artifact.availability === 'available' && onArtifact ? (
          <Button
            size="small"
            onClick={() => {
              void Promise.resolve().then(() => onArtifact(artifact.artifact_id)).catch(reportError);
            }}
          >
            查看资料
          </Button>
        ) : (
          <Text type="secondary">{artifact.availability === 'expired' ? '已过期' : '暂不可用'}</Text>
        )}
      </Space>
    </li>
  );
}

export function BrowserRunCard({
  parsedView,
  taskId,
  runId,
  requestGeneration,
  currentGeneration,
  resultValue,
  onCancel,
  onArtifact,
}: BrowserRunCardProps) {
  const pendingCancel = useRef<{ key: string; token: number } | null>(null);
  const nextCancelToken = useRef(0);
  const activeRequestKey = useRef<string | null>(null);
  const activeCancelKey = useRef<string | null>(null);
  const [cancelInFlightKey, setCancelInFlightKey] = useState<string | null>(null);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [artifactErrorKey, setArtifactErrorKey] = useState<string | null>(null);
  const validGeneration = Number.isSafeInteger(requestGeneration)
    && requestGeneration >= 0 && Number.isSafeInteger(currentGeneration)
    && currentGeneration >= 0 && requestGeneration === currentGeneration;
  const view = isParsedBrowserRunView(parsedView)
    && isSafeBrowserId(taskId) && isSafeBrowserId(runId)
    && validGeneration && parsedView.task_id === taskId && parsedView.run_id === runId
    ? parsedView : null;

  const cancelKey = view === null ? null : `${taskId}/${runId}/${requestGeneration}`;
  activeRequestKey.current = cancelKey;
  activeCancelKey.current = view !== null && view.result === null && !view.cancel.requested
    ? cancelKey : null;

  if (view === null) {
    return <Alert type="warning" showIcon title="当前运行信息暂不可用" />;
  }

  const cancelPendingLocally = pendingCancel.current?.key === cancelKey;
  const terminal = view.result !== null;
  let resultText: string | null = null;
  if (view.status === 'completed' && view.result?.verification === 'verified'
      && resultValue !== null && resultValue !== undefined) {
    try {
      resultText = JSON.stringify(resultValue, null, 2);
    } catch { /* The owner-checked API parser normally excludes invalid values. */ }
  }
  const canCancel = onCancel !== undefined && !terminal && !view.cancel.requested;
  const submitCancel = async () => {
    if (!canCancel || onCancel === undefined || cancelKey === null
        || pendingCancel.current?.key === cancelKey) return;
    const invocation = { key: cancelKey, token: ++nextCancelToken.current };
    pendingCancel.current = invocation;
    setCancelError(null);
    setCancelInFlightKey(cancelKey);
    try {
      await onCancel(taskId, runId, view.state_revision);
      // A resolved callback means only that the request was handed off.
      // Cancellation is acknowledged solely by a later parsed server view.
    } catch {
      if (pendingCancel.current === invocation && activeCancelKey.current === cancelKey) {
        pendingCancel.current = null;
        setCancelError(cancelKey);
      }
    } finally {
      if (activeRequestKey.current === cancelKey && nextCancelToken.current === invocation.token) {
        setCancelInFlightKey(null);
      }
    }
  };

  const statusLabel = view.status === 'completed' ? '业务已完成'
    : view.status === 'failed' ? '执行未完成'
      : view.status === 'cancelled' ? '已取消'
        : view.status === 'waiting_user' ? '等待人工处理' : '正在处理';
  const statusColor = view.status === 'completed' ? 'green'
    : view.status === 'failed' ? 'red'
      : view.status === 'cancelled' ? 'default' : 'blue';

  return (
    <Card aria-label="浏览器任务进度" size="small">
      <Flex align="center" justify="space-between" gap={12} wrap>
        <Space wrap>
          <Title level={5} style={{ margin: 0 }}>浏览器任务</Title>
          <Tag color={statusColor}>{statusLabel}</Tag>
        </Space>
        {canCancel ? (
          <Button
            disabled={cancelPendingLocally}
            loading={cancelPendingLocally && cancelInFlightKey === cancelKey}
            onClick={() => void submitCancel()}
          >
            请求取消
          </Button>
        ) : null}
      </Flex>

      {view.progress !== null ? (
        <p role="status">
          {phaseLabels[view.progress.phase]}
          {view.progress.completed_steps !== null
            ? ` · ${view.progress.completed_steps}/${view.progress.total_steps} 步` : ''}
        </p>
      ) : null}

      {view.cancel.requested && !view.cancel.acknowledged ? (
        <Alert type="info" showIcon title="取消已请求，等待确认" />
      ) : view.cancel.acknowledged && !terminal ? (
        <Alert type="info" showIcon title="取消请求已确认，等待最终结果" />
      ) : cancelPendingLocally && !terminal ? (
        <Alert type="info" showIcon title="取消请求已提交，等待状态更新" />
      ) : null}
      {cancelError === cancelKey ? (
        <Alert type="warning" showIcon title="取消请求未送达，请稍后重试" />
      ) : null}

      {view.result?.effect === 'unknown' ? (
        <Alert type="warning" showIcon title="执行效果暂不明确，请人工核验；不会自动重试" />
      ) : null}
      {view.result?.business === 'completed' && view.result.cleanup === 'failed' ? (
        <Alert type="warning" showIcon title="业务已完成，但资源清理失败，需人工处理" />
      ) : null}
      {view.result?.business === 'failed' && view.result.effect !== 'unknown' ? (
        <Alert type="error" showIcon title="任务未完成，请查看人工处理结果" />
      ) : null}

      {resultText === null ? null : (
        <section aria-label="浏览器结果">
          <Text strong>浏览器结果</Text>
          <pre style={{ maxHeight: 300, overflow: 'auto', whiteSpace: 'pre-wrap',
            overflowWrap: 'anywhere' }}>{resultText}</pre>
        </section>
      )}

      {view.draft !== null ? (
        <p>
          {view.draft.state === 'validated' ? '草稿结构已校验，仍需人工审阅'
            : view.draft.state === 'rejected' ? '草稿需修改' : '草稿待审阅'}
          <Text type="secondary"> · 参数 {view.draft.parameter_names.length} 项</Text>
        </p>
      ) : null}

      {view.artifacts.length > 0 ? (
        <section aria-label="任务资料">
          <Text strong>任务资料</Text>
          <ul>
            {view.artifacts.map((artifact) => artifactItem(
              artifact, onArtifact, () => {
                if (activeRequestKey.current === cancelKey) setArtifactErrorKey(cancelKey);
              },
            ))}
          </ul>
          {artifactErrorKey === cancelKey ? <Alert type="warning" showIcon title="资料暂不可用" /> : null}
        </section>
      ) : null}
    </Card>
  );
}
