import { useState } from 'react';
import { Alert, Button, Form, Input } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import { useLocation, useNavigate } from 'react-router-dom';
import { ApiError } from '../api/mutator';
import { loginApiV1AuthLoginPost } from '../generated/auth/auth';
import type { LoginApiV1AuthLoginPostBody } from '../generated/auth/auth.schemas';
import { Icon } from '../shared/ui/Icon';
import { useAuthStore } from '../stores/authStore';
import { getReturnPath } from './loginNavigation';
import styles from './LoginPage.module.css';

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
    <div className={styles.screen}>
      <div className={styles.card}>
        <div className={styles.brand}>
          <span className={styles.brandMark}>
            <Icon name="bolt" size={26} strokeWidth={1.9} />
          </span>
          <span className={styles.brandText}>
            <span className={styles.brandName}>EternalAI</span>
            <small className={styles.brandSub}>办事工作台</small>
          </span>
        </div>

        <h1 className={styles.welcome}>欢迎回来</h1>
        <p className={styles.subtitle}>OA 里要你办的事，都在这儿。</p>

        {errorMessage ? (
          <Alert className={styles.alert} title={errorMessage} type="error" showIcon />
        ) : null}

        <Form<LoginApiV1AuthLoginPostBody>
          autoComplete="off"
          className={styles.form}
          form={form}
          layout="vertical"
          onFinish={handleLogin}
        >
          <Form.Item
            label="账号"
            name="loginid"
            rules={[{ message: '请输入账号。', required: true }]}
          >
            <Input autoComplete="off" disabled={submitting} />
          </Form.Item>
          <Form.Item
            label="密码"
            name="userpassword"
            rules={[{ message: '请输入密码。', required: true }]}
          >
            <Input.Password
              autoComplete="off"
              disabled={submitting}
              visibilityToggle={false}
            />
          </Form.Item>
          <Button
            block
            className={styles.submit}
            htmlType="submit"
            loading={submitting}
            type="primary"
          >
            登录
          </Button>
        </Form>

        <p className={styles.note}>登录信息只用于这一次 OA 验证，提交后立即清除。</p>
      </div>
    </div>
  );
}
