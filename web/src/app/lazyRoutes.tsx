import { lazy } from 'react';
import type { ComponentType } from 'react';

/**
 * 只在通过 ProtectedRoute 后才下载 Ant App 上下文和壳层。它持续包住 AppShell 的 Outlet，保持页面对
 * AntApp.useApp() 的既有使用方式，而未认证和启动确认态不会触发这个模块。
 */
const LazyAuthenticatedAppShell = lazy(() =>
  import('./AppShell').then(({ AuthenticatedAppShell }) => ({
    default: AuthenticatedAppShell,
  })),
);
const LazyAppsPage = lazy(() => import('../features/apps/AppsPage'));
const LazyMessagesPage = lazy(() => import('../features/messages/MessagesPage'));
const LazyWorkDispatchPage = lazy(() =>
  import('../features/work-dispatch/WorkDispatchPage'),
);
const LazyWorkObjectSearchPage = lazy(() =>
  import('../features/work-dispatch/WorkObjectSearchPage'),
);
const LazyBindingsPage = lazy(() => import('../pages/admin/BindingsPage'));
const LazyRegistryPage = lazy(() => import('../pages/admin/RegistryPage'));
const LazyTasksPage = lazy(() => import('../pages/admin/TasksPage'));
const LazyChatPage = lazy(() => import('../pages/ChatPage'));
const LazyHealthPage = lazy(() => import('../pages/HealthPage'));
const LazyLoginPage = lazy(() => import('../pages/LoginPage'));
const LazyWorkObjectsPage = lazy(() => import('../pages/WorkObjectsPage'));

export interface LazyRouteComponents {
  LazyAuthenticatedAppShell: ComponentType;
  LazyAppsPage: ComponentType;
  LazyBindingsPage: ComponentType;
  LazyChatPage: ComponentType;
  LazyHealthPage: ComponentType;
  LazyLoginPage: ComponentType;
  LazyMessagesPage: ComponentType;
  LazyRegistryPage: ComponentType;
  LazyTasksPage: ComponentType;
  LazyWorkDispatchPage: ComponentType;
  LazyWorkObjectSearchPage: ComponentType;
  LazyWorkObjectsPage: ComponentType;
}

/** The production route modules; tests may supply controlled loaders without changing route semantics. */
export const lazyRouteComponents: LazyRouteComponents = {
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
};
