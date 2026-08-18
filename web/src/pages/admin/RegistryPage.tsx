import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App as AntApp,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { Rule } from 'antd/es/form';
import { ApiError } from '../../api/mutator';
import {
  createCapability,
  disableCapability,
  enableCapability,
  listRegistry,
} from '../../generated/admin/admin';
import {
  CapabilityExecutionIdentity,
  CapabilityRiskLevel,
  CapabilityTargetSystem,
  CapabilityType,
} from '../../generated/admin/admin.schemas';
import type {
  AdminCapabilityCreate,
  AdminCapabilityView,
} from '../../generated/admin/admin.schemas';
import {
  normalizeIntentTags,
  normalizePromptSafeText,
  promptSafeLimits,
} from './registryValidation';

const { Title, Text } = Typography;
const { TextArea } = Input;
const registryQueryKey = ['admin', 'registry'] as const;

interface CreateFormValues {
  capability_id: string;
  name: string;
  type: AdminCapabilityCreate['type'];
  intent_tags?: string[];
  input_schema: string;
  output_schema: string;
  input_schema_digest: string;
  output_schema_digest: string;
  risk_level: AdminCapabilityCreate['risk_level'];
  owner: string;
  version: string;
  short_description: string;
  target_system?: AdminCapabilityCreate['target_system'];
  execution_identity: AdminCapabilityCreate['execution_identity'];
  binding_required: boolean;
  policy_digest?: string;
}

function selectOptions(values: Record<string, string>) {
  return Object.values(values).map((value) => ({ label: value, value }));
}

function parseJsonObject(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('必须是 JSON object');
  }
  return parsed as Record<string, unknown>;
}

function promptSafeTextRules(label: string, maxLength: number): Rule[] {
  return [
    { required: true, message: `${label} 必填` },
    {
      validator: async (_, value: unknown) => {
        if (typeof value === 'string') {
          normalizePromptSafeText(value, label, maxLength);
        }
      },
    },
  ];
}

const intentTagRules: Rule[] = [
  {
    validator: async (_, value: unknown) => {
      if (value === undefined) {
        return;
      }
      if (!Array.isArray(value) || !value.every((item) => typeof item === 'string')) {
        throw new Error('Intent Tags 格式无效');
      }
      normalizeIntentTags(value);
    },
  },
];

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.code}: ${error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'unknown_error: 请求失败';
}

export default function RegistryPage() {
  const [form] = Form.useForm<CreateFormValues>();
  const [createOpen, setCreateOpen] = useState(false);
  const [actionError, setActionError] = useState<string>();
  const queryClient = useQueryClient();
  const { message } = AntApp.useApp();

  const registryQuery = useQuery({
    queryKey: registryQueryKey,
    queryFn: listRegistry,
  });

  const reportError = (error: unknown) => {
    const text = errorText(error);
    setActionError(text);
    void message.error(text);
  };

  const refreshRegistry = async () => {
    setActionError(undefined);
    await queryClient.invalidateQueries({ queryKey: registryQueryKey });
  };

  const createMutation = useMutation({
    mutationFn: (payload: AdminCapabilityCreate) => createCapability(payload),
    onSuccess: async () => {
      setCreateOpen(false);
      form.resetFields();
      await refreshRegistry();
    },
    onError: reportError,
  });

  const enableMutation = useMutation({
    mutationFn: (capabilityId: string) => enableCapability(capabilityId),
    onSuccess: refreshRegistry,
    onError: reportError,
  });

  const disableMutation = useMutation({
    mutationFn: (capabilityId: string) => disableCapability(capabilityId),
    onSuccess: refreshRegistry,
    onError: reportError,
  });

  const columns: ColumnsType<AdminCapabilityView> = [
    { title: 'Capability ID', dataIndex: 'capability_id', key: 'capability_id' },
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '类型', dataIndex: 'type', key: 'type' },
    { title: '状态', dataIndex: 'status', key: 'status' },
    { title: '风险', dataIndex: 'risk_level', key: 'risk_level' },
    {
      title: '目标系统',
      dataIndex: 'target_system',
      key: 'target_system',
      render: (value: AdminCapabilityView['target_system']) => value ?? '-',
    },
    {
      title: '执行身份',
      dataIndex: 'execution_identity',
      key: 'execution_identity',
    },
    { title: 'Owner', dataIndex: 'owner', key: 'owner' },
    { title: '版本', dataIndex: 'version', key: 'version' },
    {
      title: '操作',
      key: 'actions',
      fixed: 'right',
      render: (_, capability) =>
        capability.status === 'active' ? (
          <Button
            size="small"
            loading={
              disableMutation.isPending && disableMutation.variables === capability.capability_id
            }
            onClick={() => disableMutation.mutate(capability.capability_id)}
          >
            停用
          </Button>
        ) : (
          <Button
            size="small"
            type="primary"
            loading={
              enableMutation.isPending && enableMutation.variables === capability.capability_id
            }
            onClick={() => enableMutation.mutate(capability.capability_id)}
          >
            启用
          </Button>
        ),
    },
  ];

  const submitCreate = (values: CreateFormValues) => {
    const payload: AdminCapabilityCreate = {
      capability_id: values.capability_id,
      name: normalizePromptSafeText(values.name, '名称', promptSafeLimits.name),
      type: values.type,
      intent_tags: normalizeIntentTags(values.intent_tags ?? []),
      input_schema: parseJsonObject(values.input_schema),
      output_schema: parseJsonObject(values.output_schema),
      input_schema_digest: values.input_schema_digest,
      output_schema_digest: values.output_schema_digest,
      risk_level: values.risk_level,
      owner: normalizePromptSafeText(values.owner, 'Owner', promptSafeLimits.owner),
      version: values.version,
      short_description: normalizePromptSafeText(
        values.short_description,
        '简短描述',
        promptSafeLimits.short_description,
      ),
      target_system: values.target_system ?? null,
      execution_identity: values.execution_identity,
      binding_required: values.binding_required,
      policy_digest: values.policy_digest?.trim() || null,
    };
    createMutation.mutate(payload);
  };

  const queryError = registryQuery.error ? errorText(registryQuery.error) : undefined;

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }}>
        <div>
          <Title level={3} style={{ marginBottom: 0 }}>
            Registry 管理
          </Title>
          <Text type="secondary">新建能力始终由后端创建为 draft。</Text>
        </div>
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          新建能力
        </Button>
      </Space>

      {(actionError ?? queryError) && (
        <Alert
          type="error"
          showIcon
          title="Registry 请求失败"
          description={actionError ?? queryError}
        />
      )}

      <Table<AdminCapabilityView>
        rowKey="capability_id"
        columns={columns}
        dataSource={registryQuery.data?.items ?? []}
        loading={registryQuery.isLoading}
        pagination={false}
        scroll={{ x: 1200 }}
      />

      <Modal
        title="新建 Registry 能力"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnHidden
        width={760}
      >
        <Form<CreateFormValues>
          form={form}
          layout="vertical"
          initialValues={{
            type: 'query',
            intent_tags: [],
            input_schema: '{}',
            output_schema: '{}',
            risk_level: 'low',
            execution_identity: 'user_delegated',
            binding_required: false,
          }}
          onFinish={submitCreate}
        >
          <Form.Item label="Capability ID" name="capability_id" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            label="名称"
            name="name"
            rules={promptSafeTextRules('名称', promptSafeLimits.name)}
          >
            <Input />
          </Form.Item>
          <Form.Item label="类型" name="type" rules={[{ required: true }]}>
            <Select options={selectOptions(CapabilityType)} />
          </Form.Item>
          <Form.Item label="Intent Tags" name="intent_tags" rules={intentTagRules}>
            <Select mode="tags" tokenSeparators={[',']} />
          </Form.Item>
          <Form.Item
            label="Input Schema (JSON object)"
            name="input_schema"
            rules={[
              { required: true },
              {
                validator: async (_, value: string) => {
                  parseJsonObject(value);
                },
              },
            ]}
          >
            <TextArea rows={3} />
          </Form.Item>
          <Form.Item
            label="Output Schema (JSON object)"
            name="output_schema"
            rules={[
              { required: true },
              {
                validator: async (_, value: string) => {
                  parseJsonObject(value);
                },
              },
            ]}
          >
            <TextArea rows={3} />
          </Form.Item>
          <Form.Item
            label="Input Schema Digest"
            name="input_schema_digest"
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            label="Output Schema Digest"
            name="output_schema_digest"
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item label="风险等级" name="risk_level" rules={[{ required: true }]}>
            <Select options={selectOptions(CapabilityRiskLevel)} />
          </Form.Item>
          <Form.Item
            label="Owner"
            name="owner"
            rules={promptSafeTextRules('Owner', promptSafeLimits.owner)}
          >
            <Input />
          </Form.Item>
          <Form.Item label="版本" name="version" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            label="简短描述"
            name="short_description"
            rules={promptSafeTextRules(
              '简短描述',
              promptSafeLimits.short_description,
            )}
          >
            <TextArea rows={2} />
          </Form.Item>
          <Form.Item label="目标系统" name="target_system">
            <Select allowClear options={selectOptions(CapabilityTargetSystem)} />
          </Form.Item>
          <Form.Item
            label="执行身份"
            name="execution_identity"
            rules={[{ required: true }]}
          >
            <Select options={selectOptions(CapabilityExecutionIdentity)} />
          </Form.Item>
          <Form.Item label="需要绑定" name="binding_required" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Form.Item label="Policy Digest" name="policy_digest">
            <Input />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={createMutation.isPending}>
            创建 draft
          </Button>
        </Form>
      </Modal>
    </Space>
  );
}
