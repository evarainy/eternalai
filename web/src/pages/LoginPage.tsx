import { useState } from 'react';
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { ApiError } from '../api/mutator';
import { loginApiV1AuthLoginPost } from '../generated/auth/auth';
import type { LoginApiV1AuthLoginPostBody } from '../generated/auth/auth.schemas';
import { useAuthStore } from '../stores/authStore';
import { getReturnPath } from './loginNavigation';

const { Paragraph, Title } = Typography;
const LOGIN_FAILED_MESSAGE = '登录失败，请检查登录信息后重试。';
const LOGIN_UNAVAILABLE_MESSAGE = '认证服务暂时不可用，请稍后重试。';

export default function LoginPage() {
  const [form] = Form.useForm<LoginApiV1AuthLoginPostBody>();
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string>();
  const markAuthenticated = useAuthStore((state) => state.markAuthenticated);
  const queryClient = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();

  const handleLogin = async (credential: LoginApiV1AuthLoginPostBody) => {
    form.resetFields();
    setSubmitting(true);
    setErrorMessage(undefined);
    try {
      const response = await loginApiV1AuthLoginPost(credential);
      if (response.authenticated !== true) {
        setErrorMessage(LOGIN_FAILED_MESSAGE);
        return;
      }
      queryClient.clear();
      markAuthenticated();
      navigate(getReturnPath(location.state), { replace: true });
    } catch (error) {
      setErrorMessage(
        error instanceof ApiError && error.status === 401
          ? LOGIN_FAILED_MESSAGE
          : LOGIN_UNAVAILABLE_MESSAGE,
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{
        alignItems: 'center',
        display: 'flex',
        justifyContent: 'center',
        minHeight: 'calc(100vh - 160px)',
      }}
    >
      <Card style={{ maxWidth: 440, width: '100%' }}>
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          <div>
            <Title level={2} style={{ marginBottom: 8 }}>
              登录 EternalAI
            </Title>
            <Paragraph type="secondary" style={{ marginBottom: 0 }}>
              登录信息仅用于本次 OA 身份验证，提交后即从表单清除。
            </Paragraph>
          </div>
          {errorMessage ? <Alert title={errorMessage} type="error" showIcon /> : null}
          <Form<LoginApiV1AuthLoginPostBody>
            autoComplete="off"
            form={form}
            layout="vertical"
            onFinish={handleLogin}
          >
            <Form.Item
              label="OA 登录标识"
              name="loginid"
              rules={[{ message: '请输入 OA 登录标识。', required: true }]}
            >
              <Input.Password
                autoComplete="off"
                disabled={submitting}
                visibilityToggle={false}
              />
            </Form.Item>
            <Form.Item
              label="OA 密码"
              name="userpassword"
              rules={[{ message: '请输入 OA 密码。', required: true }]}
            >
              <Input.Password
                autoComplete="off"
                disabled={submitting}
                visibilityToggle={false}
              />
            </Form.Item>
            <Button block htmlType="submit" loading={submitting} type="primary">
              登录
            </Button>
          </Form>
        </Space>
      </Card>
    </div>
  );
}
