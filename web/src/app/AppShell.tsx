import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, FormEvent } from 'react';
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
  TOPBAR_SEARCH_MIN_WIDTH,
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

/** 弹出层的可访问名称；同时用于 `aria-label`，与顶栏触发按钮一一对应。 */
const PANEL_LABELS: Record<TopbarPanel, string> = {
  'notifications': '通知',
  'style': '界面风格',
  'system-status': '系统状态',
  'user': '用户菜单',
};

interface ShellCssVariables extends CSSProperties {
  '--shell-sidebar-width': string;
  '--shell-sidebar-collapsed-padding': string;
  '--topbar-avatar-width': string;
  '--topbar-gap': string;
  '--topbar-identity-width': string;
  '--topbar-notifications-width': string;
  '--topbar-padding': string;
  '--topbar-search-min-width': string;
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
    '--topbar-search-min-width': `${TOPBAR_SEARCH_MIN_WIDTH}px`,
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
  | 'normal'
  | 'unchecked';

/** 读取后端状态的三态：还在读 / 读到了 / 读不到。出错不得退化成「没有需要处理的项」。 */
type SystemStatusFetch = 'loading' | 'ready' | 'error';

interface SystemStatusRow {
  action: { label: string; to: string } | null;
  icon: IconName;
  kind: SystemStatusKind;
  key: string;
  name: string;
  statusText: string;
  /** 画板在动作按钮右侧放一个技术标识；这里只放后端真实返回的 `poll_status`，没有就留空。 */
  technical: string | null;
}

const BINDINGS_ACTION = { label: '重新绑定 OA 账号', to: BINDINGS_PATH } as const;

/**
 * 系统状态只能回答「目标系统凭证是否可用」。`credential-bindings` 只有按 `target_system` 单查的
 * GET，现役闭集里只有 `oa`，所以只有 OA 这一行有真实判定来源。
 *
 * 画板 `TopPops.dc.html` 还画了「AI 助手」「工作台自己的数据」两行。它们的判定来源尚未落盘（活欠债），
 * 因此照画板保留行位与顺序，但**状态一律标成「还没有接上检查」**，既不编「正常」也不编「不正常」。
 */
function oaSystemStatusRow(
  binding: CredentialBindingView | undefined,
  fetchState: SystemStatusFetch,
): SystemStatusRow {
  const base = { key: 'oa', name: 'OA 办公系统' } as const;
  if (fetchState === 'loading') {
    return {
      ...base,
      action: null,
      icon: 'clock',
      kind: 'loading',
      statusText: '正在读取。读到了这里会自己变。',
      technical: null,
    };
  }
  if (fetchState === 'error' || binding === undefined) {
    return {
      ...base,
      action: null,
      icon: 'help',
      kind: 'unknown',
      statusText: '读不到，暂时不知道。刷新本页；还是取不到就找管理员，别当成正常。',
      technical: null,
    };
  }
  if (binding.poll_status === 'invalid') {
    return {
      ...base,
      action: BINDINGS_ACTION,
      icon: 'alert',
      kind: 'attention',
      statusText: '你在 OA 的密码已经失效，现在取不到新数据。重新绑定大概 10 秒。',
      technical: binding.poll_status,
    };
  }
  if (binding.poll_status === 'captcha_required') {
    return {
      ...base,
      action: BINDINGS_ACTION,
      icon: 'alert',
      kind: 'attention',
      statusText: 'OA 要求输入验证码，现在取不到新数据。重新绑定大概 10 秒。',
      technical: binding.poll_status,
    };
  }
  if (!binding.bound) {
    return {
      ...base,
      action: { label: '绑定 OA 账号', to: BINDINGS_PATH },
      icon: 'minus',
      kind: 'unbound',
      statusText: '还没绑账号，后台同步没开始。',
      technical: null,
    };
  }
  if (binding.poll_status === 'retrying') {
    return {
      ...base,
      action: null,
      icon: 'alert',
      kind: 'retrying',
      statusText: '正在重试，不是密码问题。先照常办事；一直不好就找管理员。',
      technical: binding.poll_status,
    };
  }
  return {
    ...base,
    action: null,
    icon: 'check',
    kind: 'normal',
    statusText: '正常。',
    technical: binding.poll_status,
  };
}

/**
 * 用户菜单「账号绑定」那一行右侧的状态标。画板画的是「OA 已绑」；这里一律取 `getBinding('oa')` 的
 * 真实结果，读不到就说读不到，不默认「已绑」。
 */
function oaBindingPill(
  binding: CredentialBindingView | undefined,
  fetchState: SystemStatusFetch,
): { text: string; tone: 'ok' | 'alert' | 'muted' } {
  if (fetchState === 'loading') {
    return { text: '读取中', tone: 'muted' };
  }
  if (fetchState === 'error' || binding === undefined) {
    return { text: '读不到', tone: 'muted' };
  }
  if (!binding.bound) {
    return { text: 'OA 未绑', tone: 'muted' };
  }
  if (binding.poll_status === 'invalid' || binding.poll_status === 'captcha_required') {
    return { text: 'OA 需重绑', tone: 'alert' };
  }
  return { text: 'OA 已绑', tone: 'ok' };
}

/** 画板上的另外两行：有行位、无判定来源，一律如实标注。 */
const UNCHECKED_STATUS_ROWS: readonly SystemStatusRow[] = [
  {
    action: null,
    icon: 'help',
    key: 'assistant',
    kind: 'unchecked',
    name: 'AI 助手',
    statusText: '还没有接上检查，这里不代表正常。',
    technical: null,
  },
  {
    action: null,
    icon: 'help',
    key: 'workbench',
    kind: 'unchecked',
    name: '工作台自己的数据',
    statusText: '还没有接上检查，这里不代表正常。',
    technical: null,
  },
];

/**
 * 顶栏搜索。画板 `Main.dc.html` 画的是一个 392×46 的凹槽，左边一个图标、右边一行提示文字，**画板上
 * 没有独立按钮**；上一轮据此把按钮收成了左侧那个纯图标提交键。雨爷 2026-09-04 第二次走查的结论是
 * 「页面最上方的搜索按钮丢失」——**以雨爷为准**，把带文字的搜索按钮加回来：
 *
 * - 左侧图标退回装饰（`aria-hidden`），提交入口只有右侧那一个，读屏软件不会听到两个「搜索」；
 * - 按钮与输入框在同一个 flex 行里，靠 `align-items: center` 垂直居中（这正是他上一轮提的对齐问题）；
 * - 可见性按 WCAG 2.2 SC 1.4.11 做：1px 可辨边界（≥3:1）+ 主题色投影，两者并用，不是只加投影；
 * - 凹槽整体仍是画板的 392×46，提示文字不变。
 */
function GlobalWorkObjectSearch({
  initialValue,
  onSubmit,
}: {
  initialValue: string;
  onSubmit: (value: string) => void;
}) {
  const [value, setValue] = useState(initialValue);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSubmit(value);
  };

  return (
    <form className={styles.searchField} onSubmit={handleSubmit} role="search">
      <span aria-hidden="true" className={styles.searchIcon}>
        <Icon name="search" size={19} />
      </span>
      <Input
        allowClear
        aria-label="搜索工作事项"
        className={styles.searchInput}
        onChange={(event) => setValue(event.target.value)}
        placeholder="搜索工作事项、文件编号、责任人"
        value={value}
        variant="borderless"
      />
      <button className={styles.searchSubmit} type="submit">
        搜索
      </button>
    </form>
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
  const popoverRef = useRef<HTMLElement | null>(null);
  const topbarRef = useRef<HTMLElement | null>(null);

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
  const statusRows = [statusRow, ...UNCHECKED_STATUS_ROWS];
  const attentionCount = statusRow.kind === 'attention' ? 1 : 0;
  const statusUnknown = statusRow.kind === 'unknown';
  const statusLoading = statusRow.kind === 'loading';
  const bindingPill = oaBindingPill(bindingQuery.data, statusFetch);
  const statusSummary: { text: string; tone: 'ok' | 'alert' | 'muted' } = statusLoading
    ? { text: '正在读取', tone: 'muted' }
    : statusUnknown
      ? { text: '读不到', tone: 'muted' }
      : attentionCount > 0
        ? { text: `${attentionCount} 个不正常`, tone: 'alert' }
        : { text: '暂无不正常', tone: 'ok' };

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

  /*
   * 弹出层浮在正文之上，所以必须能在不点触发按钮的情况下关掉：点面板与顶栏以外的地方、或按 Esc。
   * 触发按钮本身的点击交给 `togglePanel`，因此顶栏内的点击在这里一律放行，避免「关一次又开一次」。
   */
  useEffect(() => {
    if (openPanel === null) {
      return;
    }
    const closeOnOutside = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (
        popoverRef.current?.contains(target) === true ||
        topbarRef.current?.contains(target) === true
      ) {
        return;
      }
      setOpenPanel(null);
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpenPanel(null);
      }
    };
    document.addEventListener('mousedown', closeOnOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeOnOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [openPanel]);

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
        <header className={styles.topbar} data-testid="app-topbar" ref={topbarRef}>
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
            data-testid="topbar-system-status"
            onClick={() => togglePanel('system-status')}
            type="button"
          >
            {/*
              画板 `TopPops.dc.html` 的顶栏这一格是**带颜色的圆点**（`<span class="dot"
              style="background:#e0342c"></span>系统状态`），不是线框图标。颜色取自与面板里同一份
              `data-status-kind` 映射，不另写一套色值；圆点只承担视觉，可见文字「系统状态」与按钮
              `aria-label` 里的整句状态仍在，颜色不是唯一的区分手段。
            */}
            <span
              aria-hidden="true"
              className={styles.signalDot}
              data-status-kind={statusRow.kind}
              data-testid="topbar-system-status-dot"
            />
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

        {/*
          顶栏弹出层。画板 `TopPops.dc.html` 把它们画成浮在正文之上的 `.pop`：用户菜单 268px、系统状态
          384px、通知 412px，锚在顶栏下方、右对齐到各自的触发按钮，**不占据也不推挤主内容**。这里因此
          用 `position: absolute`（`.stage` 是定位上下文），不再作为 `.stage` 的流内兄弟节点。
        */}
        {openPanel === null ? null : (
          <section
            aria-label={PANEL_LABELS[openPanel]}
            className={styles.popover}
            data-panel={openPanel}
            data-testid="topbar-popover"
            ref={popoverRef}
            role="region"
          >
            {openPanel === 'style' ? (
              <>
                <div className={styles.popHead}>
                  <span className={styles.popIcon}>
                    <Icon name="droplet" size={18} strokeWidth={1.9} />
                  </span>
                  <h2 className={styles.popTitle}>界面风格</h2>
                </div>
                <div className={styles.popBody}>
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
                </div>
              </>
            ) : null}

            {openPanel === 'user' ? (
              <>
                <div className={styles.userHead}>
                  <span className={styles.userAvatar}>
                    <Icon name="user" size={30} />
                  </span>
                  <div className={styles.userWho}>
                    <span className={styles.userName}>
                      {IDENTITY_UNAVAILABLE_STATEMENT}
                    </span>
                    <span className={styles.userMeta}>
                      {IDENTITY_UNAVAILABLE_NEXT_STEP}
                    </span>
                  </div>
                </div>
                <div className={styles.menuGroup}>
                  <Link
                    className={styles.menuRow}
                    onClick={() => setOpenPanel(null)}
                    to={BINDINGS_PATH}
                  >
                    <span className={styles.menuIcon}>
                      <Icon name="card" size={16} strokeWidth={1.9} />
                    </span>
                    <span>账号绑定</span>
                    <span className={styles.pill} data-tone={bindingPill.tone}>
                      {bindingPill.text}
                    </span>
                  </Link>
                  <button
                    className={styles.menuRow}
                    onClick={() => setOpenPanel('style')}
                    type="button"
                  >
                    <span className={styles.menuIcon}>
                      <Icon name="droplet" size={16} strokeWidth={1.9} />
                    </span>
                    <span>换个界面风格</span>
                  </button>
                  {/*
                    这两项画板上有，但工作台里没有对应页面。做成不可点的行位并如实标注去处，
                    不做一个点了没反应的按钮。
                  */}
                  <div className={styles.menuRow} data-inert="true">
                    <span className={styles.menuIcon}>
                      <Icon name="sliders" size={16} strokeWidth={1.9} />
                    </span>
                    <span>个人设置</span>
                    <span className={styles.menuNote}>还没做</span>
                  </div>
                  <div className={styles.menuRow} data-inert="true">
                    <span className={styles.menuIcon}>
                      <Icon name="help" size={16} strokeWidth={1.9} />
                    </span>
                    <span>怎么用 / 找人帮忙</span>
                    <span className={styles.menuNote}>找管理员</span>
                  </div>
                </div>
                <div className={styles.menuFoot}>
                  <button
                    className={styles.menuRow}
                    data-tone="alert"
                    onClick={() => markUnauthenticated()}
                    type="button"
                  >
                    <span className={styles.menuIcon}>
                      <Icon name="external" size={16} strokeWidth={1.9} />
                    </span>
                    <span>退出登录</span>
                    <span className={styles.menuNote}>只退工作台</span>
                  </button>
                </div>
              </>
            ) : null}

            {openPanel === 'system-status' ? (
              <>
                <div className={styles.popHead}>
                  <span className={styles.popIcon} data-tone={statusRow.kind}>
                    <Icon name={statusRow.icon} size={18} strokeWidth={1.9} />
                  </span>
                  <h2 className={styles.popTitle}>系统状态</h2>
                  <span
                    className={styles.pill}
                    data-testid="system-status-summary"
                    data-tone={statusSummary.tone}
                  >
                    {statusSummary.text}
                  </span>
                </div>
                <p className={styles.popNote}>
                  这里只说各个系统还能不能用。要你办的事在右边的「通知」里。
                </p>
                {statusRows.map((row) => (
                  <div
                    className={styles.statusRow}
                    data-status-kind={row.kind}
                    key={row.key}
                  >
                    <span
                      aria-hidden="true"
                      className={styles.statusDot}
                      data-status-kind={row.kind}
                    />
                    <div className={styles.statusBody}>
                      <div className={styles.statusName}>{row.name}</div>
                      <p className={styles.statusText}>{row.statusText}</p>
                      {row.action === null && row.technical === null ? null : (
                        <div className={styles.statusActions}>
                          {row.action === null ? null : (
                            <Link
                              className={styles.statusAction}
                              onClick={() => setOpenPanel(null)}
                              to={row.action.to}
                            >
                              {row.action.label}
                            </Link>
                          )}
                          {row.technical === null ? null : (
                            <span className={styles.statusTechnical}>
                              {row.technical}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </>
            ) : null}

            {openPanel === 'notifications' ? (
              <>
                <div className={styles.popHead}>
                  <span className={styles.popIcon}>
                    <Icon name="bell" size={18} strokeWidth={1.9} />
                  </span>
                  <h2 className={styles.popTitle}>通知</h2>
                </div>
                <p className={styles.popNote}>
                  要你办的事在这儿；系统能不能用，看左边的「系统状态」。
                </p>
                <div className={styles.popBody}>
                  <p className={styles.popEmpty}>这里现在是空的。</p>
                  <p className={styles.popHint}>
                    消息功能还没有开发，系统还不会给你发提醒。
                  </p>
                  <p className={styles.popHint}>
                    要办的事请看
                    <Link
                      className={styles.panelInlineLink}
                      onClick={() => setOpenPanel(null)}
                      to={WORK_OBJECTS_PATH}
                    >
                      工作事项
                    </Link>
                    ；OA 里的通知请直接去 OA 查看。
                  </p>
                </div>
              </>
            ) : null}
          </section>
        )}

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
