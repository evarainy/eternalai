import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Card, Descriptions, InputNumber, Space, Tag, Typography } from 'antd';
import { ApiError } from '../../api/mutator';
import { useAuthStore } from '../../stores/authStore';
import type { BackgroundJobResponse } from '../../generated/background-jobs/background-jobs.schemas';
import {
  CHECK_TEXT,
  STATUS_TEXT,
  cancelJob,
  isActive,
  latestSelfCheck,
  readJob,
  submitSelfCheck,
} from './selfCheckApi';

const { Text } = Typography;
const LATEST_KEY = ['background-jobs', 'self-check', 'latest'] as const;

function failureText(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) return '请先登录后再运行后台自检。';
  if (error instanceof ApiError && error.message) return error.message;
  return '操作没有成功，请稍后再试。';
}

function resultText(job: BackgroundJobResponse): string | null {
  if (job.status !== 'succeeded') return null;
  return job.result_code === 'ok' ? '全部检查通过' : '部分检查未通过';
}

function JobResult({ job }: { job: BackgroundJobResponse }) {
  const checks = (job.result_summary?.checks ?? {}) as Record<string, string>;
  return (
    <Descriptions size="small" column={1} bordered>
      <Descriptions.Item label="状态">{STATUS_TEXT[job.status]}</Descriptions.Item>
      {resultText(job) && <Descriptions.Item label="结果">{resultText(job)}</Descriptions.Item>}
      <Descriptions.Item label="发起时间">{new Date(job.created_at).toLocaleString()}</Descriptions.Item>
      {job.finished_at && (
        <Descriptions.Item label="结束时间">{new Date(job.finished_at).toLocaleString()}</Descriptions.Item>
      )}
      {Object.entries(checks).map(([name, value]) => (
        <Descriptions.Item key={name} label={CHECK_TEXT[name] ?? name}>
          <Tag color={value === 'ok' ? 'green' : 'red'}>{value === 'ok' ? '正常' : '异常'}</Tag>
        </Descriptions.Item>
      ))}
    </Descriptions>
  );
}

export default function SelfCheckPanel() {
  const authenticated = useAuthStore((state) => state.status === 'authenticated');
  const queryClient = useQueryClient();
  const [holdSeconds, setHoldSeconds] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);

  const latest = useQuery({
    queryKey: LATEST_KEY,
    queryFn: latestSelfCheck,
    enabled: authenticated,
  });
  const job = latest.data ?? null;
  const jobId = job?.job_id;
  const polled = useQuery({
    queryKey: ['background-jobs', 'job', jobId],
    queryFn: () => readJob(jobId as string),
    enabled: authenticated && jobId !== undefined && job !== null && isActive(job),
    refetchInterval: (query) => {
      const current = query.state.data;
      return current === undefined || isActive(current) ? 2000 : false;
    },
  });
  const polledJob = polled.data && polled.data.job_id === jobId ? polled.data : null;
  const current = polledJob && job && polledJob.updated_at >= job.updated_at ? polledJob : job;

  const submit = useMutation({
    mutationFn: () => submitSelfCheck(holdSeconds),
    onSuccess: (accepted) => {
      setNotice('后台自检已接收，可以关闭页面，稍后回来查看结果。');
      queryClient.setQueryData(LATEST_KEY, accepted);
    },
  });
  const cancel = useMutation({
    mutationFn: (id: string) => cancelJob(id),
    onSuccess: (updated) => {
      setNotice(updated.status === 'cancel_requested' ? '已请求取消，后台正在停止。' : null);
      queryClient.setQueryData(LATEST_KEY, updated);
    },
  });

  if (!authenticated) {
    return (
      <Card title="后台自检">
        <Text>请先 <Link to="/login">登录</Link> 后再运行后台自检。</Text>
      </Card>
    );
  }

  const error = submit.error ?? cancel.error ?? latest.error;
  return (
    <Card title="后台自检">
      <Space direction="vertical" style={{ width: '100%' }}>
        <Space wrap>
          <Text>开始检查前等待</Text>
          <InputNumber
            min={0}
            max={600}
            value={holdSeconds}
            onChange={(value) => setHoldSeconds(value ?? 0)}
            addonAfter="秒"
          />
          <Button type="primary" loading={submit.isPending} onClick={() => submit.mutate()}>
            运行后台自检
          </Button>
          {current && isActive(current) && current.status !== 'cancel_requested' && (
            <Button loading={cancel.isPending} onClick={() => cancel.mutate(current.job_id)}>
              取消本次自检
            </Button>
          )}
        </Space>
        {notice && <Alert type="success" showIcon message={notice} />}
        {error && <Alert type="error" showIcon message={failureText(error)} />}
        {current ? <JobResult job={current} /> : <Text type="secondary">还没有运行过后台自检。</Text>}
      </Space>
    </Card>
  );
}
