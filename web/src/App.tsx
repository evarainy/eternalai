import { useEffect } from 'react';
import { ConfigProvider, App as AntApp, Button, Flex, Layout, Space } from 'antd';
import {
  QueryClient,
  QueryClientProvider,
  useQueryClient,
} from '@tanstack/react-query';
import {
  BrowserRouter,
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom';
import { ApiError } from './api/mutator';
import HealthPage from './pages/HealthPage';
import LoginPage from './pages/LoginPage';
import { getReturnPath } from './pages/loginNavigation';
import BindingsPage from './pages/admin/BindingsPage';
import RegistryPage from './pages/admin/RegistryPage';
import TasksPage from './pages/admin/TasksPage';
import { useAuthStore } from './stores/authStore';

const { Header, Content } = Layout;
const queryClient = new QueryClient({
  defaultOptions: {
    mutations: {
      retry: false,
    },
    queries: {
      retry: (failureCount, error) =>
        error instanceof ApiError && error.status === 401 ? false : failureCount < 3,
    },
  },
});

export function AuthenticationEffects() {
  const status = useAuthStore((state) => state.status);
  const activeQueryClient = useQueryClient();

  useEffect(() => {
    if (status === 'unauthenticated') {
      activeQueryClient.clear();
    }
  }, [activeQueryClient, status]);

  return null;
}

export function ProtectedRoute() {
  const status = useAuthStore((state) => state.status);
  const location = useLocation();

  if (status !== 'authenticated') {
    return (
      <Navigate
        replace
        state={{ from: location.pathname }}
        to="/login"
      />
    );
  }

  return <Outlet />;
}

export function LoginRoute() {
  const status = useAuthStore((state) => state.status);
  const location = useLocation();

  return status === 'authenticated' ? (
    <Navigate replace to={getReturnPath(location.state)} />
  ) : (
    <LoginPage />
  );
}

function AppShell() {
  const status = useAuthStore((state) => state.status);
  const markUnauthenticated = useAuthStore((state) => state.markUnauthenticated);

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ height: 'auto', paddingBlock: 12 }}>
        <Flex align="center" justify="space-between" gap={24} wrap>
          <Space size="large">
            <Link to="/health" style={{ color: '#fff' }}>
              EternalAI
            </Link>
            {status === 'authenticated' ? (
              <>
                    <Link to="/admin/registry" style={{ color: '#fff' }}>
                      Registry 管理
                    </Link>
                    <Link to="/admin/tasks" style={{ color: '#fff' }}>
                      Task 证据
                    </Link>
                    <Link to="/admin/bindings" style={{ color: '#fff' }}>
                      Binding 查看
                    </Link>
              </>
            ) : null}
          </Space>
          {status === 'authenticated' ? (
            <Button onClick={() => markUnauthenticated()}>
              退出登录（本地）
            </Button>
          ) : (
            <Link to="/login" style={{ color: '#fff' }}>
              登录
            </Link>
          )}
        </Flex>
      </Header>
      <Content style={{ padding: 24 }}>
        <Routes>
          <Route path="/" element={<HealthPage />} />
          <Route path="/health" element={<HealthPage />} />
          <Route path="/login" element={<LoginRoute />} />
          <Route element={<ProtectedRoute />}>
            <Route path="/admin/registry" element={<RegistryPage />} />
            <Route path="/admin/tasks" element={<TasksPage />} />
            <Route path="/admin/bindings" element={<BindingsPage />} />
          </Route>
        </Routes>
      </Content>
    </Layout>
  );
}

export default function App() {
  return (
    <ConfigProvider>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <AuthenticationEffects />
            <AppShell />
          </BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}
