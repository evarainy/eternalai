import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  Space,
  Spin,
  Table,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ApiError } from '../../api/mutator';
import { listTaskEvents, listTasks } from '../../generated/admin/admin';
import type {
  AdminTaskEventEvidence,
  AdminTaskView,
  ListTasksParams,
} from '../../generated/admin/admin.schemas';

const { Title, Text } = Typography;

const evidenceFields: (keyof AdminTaskEventEvidence)[] = [
  'capability_id',
  'selection_rule',
  'workflow_id',
  'workflow_version',
  'workflow_status',
  'error_code',
  'step_id',
  'step_index',
  'step_status',
  'attempt',
  'retry_number',
  'max_attempts',
  'waiting_step_id',
  'waiting_step_index',
  'confirmed_capability_id',
  'completed_step_ids',
  'step_output_keys',
  'recovery_input_keys',
];

interface TaskFilterValues {
  session_id?: string;
  ai_user_id?: string;
}

interface SubmittedTaskQuery {
  filters: ListTasksParams;
  revision: number;
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.code}: ${error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'unknown_error: 请求失败';
}

function evidenceText(value: AdminTaskEventEvidence[keyof AdminTaskEventEvidence]): string {
  if (Array.isArray(value) || (typeof value === 'object' && value !== null)) {
    return JSON.stringify(value);
  }
  return String(value);
}

export default function TasksPage() {
  const [submittedQuery, setSubmittedQuery] = useState<SubmittedTaskQuery>();
  const [filterError, setFilterError] = useState<string>();
  const [selectedTaskId, setSelectedTaskId] = useState<string>();

  const taskQuery = useQuery({
    queryKey: ['admin', 'tasks', submittedQuery?.filters, submittedQuery?.revision],
    queryFn: () => listTasks(submittedQuery?.filters),
    enabled: submittedQuery !== undefined,
  });

  const eventQuery = useQuery({
    queryKey: ['admin', 'task-events', selectedTaskId],
    queryFn: () => listTaskEvents(selectedTaskId as string),
    enabled: selectedTaskId !== undefined,
  });

  const submitFilters = (values: TaskFilterValues) => {
    const sessionId = values.session_id?.trim();
    const aiUserId = values.ai_user_id?.trim();
    if (!sessionId && !aiUserId) {
      setSubmittedQuery(undefined);
      setFilterError('至少填 session_id 或 ai_user_id');
      return;
    }

    const filters: ListTasksParams = {};
    if (sessionId) {
      filters.session_id = sessionId;
    }
    if (aiUserId) {
      filters.ai_user_id = aiUserId;
    }
    setFilterError(undefined);
    setSubmittedQuery((current) => ({
      filters,
      revision: (current?.revision ?? 0) + 1,
    }));
  };

  const columns: ColumnsType<AdminTaskView> = [
    { title: 'task_id', dataIndex: 'task_id', key: 'task_id' },
    { title: 'session_id', dataIndex: 'session_id', key: 'session_id' },
    { title: 'ai_user_id', dataIndex: 'ai_user_id', key: 'ai_user_id' },
    { title: 'status', dataIndex: 'status', key: 'status' },
    {
      title: 'capability_id',
      dataIndex: 'capability_id',
      key: 'capability_id',
      render: (value: AdminTaskView['capability_id']) => value ?? '-',
    },
    {
      title: 'error_code',
      dataIndex: 'error_code',
      key: 'error_code',
      render: (value: AdminTaskView['error_code']) => value ?? '-',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, task) => (
        <Button size="small" onClick={() => setSelectedTaskId(task.task_id)}>
          查看证据
        </Button>
      ),
    },
  ];

  const taskError = taskQuery.error ? errorText(taskQuery.error) : undefined;
  const eventError = eventQuery.error ? errorText(eventQuery.error) : undefined;

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Title level={3} style={{ marginBottom: 0 }}>
          Task 证据查看
        </Title>
        <Text type="secondary">按 session_id 或 ai_user_id 查询，只展示后端白名单证据。</Text>
      </div>

      <Form<TaskFilterValues> layout="inline" onFinish={submitFilters}>
        <Form.Item label="session_id" name="session_id">
          <Input allowClear />
        </Form.Item>
        <Form.Item label="ai_user_id" name="ai_user_id">
          <Input allowClear />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={taskQuery.isFetching}>
            查询
          </Button>
        </Form.Item>
      </Form>

      {filterError && <Alert type="info" showIcon message={filterError} />}
      {taskError && (
        <Alert type="error" showIcon message="Task 请求失败" description={taskError} />
      )}

      <Table<AdminTaskView>
        rowKey="task_id"
        columns={columns}
        dataSource={taskQuery.data?.items ?? []}
        loading={taskQuery.isLoading}
        pagination={false}
        scroll={{ x: 1100 }}
      />

      <Drawer
        title={`Task 证据：${selectedTaskId ?? ''}`}
        open={selectedTaskId !== undefined}
        onClose={() => setSelectedTaskId(undefined)}
        width={760}
        destroyOnHidden
      >
        {eventError && (
          <Alert type="error" showIcon message="Task 事件请求失败" description={eventError} />
        )}
        {eventQuery.isLoading ? (
          <Spin />
        ) : eventQuery.data?.items.length ? (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            {eventQuery.data.items.map((event) => {
              const visibleEvidence = evidenceFields.filter(
                (field) =>
                  Object.prototype.hasOwnProperty.call(event.evidence, field) &&
                  event.evidence[field] !== undefined,
              );
              return (
                <Descriptions
                  key={event.event_id}
                  column={1}
                  bordered
                  size="small"
                  title={event.event_type}
                >
                  <Descriptions.Item label="event_id">{event.event_id}</Descriptions.Item>
                  <Descriptions.Item label="event_type">{event.event_type}</Descriptions.Item>
                  <Descriptions.Item label="timestamp">{event.timestamp}</Descriptions.Item>
                  {visibleEvidence.map((field) => (
                    <Descriptions.Item key={field} label={field}>
                      {evidenceText(event.evidence[field])}
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              );
            })}
          </Space>
        ) : (
          !eventError && <Empty description="暂无 Task 事件" />
        )}
      </Drawer>
    </Space>
  );
}
