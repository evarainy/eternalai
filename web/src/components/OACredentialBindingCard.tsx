import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Flex,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Tag,
  Typography,
} from 'antd';
import { ApiError } from '../api/mutator';
import {
  bindPasswordApiV1CredentialBindingsTargetSystemPut as bindPassword,
  getBindingApiV1CredentialBindingsTargetSystemGet as getBinding,
  unbindPasswordApiV1CredentialBindingsTargetSystemDelete as unbindPassword,
} from '../generated/credential-bindings/credential-bindings';
import type {
  BindPasswordApiV1CredentialBindingsTargetSystemPutBody,
  CredentialBindingView,
} from '../generated/credential-bindings/credential-bindings.schemas';
import { useAuthStore } from '../stores/authStore';

const { Paragraph, Text, Title } = Typography;

function bindingQueryKey(authGeneration: number) {
  return ['credential-binding', authGeneration, 'oa'] as const;
}

function safeErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.code}: ${error.message}`;
  }
  return 'credential_binding_unavailable: 凭证服务暂时不可用';
}

function statusPresentation(binding: CredentialBindingView | undefined) {
  switch (binding?.poll_status) {
    case 'active':
      return { color: 'green', label: '后台轮询已启用' } as const;
    case 'retrying':
      return { color: 'gold', label: '非认证类故障退避中' } as const;
    case 'invalid':
      return { color: 'red', label: '密码已失效，需重新绑定' } as const;
    case 'captcha_required':
      return { color: 'orange', label: 'OA 要求验证码，轮询已停止' } as const;
    default:
      return { color: 'default', label: '尚未绑定' } as const;
  }
}

export default function OACredentialBindingCard() {
  const [form] = Form.useForm<BindPasswordApiV1CredentialBindingsTargetSystemPutBody>();
  const [modalOpen, setModalOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [unbinding, setUnbinding] = useState(false);
  const [operationError, setOperationError] = useState<string>();
  const authGeneration = useAuthStore((state) => state.generation);
  const queryClient = useQueryClient();
  const { message } = AntApp.useApp();
  const queryKey = bindingQueryKey(authGeneration);
  const bindingQuery = useQuery({
    queryKey,
    queryFn: () => getBinding('oa'),
  });
  const binding = bindingQuery.data;
  const presentation = statusPresentation(binding);

  const closeModal = () => {
    form.resetFields();
    setOperationError(undefined);
    setModalOpen(false);
  };

  const handleBind = async (
    credential: BindPasswordApiV1CredentialBindingsTargetSystemPutBody,
  ) => {
    form.resetFields();
    setSubmitting(true);
    setOperationError(undefined);
    try {
      const updated = await bindPassword('oa', credential);
      queryClient.setQueryData(queryKey, updated);
      setModalOpen(false);
      void message.success(binding?.bound ? 'OA 密码已更新' : 'OA 密码已绑定');
    } catch (error) {
      setOperationError(safeErrorText(error));
    } finally {
      setSubmitting(false);
    }
  };

  const handleUnbind = async () => {
    setUnbinding(true);
    setOperationError(undefined);
    try {
      const updated = await unbindPassword('oa');
      queryClient.setQueryData(queryKey, updated);
      form.resetFields();
      void message.success('OA 密码绑定已解除');
    } catch (error) {
      void message.error(safeErrorText(error));
    } finally {
      setUnbinding(false);
    }
  };

  return (
    <Card
      styles={{ body: { padding: 24 } }}
      style={{ borderColor: '#b7d9d0', background: '#f7fffc' }}
    >
      <Flex align="center" justify="space-between" gap={20} wrap>
        <Space orientation="vertical" size={4}>
          <Text style={{ color: '#24695d', letterSpacing: 1.4 }}>OA CREDENTIAL</Text>
          <Space wrap>
            <Title level={4} style={{ margin: 0 }}>
              后台同步凭证
            </Title>
            <Tag color={presentation.color}>{presentation.label}</Tag>
          </Space>
          <Paragraph type="secondary" style={{ margin: 0, maxWidth: 720 }}>
            密码仅用于 OA 身份验证并加密保存。密码错误一次或出现验证码时，后台轮询会持久停止。
          </Paragraph>
          {binding?.poll_status === 'retrying' ? (
            <Text type="secondary">
              非认证类临时失败次数：{binding.poll_failure_count}（仅网络、超时、5xx 或响应格式异常）
            </Text>
          ) : null}
        </Space>
        <Space>
          {binding?.bound ? (
            <Popconfirm
              title="解除 OA 密码绑定？"
              description="解除后后台轮询立即停止。"
              okText="解除绑定"
              cancelText="取消"
              onConfirm={handleUnbind}
            >
              <Button danger loading={unbinding}>
                解除绑定
              </Button>
            </Popconfirm>
          ) : null}
          <Button
            type="primary"
            onClick={() => {
              setOperationError(undefined);
              setModalOpen(true);
            }}
          >
            {binding?.bound ? '重新绑定' : '绑定 OA 密码'}
          </Button>
        </Space>
      </Flex>

      {bindingQuery.error ? (
        <Alert
          showIcon
          type="error"
          style={{ marginTop: 16 }}
          title="无法读取 OA 凭证状态"
          description={safeErrorText(bindingQuery.error)}
        />
      ) : null}

      <Modal
        title={binding?.bound ? '重新绑定 OA 密码' : '绑定 OA 密码'}
        open={modalOpen}
        footer={null}
        destroyOnHidden
        mask={{ closable: !submitting }}
        onCancel={closeModal}
      >
        <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
          <Alert
            showIcon
            type="info"
            title="提交后立即从表单清除"
            description="如果 OA 拒绝这次验证，凭证不会保存。"
          />
          {operationError ? (
            <Alert showIcon type="error" title="绑定失败" description={operationError} />
          ) : null}
          <Form<BindPasswordApiV1CredentialBindingsTargetSystemPutBody>
            autoComplete="off"
            form={form}
            layout="vertical"
            onFinish={handleBind}
          >
            <Form.Item
              label="OA 登录标识"
              name="login_id"
              rules={[{ required: true, message: '请输入 OA 登录标识。' }]}
            >
              <Input.Password
                autoComplete="off"
                disabled={submitting}
                visibilityToggle={false}
              />
            </Form.Item>
            <Form.Item
              label="OA 密码"
              name="password"
              rules={[{ required: true, message: '请输入 OA 密码。' }]}
            >
              <Input.Password
                autoComplete="off"
                disabled={submitting}
                visibilityToggle={false}
              />
            </Form.Item>
            <Button block htmlType="submit" loading={submitting} type="primary">
              验证并保存
            </Button>
          </Form>
        </Space>
      </Modal>
    </Card>
  );
}
