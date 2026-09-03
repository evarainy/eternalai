import { useEffect, useState } from 'react';
import type { CSSProperties } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Input } from 'antd';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { getBindingApiV1CredentialBindingsTargetSystemGet as getBinding } from '../generated/credential-bindings/credential-bindings';
import type { CredentialBindingView } from '../generated/credential-bindings/credential-bindings.schemas';
import { useAIDockStore } from '../stores/aiDockStore';
import { useAppearanceStore } from '../stores/appearanceStore';
import { useAuthStore } from '../stores/authStore';
import { useNavigationStore } from '../stores/navigationStore';
import { Icon } from '../shared/ui/Icon';
import type { IconName } from '../shared/ui/Icon';
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
import { BACKGROUND_PRESETS, BACKGROUND_PRESET_LABELS } from './theme';
import styles from './AppShell.module.css';

const AI_ASSISTANT_PATH = '/chat';
const BINDINGS_PATH = '/admin/bindings';
const WORK_OBJECTS_PATH = '/work-objects';

interface PrimaryNavigationItem {
  icon: IconName;
  label: string;
  to: string;
}

/**
 * 2026-09-02 裁决：一级导航五项平铺，顺序与路径固定。图标取定稿画板符号表（`shared/ui/Icon`），
 * 与画板左导航逐项对应，不再用字符符号。
 */
const PRIMARY_NAVIGATION: readonly PrimaryNavigationItem[] = [
  { icon: 'chat', label: 'AI 助手', to: AI_ASSISTANT_PATH },
  { icon: 'list', label: '工作事项', to: WORK_OBJECTS_PATH },
  { icon: 'send', label: '任务交办', to: '/work-dispatch' },
  { icon: 'grid', label: '软件中心', to: '/apps' },
  { icon: 'mail', label: '消息', to: '/messages' },
];

const ADMIN_NAVIGATION: readonly PrimaryNavigationItem[] = [
  { icon: 'tool', label: '功能管理', to: '/admin/registry' },
  { icon: 'file', label: '任务证据', to: '/admin/tasks' },
  { icon: 'card', label: '账号绑定', to: BINDINGS_PATH },
];

type TopbarPanel = 'style' | 'system-status' | 'notifications' | 'user';

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

type SystemStatusKind =
  | 'loading'
  | 'unknown'
  | 'unbound'
  | 'attention'
  | 'retrying'
  | 'normal';

/** 读取后端状态的三态：还在读 / 读到了 / 读不到。出错不得退化成「没有需要处理的项」。 */
type SystemStatusFetch = 'loading' | 'ready' | 'error';

interface SystemStatusRow {
  icon: IconName;
  kind: SystemStatusKind;
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
  fetchState: SystemStatusFetch,
): SystemStatusRow {
  if (fetchState === 'loading') {
    return {
      icon: 'clock',
      kind: 'loading',
      name: 'OA 系统',
      statusText: '正在读取',
      nextStep: '下一步：稍等，读到了这里会自己变。',
    };
  }
  if (fetchState === 'error' || binding === undefined) {
    return {
      icon: 'help',
      kind: 'unknown',
      name: 'OA 系统',
      statusText: '读不到，暂时不知道',
      nextStep: '下一步：刷新本页；还是取不到就找管理员，别当成正常。',
    };
  }
  if (binding.poll_status === 'invalid') {
    return {
      icon: 'alert',
      kind: 'attention',
      name: 'OA 系统',
      statusText: '密码已失效，后台同步已停止',
      nextStep: '下一步：去「账号绑定」重新绑定 OA 密码。',
    };
  }
  if (binding.poll_status === 'captcha_required') {
    return {
      icon: 'alert',
      kind: 'attention',
      name: 'OA 系统',
      statusText: 'OA 要求输入验证码，后台同步已停止',
      nextStep: '下一步：去「账号绑定」重新绑定 OA 密码。',
    };
  }
  if (!binding.bound) {
    return {
      icon: 'minus',
      kind: 'unbound',
      name: 'OA 系统',
      statusText: '尚未绑定',
      nextStep: '下一步：去「账号绑定」绑定 OA 密码后才能后台同步。',
    };
  }
  if (binding.poll_status === 'retrying') {
    return {
      icon: 'alert',
      kind: 'retrying',
      name: 'OA 系统',
      statusText: '正在重试（不是密码问题）',
      nextStep: '下一步：先照常办事；持续不恢复时请联系管理员。',
    };
  }
  return {
    icon: 'check',
    kind: 'normal',
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
  icon: IconName;
  label: string;
  to: string;
}) {
  return (
    <NavLink aria-label={label} className={styles.navLink} title={label} to={to}>
      <span className={styles.navIcon}>
        <Icon name={icon} size={17} strokeWidth={1.9} />
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
  const background = useAppearanceStore((state) => state.background);
  const setBackground = useAppearanceStore((state) => state.setBackground);
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
  const statusFetch: SystemStatusFetch = bindingQuery.isPending
    ? 'loading'
    : bindingQuery.isError
      ? 'error'
      : 'ready';
  const statusRow = oaSystemStatusRow(bindingQuery.data, statusFetch);
  const attentionCount = statusRow.kind === 'attention' ? 1 : 0;
  const statusUnknown = statusRow.kind === 'unknown';
  const statusLoading = statusRow.kind === 'loading';

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

  const systemStatusLabel = statusLoading
    ? '系统状态，正在读取'
    : statusUnknown
      ? '系统状态，暂时无法判断'
      : attentionCount > 0
        ? `系统状态，${attentionCount} 项需要处理`
        : '系统状态，暂无需要处理的项';

  return (
    <div
      className={`${styles.shell} ${styles[background]}`}
      data-background={background}
      data-collapsed={collapsed ? 'true' : 'false'}
      style={shellCssVariables(collapsed)}
    >
      <aside
        aria-label="主导航"
        className={styles.sidebar}
        data-collapsed={collapsed ? 'true' : 'false'}
      >
        <div className={styles.brand} data-testid="app-brand">
          <span className={styles.brandMark}>
            <Icon name="bolt" size={21} strokeWidth={1.9} />
          </span>
          {collapsed ? null : (
            <span className={styles.brandText}>
              <span className={styles.brandName}>EternalAI</span>
              <small>办事工作台</small>
            </span>
          )}
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
              <span className={styles.navIcon}>
                <Icon name="sliders" size={17} strokeWidth={1.9} />
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
            <span className={styles.navIcon} data-direction={collapsed ? 'expand' : 'collapse'}>
              <Icon name="expandnav" size={17} strokeWidth={1.9} />
            </span>
            {collapsed ? null : (
              <span className={styles.navText}>{collapsed ? '展开导航' : '收起导航'}</span>
            )}
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
          </div>

          <button
            aria-expanded={openPanel === 'style'}
            aria-label="切换界面风格"
            className={styles.signalButton}
            data-slot="style"
            onClick={() => togglePanel('style')}
            type="button"
          >
            <Icon className={styles.signalIcon} name="droplet" size={18} />
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
            <Icon className={styles.signalIcon} name={statusRow.icon} size={18} />
            <span className={styles.signalText}>系统状态</span>
            {statusLoading ? (
              <span className={styles.badgeUnknown} data-testid="system-status-count">
                …
              </span>
            ) : statusUnknown ? (
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
            <Icon className={styles.signalIcon} name="bell" size={18} />
            <span className={styles.signalText}>通知</span>
          </button>

          <button
            aria-expanded={openPanel === 'user'}
            aria-label="用户菜单，暂时取不到你的照片"
            className={styles.avatarButton}
            data-slot="avatar"
            data-testid="topbar-avatar"
            onClick={() => togglePanel('user')}
            type="button"
          >
            <Icon name="user" size={24} />
          </button>
        </header>

        {openPanel === 'style' ? (
          <section aria-label="界面风格" className={styles.panel} role="region">
            <h2 className={styles.panelTitle}>界面风格</h2>
            <p>选一张底图。字号和按钮大小都不会跟着变。</p>
            <div className={styles.backgroundChoices}>
              {BACKGROUND_PRESETS.map((preset) => (
                <button
                  aria-pressed={background === preset}
                  className={styles.backgroundChoice}
                  key={preset}
                  onClick={() => setBackground(preset)}
                  type="button"
                >
                  <span
                    aria-hidden="true"
                    className={styles.backgroundSwatch}
                    data-preset={preset}
                  />
                  <span>{BACKGROUND_PRESET_LABELS[preset]}</span>
                </button>
              ))}
            </div>
            <p className={styles.panelHint}>选好就生效，下次打开还是这一张。</p>
          </section>
        ) : null}

        {openPanel === 'user' ? (
          <section aria-label="用户菜单" className={styles.panel} role="region">
            <h2 className={styles.panelTitle}>你的账号</h2>
            <div className={styles.userMenuIdentity}>
              <span className={styles.identityLine}>
                {IDENTITY_UNAVAILABLE_STATEMENT}
              </span>
              <span className={styles.identityLine}>
                {IDENTITY_UNAVAILABLE_NEXT_STEP}
              </span>
            </div>
            <ul className={styles.userMenuList}>
              <li>
                <Link className={styles.userMenuItem} to={BINDINGS_PATH}>
                  <Icon name="card" size={20} />
                  <span>账号绑定</span>
                </Link>
              </li>
              <li>
                <button
                  className={styles.userMenuItem}
                  onClick={() => setOpenPanel('style')}
                  type="button"
                >
                  <Icon name="droplet" size={20} />
                  <span>换个界面风格</span>
                </button>
              </li>
              <li className={styles.userMenuItem}>
                <Icon name="help" size={20} />
                <span>怎么用 / 找人帮忙：遇到问题请找本单位的系统管理员。</span>
              </li>
              <li className={styles.userMenuSeparated}>
                <button
                  className={styles.userMenuItem}
                  onClick={() => markUnauthenticated()}
                  type="button"
                >
                  <Icon name="external" size={20} />
                  <span>退出登录（本地）</span>
                </button>
              </li>
            </ul>
            <p className={styles.panelHint}>
              退出登录只清这台电脑上的登录状态，不会退出 OA。
            </p>
          </section>
        ) : null}

        {openPanel === 'system-status' ? (
          <section aria-label="系统状态" className={styles.panel} role="region">
            <h2 className={styles.panelTitle}>系统状态</h2>
            <div className={styles.statusRow} data-status-kind={statusRow.kind}>
              <span className={styles.statusMarker}>
                <Icon name={statusRow.icon} size={22} strokeWidth={1.9} />
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
              只能显示 OA 一项；其余系统还没有判定来源，不显示不等于正常。
            </p>
          </section>
        ) : null}

        {openPanel === 'notifications' ? (
          <section aria-label="通知" className={styles.panel} role="region">
            <h2 className={styles.panelTitle}>通知</h2>
            <p>这里现在是空的。</p>
            <p className={styles.panelHint}>
              消息功能还没有开发，系统还不会给你发提醒。
            </p>
            <p className={styles.panelHint}>
              要办的事请看
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
          <Icon className={styles.floatingIcon} name="spark" size={21} strokeWidth={1.9} />
          <span className={styles.floatingText}>问 AI</span>
        </button>
      )}

      <AIDock suppressed={isAIAssistantPage} />
    </div>
  );
}
