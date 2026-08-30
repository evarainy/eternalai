import { useEffect } from 'react';
import { ConfigProvider, App as AntApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import {
  QueryClient,
  QueryClientProvider,
  useQueryClient,
} from '@tanstack/react-query';
import {
  BrowserRouter,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom';
import { ApiError } from './api/mutator';
import { AppShell } from './app/AppShell';
import { workbenchTheme } from './app/theme';
import ChatPage from './pages/ChatPage';
import HealthPage from './pages/HealthPage';
import LoginPage from './pages/LoginPage';
import WorkObjectsPage from './pages/WorkObjectsPage';
import { getReturnPath } from './pages/loginNavigation';
import BindingsPage from './pages/admin/BindingsPage';
import RegistryPage from './pages/admin/RegistryPage';
import TasksPage from './pages/admin/TasksPage';
import { useAIDockStore } from './stores/aiDockStore';
import { useAuthStore } from './stores/authStore';

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
      useAIDockStore.getState().clearSession();
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

export default function App() {
  return (
    <ConfigProvider locale={zhCN} theme={workbenchTheme}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <AuthenticationEffects />
            <Routes>
              <Route path="/health" element={<HealthPage />} />
              <Route path="/login" element={<LoginRoute />} />
              <Route element={<ProtectedRoute />}>
                <Route element={<AppShell />}>
                  <Route path="/" element={<ChatPage />} />
                  <Route path="/chat" element={<Navigate replace to="/" />} />
                  <Route path="/work-objects" element={<WorkObjectsPage />} />
                  <Route path="/admin/registry" element={<RegistryPage />} />
                  <Route path="/admin/tasks" element={<TasksPage />} />
                  <Route path="/admin/bindings" element={<BindingsPage />} />
                </Route>
              </Route>
            </Routes>
          </BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}
