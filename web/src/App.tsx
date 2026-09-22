import { useEffect } from 'react';
import type { ComponentType } from 'react';
import ConfigProvider from 'antd/es/config-provider';
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
import { BootGate } from './app/BootGate';
import { useIdentityBootstrap, useIdentityRevalidation } from './app/identity';
import {
  lazyRouteComponents,
} from './app/lazyRoutes';
import type { LazyRouteComponents } from './app/lazyRoutes';
import { RouteLoadBoundary } from './app/RouteLoadingFallback';
import { WORKBENCH_BUTTON_CONFIG, workbenchTheme } from './app/theme';
import { getReturnPath } from './pages/loginNavigation';
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

  // 刷新后向后端确认一次会话。恢复登录态以**后端确认**为准，不看任何客户端保存的状态。
  useIdentityBootstrap();
  const revalidationFailed = useIdentityRevalidation();

  useEffect(() => {
    const clearIdentityState = () => {
      // Remove synchronously: a later login must never be cleared by this promise.
      void activeQueryClient.cancelQueries();
      activeQueryClient.clear();
      useAIDockStore.getState().clearSession();
    };
    if (useAuthStore.getState().status === 'unauthenticated') clearIdentityState();
    return useAuthStore.subscribe((state, previous) => {
      if (state.status === 'unauthenticated' && previous.status !== 'unauthenticated') {
        clearIdentityState();
      }
    });
  }, [activeQueryClient]);

  return status === 'authenticated' && revalidationFailed
    ? <div role="alert">暂时无法确认登录状态</div>
    : null;
}

export function ProtectedRoute() {
  const status = useAuthStore((state) => state.status);
  const location = useLocation();

  // 还没问过后端时既不放行也不重定向——放行等于自称已登录，重定向就是刷新掉登录态那个 bug。
  if (status === 'unknown') {
    return <BootGate />;
  }

  if (status !== 'authenticated') {
    return (
      <Navigate
        replace
        state={{ from: `${location.pathname}${location.search}` }}
        to="/login"
      />
    );
  }

  return <Outlet />;
}

interface LoginRouteProps {
  LoginPageComponent?: ComponentType;
}

export function LoginRoute({
  LoginPageComponent = lazyRouteComponents.LazyLoginPage,
}: LoginRouteProps) {
  const status = useAuthStore((state) => state.status);
  const location = useLocation();

  // 否则已登录用户刷新时会先闪一下登录表单。
  if (status === 'unknown') {
    return <BootGate />;
  }

  return status === 'authenticated' ? (
    <Navigate replace to={getReturnPath(location.state)} />
  ) : (
    <LoginPageComponent />
  );
}

interface AppProps {
  routes?: LazyRouteComponents;
}

export default function App({ routes = lazyRouteComponents }: AppProps) {
  const {
    LazyAuthenticatedAppShell,
    LazyAppsPage,
    LazyBindingsPage,
    LazyChatPage,
    LazyHealthPage,
    LazyLoginPage,
    LazyMessagesPage,
    LazyRegistryPage,
    LazyTasksPage,
    LazyWorkDispatchPage,
    LazyWorkObjectSearchPage,
    LazyWorkObjectsPage,
  } = routes;
  return (
    <ConfigProvider
      button={WORKBENCH_BUTTON_CONFIG}
      locale={zhCN}
      theme={workbenchTheme}
    >
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthenticationEffects />
          <Routes>
            <Route
              path="/health"
              element={(
                <RouteLoadBoundary label="正在打开健康检查">
                  <LazyHealthPage />
                </RouteLoadBoundary>
              )}
            />
            <Route
              path="/login"
              element={(
                <RouteLoadBoundary label="正在打开登录页">
                  <LoginRoute LoginPageComponent={LazyLoginPage} />
                </RouteLoadBoundary>
              )}
            />
            <Route element={<ProtectedRoute />}>
              <Route path="/" element={<Navigate replace to="/chat" />} />
              <Route
                element={(
                  <RouteLoadBoundary label="正在准备工作台" surface="workspace">
                    <LazyAuthenticatedAppShell />
                  </RouteLoadBoundary>
                )}
              >
                <Route
                  path="/chat"
                  element={(
                    <RouteLoadBoundary label="正在打开页面">
                      <LazyChatPage />
                    </RouteLoadBoundary>
                  )}
                />
                <Route
                  path="/search"
                  element={(
                    <RouteLoadBoundary label="正在打开页面">
                      <LazyWorkObjectSearchPage />
                    </RouteLoadBoundary>
                  )}
                />
                <Route
                  path="/work-objects"
                  element={(
                    <RouteLoadBoundary label="正在打开页面">
                      <LazyWorkObjectsPage />
                    </RouteLoadBoundary>
                  )}
                />
                <Route
                  path="/work-dispatch"
                  element={(
                    <RouteLoadBoundary label="正在打开页面">
                      <LazyWorkDispatchPage />
                    </RouteLoadBoundary>
                  )}
                />
                <Route
                  path="/apps"
                  element={(
                    <RouteLoadBoundary label="正在打开页面">
                      <LazyAppsPage />
                    </RouteLoadBoundary>
                  )}
                />
                <Route
                  path="/messages"
                  element={(
                    <RouteLoadBoundary label="正在打开页面">
                      <LazyMessagesPage />
                    </RouteLoadBoundary>
                  )}
                />
                <Route
                  path="/admin/registry"
                  element={(
                    <RouteLoadBoundary label="正在打开页面">
                      <LazyRegistryPage />
                    </RouteLoadBoundary>
                  )}
                />
                <Route
                  path="/admin/tasks"
                  element={(
                    <RouteLoadBoundary label="正在打开页面">
                      <LazyTasksPage />
                    </RouteLoadBoundary>
                  )}
                />
                <Route
                  path="/admin/bindings"
                  element={(
                    <RouteLoadBoundary label="正在打开页面">
                      <LazyBindingsPage />
                    </RouteLoadBoundary>
                  )}
                />
              </Route>
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ConfigProvider>
  );
}
