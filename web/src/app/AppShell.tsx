import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Input } from 'antd';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { getBindingApiV1CredentialBindingsTargetSystemGet as getBinding } from '../generated/credential-bindings/credential-bindings';
import type { CredentialBindingView } from '../generated/credential-bindings/credential-bindings.schemas';
import { useAIDockStore } from '../stores/aiDockStore';
import { useAuthStore } from '../stores/authStore';
import { useNavigationStore } from '../stores/navigationStore';
import { AIDock } from './AIDock';
import {
  IDENTITY_UNAVAILABLE_NEXT_STEP,
  IDENTITY_UNAVAILABLE_STATEMENT,
  SIDEBAR_COLLAPSED_PADDING,
  SIDEBAR_COLLAPSED_WIDTH,
  SIDEBAR_EXPANDED_WIDTH,
  TOPBAR_AVATAR_WIDTH,
  TOPBAR_GAP,
  TOPBAR_HORIZONTAL_PADDING,
  TOPBAR_IDENTITY_WIDTH,
  TOPBAR_NOTIFICATIONS_WIDTH,
  TOPBAR_SEARCH_WIDTH,
  TOPBAR_STYLE_WIDTH,
  TOPBAR_SYSTEM_STATUS_WIDTH,
} from './shellLayout';
import styles from './AppShell.module.css';

const AI_ASSISTANT_PATH = '/chat';
const BINDINGS_PATH = '/admin/bindings';
const WORK_OBJECTS_PATH = '/work-objects';

interface PrimaryNavigationItem {
  icon: string;
  label: string;
  to: string;
}

/** 2026-09-02 裁决：一级导航五项平铺，顺序与路径固定。 */
const PRIMARY_NAVIGATION: readonly PrimaryNavigationItem[] = [
  { icon: '✦', label: 'AI 助手', to: AI_ASSISTANT_PATH },
  { icon: '▤', label: '工作事项', to: WORK_OBJECTS_PATH },
  { icon: '✎', label: '任务交办', to: '/work-dispatch' },
  { icon: '▦', label: '软件中心', to: '/apps' },
  { icon: '✉', label: '消息', to: '/messages' },
];

const ADMIN_NAVIGATION: readonly PrimaryNavigationItem[] = [
  { icon: '◆', label: '功能管理', to: '/admin/registry' },
  { icon: '▣', label: '任务证据', to: '/admin/tasks' },
  { icon: '●', label: '账号绑定', to: BINDINGS_PATH },
];

type TopbarPanel = 'style' | 'system-status' | 'notifications';

interface ShellCssVariables extends CSSProperties {
  '--shell-sidebar-width': string;
  '--shell-sidebar-collapsed-padding': string;
  '--topbar-avatar-width': string;
  '--topbar-gap': string;
  '--topbar-identity-width': string;
  '--topbar-notifications-width': string;
  '--topbar-padding': string;
  '--topbar-search-width': string;
  '--topbar-style-width': string;
  '--topbar-system-status-width': string;
}

function shellCssVariables(collapsed: boolean): ShellCssVariables {
  return {
    '--shell-sidebar-width': `${
      collapsed ? SIDEBAR_COLLAPSED_WIDTH : SIDEBAR_EXPANDED_WIDTH
    }px`,
    '--shell-sidebar-collapsed-padding': `${SIDEBAR_COLLAPSED_PADDING}px`,
    '--topbar-avatar-width': `${TOPBAR_AVATAR_WIDTH}px`,
    '--topbar-gap': `${TOPBAR_GAP}px`,
    '--topbar-identity-width': `${TOPBAR_IDENTITY_WIDTH}px`,
    '--topbar-notifications-width': `${TOPBAR_NOTIFICATIONS_WIDTH}px`,
    '--topbar-padding': `${TOPBAR_HORIZONTAL_PADDING}px`,
    '--topbar-search-width': `${TOPBAR_SEARCH_WIDTH}px`,
    '--topbar-style-width': `${TOPBAR_STYLE_WIDTH}px`,
    '--topbar-system-status-width': `${TOPBAR_SYSTEM_STATUS_WIDTH}px`,
  };
}

type SystemStatusKind = 'unknown' | 'unbound' | 'attention' | 'retrying' | 'normal';

interface SystemStatusRow {
  kind: SystemStatusKind;
  marker: string;
  name: string;
  statusText: string;
  nextStep: string;
}

/**
 * 系统状态只能回答「目标系统凭证是否可用」。`credential-bindings` 只有按 `target_system` 单查的
 * GET，现役闭集里只有 `oa`，因此本面板只列 OA 一行；「AI 助手」「工作台自己的数据」两行的判定来源
 * 尚未落盘，属活欠债，这里不编造。
 */
function oaSystemStatusRow(
  binding: CredentialBindingView | undefined,
  unavailable: boolean,
): SystemStatusRow {
  if (unavailable || binding === undefined) {
    return {
      kind: 'unknown',
      marker: '？',
      name: 'OA 系统',
      statusText: '暂时不知道',
      nextStep: '下一步：刷新本页；仍然取不到时请联系管理员。在弄清楚之前，不要当成正常。',
    };
  }
  if (binding.poll_status === 'invalid') {
    return {
      kind: 'attention',
      marker: '×',
      name: 'OA 系统',
      statusText: '密码已失效，后台同步已停止',
      nextStep: '下一步：去「账号绑定」重新绑定 OA 密码。',
    };
  }
  if (binding.poll_status === 'captcha_required') {
    return {
      kind: 'attention',
      marker: '×',
      name: 'OA 系统',
      statusText: 'OA 要求输入验证码，后台同步已停止',
      nextStep: '下一步：去「账号绑定」重新绑定 OA 密码。',
    };
  }
  if (!binding.bound) {
    return {
      kind: 'unbound',
      marker: '—',
      name: 'OA 系统',
      statusText: '尚未绑定',
      nextStep: '下一步：去「账号绑定」绑定 OA 密码后才能后台同步。',
    };
  }
  if (binding.poll_status === 'retrying') {
    return {
      kind: 'retrying',
      marker: '!',
      name: 'OA 系统',
      statusText: '正在重试（不是密码问题）',
      nextStep: '下一步：先照常办事；持续不恢复时请联系管理员。',
    };
  }
  return {
    kind: 'normal',
    marker: '√',
    name: 'OA 系统',
    statusText: '正常',
    nextStep: '下一步：不需要处理。',
  };
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
      placeholder="搜索工作事项、文件编号、责任人"
      value={value}
    />
  );
}

function NavigationLink({
  collapsed,
  icon,
  label,
  to,
}: {
  collapsed: boolean;
  icon: string;
  label: string;
  to: string;
}) {
  return (
    <NavLink aria-label={label} className={styles.navLink} title={label} to={to}>
      <span aria-hidden="true" className={styles.navIcon}>
        {icon}
      </span>
      {collapsed ? null : <span className={styles.navText}>{label}</span>}
    </NavLink>
  );
}

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const markUnauthenticated = useAuthStore((state) => state.markUnauthenticated);
  const authGeneration = useAuthStore((state) => state.generation);
  const openDock = useAIDockStore((state) => state.openDock);
  const clearPageContext = useAIDockStore((state) => state.clearPageContext);
  const collapsed = useNavigationStore((state) => state.collapsed);
  const toggleCollapsed = useNavigationStore((state) => state.toggleCollapsed);
  const [openPanel, setOpenPanel] = useState<TopbarPanel | null>(null);

  const isAIAssistantPage = location.pathname === AI_ASSISTANT_PATH;
  const initialSearchValue =
    location.pathname === '/search'
      ? new URLSearchParams(location.search).get('q') ?? ''
      : '';

  const bindingQuery = useQuery({
    queryKey: ['credential-binding', authGeneration, 'oa'] as const,
    queryFn: () => getBinding('oa'),
  });
  const statusRow = oaSystemStatusRow(
    bindingQuery.data,
    bindingQuery.isError || bindingQuery.isPending,
  );
  const attentionCount = statusRow.kind === 'attention' ? 1 : 0;
  const statusUnknown = statusRow.kind === 'unknown';

  const submitSearch = (value: string) => {
    const term = value.trim();
    navigate({
      pathname: '/search',
      search: term.length === 0 ? '' : `?${new URLSearchParams({ q: term })}`,
    });
  };

  const togglePanel = (panel: TopbarPanel) =>
    setOpenPanel((current) => (current === panel ? null : panel));

  useEffect(() => {
    if (isAIAssistantPage) {
      clearPageContext();
    }
  }, [clearPageContext, isAIAssistantPage]);

  const systemStatusLabel = statusUnknown
    ? '系统状态，暂时无法判断'
    : attentionCount > 0
      ? `系统状态，${attentionCount} 项需要处理`
      : '系统状态，暂无需要处理的项';

  return (
    <div
      className={styles.shell}
      data-collapsed={collapsed ? 'true' : 'false'}
      style={shellCssVariables(collapsed)}
    >
      <aside
        aria-label="主导航"
        className={styles.sidebar}
        data-collapsed={collapsed ? 'true' : 'false'}
      >
        <div className={styles.brand} data-testid="app-brand">
          <span className={styles.brandName}>EternalAI</span>
          {collapsed ? null : <small>办事工作台</small>}
        </div>
        <nav aria-label="工作区" className={styles.nav}>
          {PRIMARY_NAVIGATION.map((item) => (
            <NavigationLink
              collapsed={collapsed}
              icon={item.icon}
              key={item.to}
              label={item.label}
              to={item.to}
            />
          ))}
          <details
            className={styles.adminGroup}
            open={location.pathname.startsWith('/admin/')}
          >
            <summary
              aria-label="管理页面"
              className={styles.adminSummary}
              title="管理页面"
            >
              <span aria-hidden="true" className={styles.navIcon}>
                ⚙
              </span>
              {collapsed ? null : <span className={styles.navText}>管理页面</span>}
            </summary>
            <div className={styles.adminLinks}>
              {ADMIN_NAVIGATION.map((item) => (
                <NavigationLink
                  collapsed={collapsed}
                  icon={item.icon}
                  key={item.to}
                  label={item.label}
                  to={item.to}
                />
              ))}
            </div>
          </details>
        </nav>
        <div className={styles.sidebarFooter}>
          <button
            aria-label={collapsed ? '展开导航' : '收起导航'}
            className={styles.sidebarAction}
            onClick={toggleCollapsed}
            title={collapsed ? '展开导航' : '收起导航'}
            type="button"
          >
            <span aria-hidden="true" className={styles.navIcon}>
              {collapsed ? '»' : '«'}
            </span>
            {collapsed ? null : (
              <span className={styles.navText}>{collapsed ? '展开导航' : '收起导航'}</span>
            )}
          </button>
          <button
            aria-label="退出登录（本地）"
            className={styles.sidebarAction}
            onClick={() => markUnauthenticated()}
            title="退出登录（本地）"
            type="button"
          >
            <span aria-hidden="true" className={styles.navIcon}>
              ⏻
            </span>
            {collapsed ? null : <span className={styles.navText}>退出登录（本地）</span>}
          </button>
        </div>
      </aside>

      <div className={styles.stage} data-testid="app-main">
        <header className={styles.topbar} data-testid="app-topbar">
          <div className={styles.topbarSearch} data-slot="search">
            <GlobalWorkObjectSearch
              key={`${location.pathname}:${location.search}`}
              initialValue={initialSearchValue}
              onSubmit={submitSearch}
            />
          </div>

          <div
            className={styles.identity}
            data-slot="identity"
            data-testid="topbar-identity"
          >
            <span className={styles.identityLine}>{IDENTITY_UNAVAILABLE_STATEMENT}</span>
            <span className={styles.identityLine}>{IDENTITY_UNAVAILABLE_NEXT_STEP}</span>
          </div>

          <button
            aria-expanded={openPanel === 'style'}
            aria-label="切换界面风格"
            className={styles.signalButton}
            data-slot="style"
            onClick={() => togglePanel('style')}
            type="button"
          >
            <span aria-hidden="true" className={styles.signalIcon}>
              ◐
            </span>
            <span className={styles.signalText}>风格</span>
          </button>

          <button
            aria-expanded={openPanel === 'system-status'}
            aria-label={systemStatusLabel}
            className={styles.signalButton}
            data-slot="system-status"
            onClick={() => togglePanel('system-status')}
            type="button"
          >
            <span aria-hidden="true" className={styles.signalIcon}>
              ◉
            </span>
            <span className={styles.signalText}>系统状态</span>
            {statusUnknown ? (
              <span className={styles.badgeUnknown} data-testid="system-status-count">
                ？
              </span>
            ) : attentionCount > 0 ? (
              <span className={styles.badgeAlert} data-testid="system-status-count">
                {attentionCount}
              </span>
            ) : null}
          </button>

          <button
            aria-expanded={openPanel === 'notifications'}
            aria-label="通知，消息功能尚未开发，没有可显示的提醒"
            className={styles.signalButton}
            data-slot="notifications"
            onClick={() => togglePanel('notifications')}
            type="button"
          >
            <span aria-hidden="true" className={styles.signalIcon}>
              ⚑
            </span>
            <span className={styles.signalText}>通知</span>
          </button>

          <span
            aria-label="用户头像：暂时取不到你的照片"
            className={styles.avatar}
            data-slot="avatar"
            data-testid="topbar-avatar"
            role="img"
          >
            <span aria-hidden="true">☻</span>
          </span>
        </header>

        {openPanel === 'style' ? (
          <section aria-label="界面风格" className={styles.panel} role="region">
            <h2 className={styles.panelTitle}>界面风格</h2>
            <p>换底图在下一版提供。</p>
            <p>现在整套界面只有一种风格，这个按钮暂时不会改变任何显示。</p>
          </section>
        ) : null}

        {openPanel === 'system-status' ? (
          <section aria-label="系统状态" className={styles.panel} role="region">
            <h2 className={styles.panelTitle}>系统状态</h2>
            <div className={styles.statusRow} data-status-kind={statusRow.kind}>
              <span aria-hidden="true" className={styles.statusMarker}>
                {statusRow.marker}
              </span>
              <div className={styles.statusBody}>
                <strong>
                  {statusRow.name}：{statusRow.statusText}
                </strong>
                <p className={styles.panelHint}>{statusRow.nextStep}</p>
              </div>
              <Link className={styles.panelAction} to={BINDINGS_PATH}>
                重新绑定
              </Link>
            </div>
            <p className={styles.panelHint}>
              这里现在只能显示 OA 一项。其余项目的判定来源还没有定下来，所以不显示；不显示不等于它们正常。
            </p>
          </section>
        ) : null}

        {openPanel === 'notifications' ? (
          <section aria-label="通知" className={styles.panel} role="region">
            <h2 className={styles.panelTitle}>通知</h2>
            <p>这里现在是空的。</p>
            <p className={styles.panelHint}>
              为什么是空的：消息功能还没有开发，系统还不会给你发提醒。通知和上面的系统状态是两回事，
              两边的数字各算各的，不会合并成一个。
            </p>
            <p className={styles.panelHint}>
              现在怎么办：要办的事请看
              <Link className={styles.panelInlineLink} to={WORK_OBJECTS_PATH}>
                工作事项
              </Link>
              ；OA 里的通知请直接去 OA 查看。
            </p>
          </section>
        ) : null}

        <main className={styles.content} id="main-content">
          <Outlet />
        </main>
      </div>

      {isAIAssistantPage ? null : (
        <button
          aria-label="打开 AI 助手"
          className={styles.floatingEntry}
          data-testid="ai-dock-launcher"
          onClick={openDock}
          type="button"
        >
          <span aria-hidden="true" className={styles.floatingIcon}>
            ✦
          </span>
          <span className={styles.floatingText}>问 AI</span>
        </button>
      )}

      <AIDock suppressed={isAIAssistantPage} />
    </div>
  );
}
