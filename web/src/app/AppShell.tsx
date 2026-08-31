import { useEffect, useState } from 'react';
import { Button, Input } from 'antd';
import {
  Link,
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
} from 'react-router-dom';
import { useAIDockStore } from '../stores/aiDockStore';
import { useAuthStore } from '../stores/authStore';
import { AIDock } from './AIDock';
import styles from './AppShell.module.css';

const locationLabels: Record<string, string> = {
  '/': '开始新工作',
  '/search': '搜索工作事项',
  '/work-objects': '工作事项',
  '/admin/registry': '功能管理',
  '/admin/tasks': '任务证据',
  '/admin/bindings': '账号绑定',
};

function locationLabel(pathname: string): string {
  return locationLabels[pathname] ?? '当前位置';
}

function GlobalWorkObjectSearch({
  initialValue,
  onSubmit,
}: {
  initialValue: string;
  onSubmit: (value: string) => void;
}) {
  const [value, setValue] = useState(initialValue);

  return (
    <Input.Search
      allowClear
      aria-label="搜索工作事项"
      enterButton="搜索"
      onChange={(event) => setValue(event.target.value)}
      onSearch={onSubmit}
      placeholder="标题、来源编号或责任人"
      value={value}
    />
  );
}

function NavigationLink({
  icon,
  label,
  to,
}: {
  icon: string;
  label: string;
  to: string;
}) {
  return (
    <NavLink
      className={styles.navLink}
      to={to}
    >
      <span aria-hidden="true" className={styles.navIcon}>{icon}</span>
      <span>{label}</span>
    </NavLink>
  );
}

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const markUnauthenticated = useAuthStore((state) => state.markUnauthenticated);
  const mode = useAIDockStore((state) => state.mode);
  const openDock = useAIDockStore((state) => state.openDock);
  const clearPageContext = useAIDockStore((state) => state.clearPageContext);
  const isLandingPage = location.pathname === '/' || location.pathname === '/chat';
  const dockOffsetsContent = !isLandingPage && mode === 'pinned';
  const currentLocation = locationLabel(location.pathname);
  const initialSearchValue =
    location.pathname === '/search'
      ? new URLSearchParams(location.search).get('q') ?? ''
      : '';

  const submitSearch = (value: string) => {
    const term = value.trim();
    navigate({
      pathname: '/search',
      search: term.length === 0 ? '' : `?${new URLSearchParams({ q: term })}`,
    });
  };

  useEffect(() => {
    if (isLandingPage) {
      clearPageContext();
    }
  }, [clearPageContext, isLandingPage]);

  return (
    <div
      className={styles.shell}
      data-dock-offset={dockOffsetsContent ? 'true' : 'false'}
    >
      <aside className={styles.sidebar} aria-label="主导航">
        <Link className={styles.brand} to="/">
          EternalAI
          <small>办事工作台</small>
        </Link>
        <nav className={styles.nav} aria-label="工作区">
          <NavigationLink icon="▤" label="工作事项" to="/work-objects" />
          <details className={styles.adminGroup} open={location.pathname.startsWith('/admin/')}>
            <summary className={styles.adminSummary}>
              <span aria-hidden="true" className={styles.navIcon}>⚙</span>
              <span>管理页面</span>
            </summary>
            <div className={styles.adminLinks}>
              <NavigationLink icon="◆" label="功能管理" to="/admin/registry" />
              <NavigationLink icon="▣" label="任务证据" to="/admin/tasks" />
              <NavigationLink icon="●" label="账号绑定" to="/admin/bindings" />
            </div>
          </details>
        </nav>
        <p className={styles.sidebarNote}>页面会明确告诉你当前位置、当前状态和下一步。</p>
      </aside>

      <div className={styles.stage} data-testid="app-main">
        <header className={styles.topbar}>
          <div className={styles.location} aria-live="polite">
            <span className={styles.locationLabel}>当前位置</span>
            <strong>{currentLocation}</strong>
          </div>
          <div className={styles.topbarSearch}>
            <GlobalWorkObjectSearch
              key={`${location.pathname}:${location.search}`}
              initialValue={initialSearchValue}
              onSubmit={submitSearch}
            />
          </div>
          <div className={styles.topbarActions}>
            {!isLandingPage && mode === 'closed' ? (
              <Button type="primary" onClick={openDock}>打开 AI 助手</Button>
            ) : null}
            <Button onClick={() => markUnauthenticated()}>退出登录（本地）</Button>
          </div>
        </header>
        <main className={styles.content} id="main-content">
          <Outlet />
        </main>
      </div>

      <AIDock suppressed={isLandingPage} />
    </div>
  );
}
