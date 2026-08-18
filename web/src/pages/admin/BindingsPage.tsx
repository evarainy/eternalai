import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  Alert,
  App as AntApp,
  Button,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ApiError } from '../../api/mutator';
import {
  listBindings,
  resetBinding,
  revokeBinding,
} from '../../generated/admin/admin';
import { TargetSystem } from '../../generated/admin/admin.schemas';
import type {
  AdminBindingView,
  ListBindingsParams,
} from '../../generated/admin/admin.schemas';

const { Title, Text } = Typography;

interface BindingFilterValues {
  ai_user_id?: string;
  target_system?: ListBindingsParams['target_system'];
  binding_scope?: string;
  account_set_id?: string;
  device_domain_id?: string;
}

interface SubmittedBindingQuery {
  filters: ListBindingsParams;
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

const targetSystemOptions = Object.values(TargetSystem).map((value) => ({
  label: value,
  value,
}));

export default function BindingsPage() {
  const [submittedQuery, setSubmittedQuery] = useState<SubmittedBindingQuery>();
  const [filterError, setFilterError] = useState<string>();
  const [actionError, setActionError] = useState<string>();
  const { message } = AntApp.useApp();

  const bindingQuery = useQuery({
    queryKey: ['admin', 'bindings', submittedQuery?.filters, submittedQuery?.revision],
    queryFn: () => listBindings(submittedQuery?.filters),
    enabled: submittedQuery !== undefined,
  });

  const reportMutationError = (error: unknown) => {
    const text = errorText(error);
    setActionError(text);
    void message.error(text);
  };

  const refreshBindings = async () => {
    setActionError(undefined);
    await bindingQuery.refetch();
  };

  const revokeMutation = useMutation({
    mutationFn: (bindingId: string) => revokeBinding(bindingId),
    onSuccess: async () => {
      await refreshBindings();
      void message.success('Binding 已撤销');
    },
    onError: reportMutationError,
  });

  const resetMutation = useMutation({
    mutationFn: (bindingId: string) => resetBinding(bindingId),
    onSuccess: async () => {
      await refreshBindings();
      void message.success('Binding 已重置，请重新认证');
    },
    onError: reportMutationError,
  });

  const submitFilters = (values: BindingFilterValues) => {
    const aiUserId = values.ai_user_id?.trim();
    if (!aiUserId) {
      setSubmittedQuery(undefined);
      setFilterError('ai_user_id 必填');
      return;
    }

    const filters: ListBindingsParams = { ai_user_id: aiUserId };
    if (values.target_system) {
      filters.target_system = values.target_system;
    }
    const bindingScope = values.binding_scope?.trim();
    const accountSetId = values.account_set_id?.trim();
    const deviceDomainId = values.device_domain_id?.trim();
    if (bindingScope) {
      filters.binding_scope = bindingScope;
    }
    if (accountSetId) {
      filters.account_set_id = accountSetId;
    }
    if (deviceDomainId) {
      filters.device_domain_id = deviceDomainId;
    }

    setFilterError(undefined);
    setActionError(undefined);
    setSubmittedQuery((current) => ({
      filters,
      revision: (current?.revision ?? 0) + 1,
    }));
  };

  const columns: ColumnsType<AdminBindingView> = [
    {
      title: 'binding_id',
      dataIndex: 'binding_id',
      key: 'binding_id',
      render: (value: AdminBindingView['binding_id']) => value ?? '-',
    },
    { title: 'target_system', dataIndex: 'target_system', key: 'target_system' },
    {
      title: 'execution_identity',
      dataIndex: 'execution_identity',
      key: 'execution_identity',
    },
    { title: 'bind_status', dataIndex: 'bind_status', key: 'bind_status' },
    {
      title: 'binding_scope',
      dataIndex: 'binding_scope',
      key: 'binding_scope',
      render: (value: AdminBindingView['binding_scope']) => value ?? '-',
    },
    {
      title: 'account_set_id',
      dataIndex: 'account_set_id',
      key: 'account_set_id',
      render: (value: AdminBindingView['account_set_id']) => value ?? '-',
    },
    {
      title: 'device_domain_id',
      dataIndex: 'device_domain_id',
      key: 'device_domain_id',
      render: (value: AdminBindingView['device_domain_id']) => value ?? '-',
    },
    {
      title: 'reason_code',
      dataIndex: 'reason_code',
      key: 'reason_code',
      render: (value: AdminBindingView['reason_code']) => value ?? '-',
    },
    {
      title: '操作',
      key: 'actions',
      fixed: 'right',
      render: (_, binding) => {
        const bindingId = binding.binding_id;
        const mutationPending = revokeMutation.isPending || resetMutation.isPending;
        return (
          <Space>
            <Popconfirm
              title="确认撤销此 Binding？"
              description="撤销后，后续调用将拒绝使用该身份绑定。"
              okText="确认撤销"
              cancelText="取消"
              disabled={!bindingId}
              onConfirm={() => {
                if (bindingId) {
                  revokeMutation.mutate(bindingId);
                }
              }}
            >
              <Button
                danger
                size="small"
                disabled={!bindingId || mutationPending}
                loading={revokeMutation.isPending && revokeMutation.variables === bindingId}
              >
                撤销
              </Button>
            </Popconfirm>
            <Popconfirm
              title="确认重置此 Binding？"
              description="重置会撤销当前绑定，并要求用户重新认证。"
              okText="确认重置"
              cancelText="取消"
              disabled={!bindingId}
              onConfirm={() => {
                if (bindingId) {
                  resetMutation.mutate(bindingId);
                }
              }}
            >
              <Button
                size="small"
                disabled={!bindingId || mutationPending}
                loading={resetMutation.isPending && resetMutation.variables === bindingId}
              >
                重置
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  const queryError = bindingQuery.error ? errorText(bindingQuery.error) : undefined;

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Title level={3} style={{ marginBottom: 0 }}>
          Binding 查看
        </Title>
        <Text type="secondary">查询身份绑定，并对有 binding_id 的记录执行撤销或重置。</Text>
      </div>

      <Form<BindingFilterValues> layout="inline" onFinish={submitFilters}>
        <Form.Item label="ai_user_id" name="ai_user_id">
          <Input allowClear />
        </Form.Item>
        <Form.Item label="target_system" name="target_system">
          <Select allowClear options={targetSystemOptions} style={{ width: 170 }} />
        </Form.Item>
        <Form.Item label="binding_scope" name="binding_scope">
          <Input allowClear />
        </Form.Item>
        <Form.Item label="account_set_id" name="account_set_id">
          <Input allowClear />
        </Form.Item>
        <Form.Item label="device_domain_id" name="device_domain_id">
          <Input allowClear />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={bindingQuery.isFetching}>
            查询
          </Button>
        </Form.Item>
      </Form>

      {filterError && <Alert type="info" showIcon title={filterError} />}
      {queryError && (
        <Alert type="error" showIcon title="Binding 请求失败" description={queryError} />
      )}
      {actionError && (
        <Alert type="error" showIcon title="Binding 操作失败" description={actionError} />
      )}

      {bindingQuery.data && (
        <Title level={4} style={{ marginBottom: 0 }}>
          绑定：{bindingQuery.data.ai_user_id}
        </Title>
      )}
      <Table<AdminBindingView>
        rowKey={(binding) =>
          binding.binding_id ??
          [
            binding.target_system,
            binding.execution_identity,
            binding.binding_scope,
            binding.account_set_id,
            binding.device_domain_id,
          ].join(':')
        }
        columns={columns}
        dataSource={bindingQuery.data?.items ?? []}
        loading={bindingQuery.isLoading}
        pagination={false}
        scroll={{ x: 1450 }}
      />
    </Space>
  );
}
