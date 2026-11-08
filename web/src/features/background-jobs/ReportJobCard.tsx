/** OA pending report card: polls the caller's own job, offers cancel and the download link. */

import { useEffect, useState } from 'react';
import { Alert, Button, Card, Flex, Space, Tag, Typography } from 'antd';
import { ApiError } from '../../api/mutator';
import type { BackgroundJobResponse } from '../../generated/background-jobs/background-jobs.schemas';
import { cancelJob, isActive, readJob } from './selfCheckApi';
import {
  REPORT_KIND,
  reportDownloadUrl,
  reportFailureText,
  reportResult,
} from './reportJob';

const { Text, Title } = Typography;
const ACTIVE_POLL_MS = 2_000;
const RETRY_POLL_MS = 5_000;

const STATUS: Record<BackgroundJobResponse['status'], { label: string; color: string }> = {
  accepted: { label: '排队中', color: 'blue' },
  running: { label: '生成中', color: 'blue' },
  cancel_requested: { label: '正在取消', color: 'default' },
  succeeded: { label: '已生成', color: 'green' },
  failed: { label: '未能生成', color: 'red' },
  cancelled: { label: '已取消', color: 'default' },
};

interface ReportJobCardProps {
  jobId: string;
  /** Removes a finished card from this tab's recovery list. */
  onDismiss?: () => void;
}

export function ReportJobCard({ jobId, onDismiss }: ReportJobCardProps) {
  const [job, setJob] = useState<BackgroundJobResponse | null>(null);
  const [readState, setReadState] = useState<'ok' | 'missing' | 'retrying'>('ok');
  const [cancelling, setCancelling] = useState(false);
  const [cancelFailed, setCancelFailed] = useState(false);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const next = await readJob(jobId);
        if (stopped) return;
        if (next.job_id !== jobId || next.kind !== REPORT_KIND) {
          setReadState('missing');
          return;
        }
        setJob(next);
        setReadState('ok');
        if (isActive(next)) timer = window.setTimeout(() => void poll(), ACTIVE_POLL_MS);
      } catch (error) {
        if (stopped) return;
        if (error instanceof ApiError && error.status === 404) {
          setReadState('missing');
          return;
        }
        setReadState('retrying');
        timer = window.setTimeout(() => void poll(), RETRY_POLL_MS);
      }
    };
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [jobId]);

  const cancel = async () => {
    setCancelling(true);
    setCancelFailed(false);
    try {
      const next = await cancelJob(jobId);
      if (next.job_id === jobId) setJob(next);
    } catch {
      setCancelFailed(true);
    } finally {
      setCancelling(false);
    }
  };

  if (readState === 'missing') {
    return (
      <Alert type="warning" showIcon title="没有找到这项报表任务"
        action={onDismiss ? <Button size="small" onClick={onDismiss}>移除</Button> : undefined} />
    );
  }

  const active = job !== null && isActive(job);
  const result = job === null ? null : reportResult(job);
  const status = job === null ? null : STATUS[job.status];

  return (
    <Card aria-label="OA 待办报表" size="small">
      <Flex align="center" justify="space-between" gap={12} wrap>
        <Space wrap>
          <Title level={5} style={{ margin: 0 }}>OA 待办报表</Title>
          {status === null ? <Tag>读取中</Tag> : <Tag color={status.color}>{status.label}</Tag>}
        </Space>
        <Space>
          {active && job?.status !== 'cancel_requested' ? (
            <Button loading={cancelling} disabled={cancelling} onClick={() => void cancel()}>
              取消生成
            </Button>
          ) : null}
          {job !== null && !active && onDismiss ? (
            <Button onClick={onDismiss}>移除</Button>
          ) : null}
        </Space>
      </Flex>

      {active ? <p role="status">正在后台读取本人 OA 待办并生成文件，可以离开此页面。</p> : null}
      {result !== null ? (
        <p>
          <Text>已生成 {result.rowCount} 条待办。</Text>{' '}
          <a href={reportDownloadUrl(result.artifactId)} download>下载 Excel</a>
        </p>
      ) : null}
      {job?.status === 'succeeded' && result === null ? (
        <Alert type="warning" showIcon title="报表结果暂不可用，请重新导出" />
      ) : null}
      {job?.status === 'failed' ? (
        <Alert type="error" showIcon title={reportFailureText(job.result_code)} />
      ) : null}
      {job?.status === 'cancelled' ? <p>已取消。</p> : null}
      {readState === 'retrying' ? (
        <Alert type="warning" showIcon title="暂时读不到任务状态，正在重试" />
      ) : null}
      {cancelFailed ? <Alert type="warning" showIcon title="取消请求未送达，请稍后重试" /> : null}
    </Card>
  );
}
