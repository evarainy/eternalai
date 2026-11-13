import { normalizeSearchQuery } from '../shared/query/workObjectSearchNormalization';
import { lazy, useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import AntApp from 'antd/es/app';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { leaderRosterQueryKey, readLeaderRoster } from '../features/leader-roster/leaderRosterApi';
import { getBindingApiV1CredentialBindingsTargetSystemGet as getBinding } from '../generated/credential-bindings/credential-bindings';
import type { CredentialBindingView } from '../generated/credential-bindings/credential-bindings.schemas';
import { useAIDockStore } from '../stores/aiDockStore';
import { resolveFxMode, useAppearanceStore } from '../stores/appearanceStore';
import { useAuthStore } from '../stores/authStore';
import { Icon } from '../shared/ui/Icon';
import type { IconName } from '../shared/ui/Icon';
import { useImLifecycle } from '../features/messages/zulip/useImLifecycle';
import { useImStore } from '../features/messages/zulip/imStore';
import { unreadTotal } from '../features/messages/im/viewModel';
import { Clock } from './Clock';
import { useCurrentIdentity } from './identity';
import { useLogout } from './logout';
import { RouteLoadBoundary } from './RouteLoadingFallback';
import {
  DEPARTMENT_UNAVAILABLE_LINE,
  JOB_TITLE_UNAVAILABLE_LINE,
  NAME_UNAVAILABLE_LINE,
  RAIL_ITEM_HEIGHT,
} from './shellLayout';
import { QuickSearch } from './QuickSearch';
import {
  CLOCK_STYLES,
  CLOCK_STYLE_LABELS,
  FONT_SIZES,
  FONT_SIZE_LABELS,
  FX_MODES,
  FX_MODE_LABELS,
  SKINS,
  SKIN_LABELS,
  SKIN_SWATCHES,
} from './theme';
import styles from './AppShell.module.css';

const AI_ASSISTANT_PATH = '/chat';
const MESSAGES_PATH = '/messages';
const BINDINGS_PATH = '/admin/bindings';
const WORK_OBJECTS_PATH = '/work-objects';

const LazyAIDock = lazy(() =>
  import('./AIDock').then(({ AIDock }) => ({ default: AIDock })),
);

interface PrimaryNavigationItem {
  icon: IconName;
  /** 完整名称：无障碍名称与悬停提示。 */
  label: string;
  /** 窄栏上图标下面的两个字。 */
  short: string;
  to: string;
  /** 归在这一项下面的其他路由（没有自己的导航项）。 */
  also?: readonly string[];
}

/**
 * 2026-11-13 改版：左侧 76px 窄栏，五项。周期任务不再占一级导航，从任务交办页进入，仍高亮「交办」；
 * 工作事项搜索结果页高亮「事项」。
 */
const PRIMARY_NAVIGATION: readonly PrimaryNavigationItem[] = [
  { icon: 'sparkle', label: 'AI 助手', short: '助手', to: AI_ASSISTANT_PATH },
  { icon: 'chat', label: '消息', short: '消息', to: MESSAGES_PATH },
  { icon: 'todo', label: '工作事项', short: '事项', to: WORK_OBJECTS_PATH, also: ['/search'] },
  { icon: 'send', label: '任务交办', short: '交办', to: '/work-dispatch', also: ['/work-object-series'] },
  { icon: 'grid', label: '软件中心', short: '应用', to: '/apps' },
];

interface AdminNavigationItem {
  icon: IconName;
  label: string;
  to: string;
}

const ADMIN_NAVIGATION: readonly AdminNavigationItem[] = [
  { icon: 'tool', label: '功能管理', to: '/admin/registry' },
  { icon: 'file', label: '任务证据', to: '/admin/tasks' },
  { icon: 'card', label: '账号绑定', to: BINDINGS_PATH },
];

/** 领导名单只给本单位系统管理员：入口按后端对名单接口的实际判定显示，后端仍是最终判定。 */
const LEADER_ROSTER_NAVIGATION: AdminNavigationItem = {
  icon: 'users', label: '领导名单', to: '/admin/leader-roster',
};

/** 这些弹出层都由窄栏底部的「管理」「设置」和头像打开，共用同一个浮层，一次只开一个。 */
type ShellPanel = 'admin' | 'system-status' | 'notifications' | 'user';

/** 弹出层的可访问名称；同时用于 `aria-label`。 */
const PANEL_LABELS: Record<ShellPanel, string> = {
  'admin': '管理页面',
  'notifications': '通知',
  'system-status': '系统状态',
  'user': '设置',
};

function activeNavigationIndex(pathname: string): number {
  return PRIMARY_NAVIGATION.findIndex(
    (item) => pathname === item.to || item.also?.includes(pathname) === true,
  );
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

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const { logout, pending: logoutPending, failed: logoutFailed } = useLogout();
  const authGeneration = useAuthStore((state) => state.generation);
  const dockMode = useAIDockStore((state) => state.mode);
  const openDock = useAIDockStore((state) => state.openDock);
  const setDockDraft = useAIDockStore((state) => state.setDraft);
  const clearPageContext = useAIDockStore((state) => state.clearPageContext);
  const isAIAssistantPage = location.pathname === AI_ASSISTANT_PATH;
  const isMessagesPage = location.pathname === MESSAGES_PATH;
  // IM 接收器挂在登录生命周期上：切到其他页面也继续接收、更新窄栏角标（FE_PLAN §4.2）。
  useImLifecycle();
  const messagesUnread = useImStore(unreadTotal);
  const skin = useAppearanceStore((state) => state.skin);
  const size = useAppearanceStore((state) => state.size);
  const clock = useAppearanceStore((state) => state.clock);
  const fx = useAppearanceStore((state) => resolveFxMode(state.fx));
  const setSkin = useAppearanceStore((state) => state.setSkin);
  const setSize = useAppearanceStore((state) => state.setSize);
  const setClock = useAppearanceStore((state) => state.setClock);
  const setFx = useAppearanceStore((state) => state.setFx);
  const [openPanel, setOpenPanel] = useState<ShellPanel | null>(null);
  const [quickSearchOpen, setQuickSearchOpen] = useState(false);
  const [hasOpenedDock, setHasOpenedDock] = useState(
    dockMode !== 'closed',
  );
  const popoverRef = useRef<HTMLElement | null>(null);
  const railFootRef = useRef<HTMLDivElement | null>(null);

  const identity = useCurrentIdentity();
  const [avatarBroken, setAvatarBroken] = useState(false);

  useEffect(() => {
    setAvatarBroken(false);
  }, [identity.avatarPath]);

  const avatarSource =
    identity.avatarPath !== null && !avatarBroken ? identity.avatarPath : null;
  const surname = identity.displayName?.trim().slice(0, 1) || null;
  const avatarLabelBase =
    identity.displayName === null ? '设置' : `设置：${identity.displayName}`;

  const bindingQuery = useQuery({
    queryKey: ['credential-binding', authGeneration, 'oa'] as const,
    queryFn: () => getBinding('oa'),
  });
  const onAdminPath = location.pathname.startsWith('/admin/');
  const [adminPanelOpened, setAdminPanelOpened] = useState(false);
  // 只在管理面板打开过之后才问后端；只有当前登录身份读成功过才显示入口。
  const leaderRosterAccess = useQuery({
    queryKey: leaderRosterQueryKey(authGeneration),
    queryFn: readLeaderRoster,
    enabled: onAdminPath || adminPanelOpened,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
  const showLeaderRoster = leaderRosterAccess.data !== undefined;
  const statusFetch: SystemStatusFetch = bindingQuery.isPending
    ? 'loading'
    : bindingQuery.isError
      ? 'error'
      : 'ready';
  const statusRow = oaSystemStatusRow(bindingQuery.data, statusFetch);
  const statusRows = [statusRow, ...UNCHECKED_STATUS_ROWS];
  const attentionCount = statusRow.kind === 'attention' ? 1 : 0;
  const statusUnknown = statusRow.kind === 'unknown';
  const statusRetrying = statusRow.kind === 'retrying';
  const statusLoading = statusRow.kind === 'loading';
  const statusSummary: { text: string; tone: 'ok' | 'alert' | 'muted' } = statusLoading
    ? { text: '正在读取', tone: 'muted' }
    : statusUnknown
      ? { text: '读不到', tone: 'muted' }
      : attentionCount > 0
        ? { text: `${attentionCount} 个不正常`, tone: 'alert' }
        : { text: '暂无不正常', tone: 'ok' };

  const avatarLabel =
    attentionCount > 0
      ? `${avatarLabelBase}，系统状态 ${attentionCount} 项需要处理`
      : statusUnknown
        ? `${avatarLabelBase}，系统状态暂时无法判断`
        : statusRetrying
          ? `${avatarLabelBase}，系统状态正在重试`
          : avatarLabelBase;

  const submitSearch = (value: string) => {
    const term = normalizeSearchQuery(value) ?? '';
    setQuickSearchOpen(false);
    navigate({
      pathname: '/search',
      search: term.length === 0 ? '' : `?${new URLSearchParams({ q: term })}`,
    });
  };

  const askAssistant = (value: string) => {
    setQuickSearchOpen(false);
    setDockDraft(value);
    openDock();
  };

  useEffect(() => {
    if (dockMode !== 'closed') {
      setHasOpenedDock(true);
    }
  }, [dockMode]);

  const togglePanel = (panel: ShellPanel) => {
    if (panel === 'admin') setAdminPanelOpened(true);
    setOpenPanel((current) => (current === panel ? null : panel));
  };

  useEffect(() => {
    if (isAIAssistantPage) {
      clearPageContext();
    }
  }, [clearPageContext, isAIAssistantPage]);

  // 换页后浮层不该还盖在新页面上。
  useEffect(() => {
    setOpenPanel(null);
  }, [location.pathname]);

  // Ctrl K：任何页面都能打开搜索。IM 自己的快捷键（Ctrl F、Alt ↑↓、Esc）不在这里处理，不会被吞掉。
  useEffect(() => {
    const openOnShortcut = (event: globalThis.KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpenPanel(null);
        setQuickSearchOpen(true);
      }
    };
    document.addEventListener('keydown', openOnShortcut);
    return () => document.removeEventListener('keydown', openOnShortcut);
  }, []);

  /*
   * 弹出层浮在正文之上，所以必须能在不点触发按钮的情况下关掉：点面板与窄栏底部以外的地方、或按 Esc。
   * 窄栏底部的点击交给各自按钮，因此在这里一律放行，避免「关一次又开一次」。
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
        railFootRef.current?.contains(target) === true
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

  const activeIndex = activeNavigationIndex(location.pathname);

  const avatar =
    avatarSource === null ? (
      surname === null ? (
        <Icon name="user" size={20} />
      ) : (
        <span data-testid="user-bar-avatar-initial">{surname}</span>
      )
    ) : (
      <img
        alt=""
        className={styles.avatarImage}
        data-testid="user-bar-avatar-image"
        onError={() => setAvatarBroken(true)}
        src={avatarSource}
      />
    );

  const backToMenu = (
    <button
      aria-label="返回设置"
      className={styles.popBack}
      onClick={() => setOpenPanel('user')}
      type="button"
    >
      <Icon className={styles.popBackIcon} name="right" size={16} strokeWidth={2} />
    </button>
  );

  return (
    <div
      className={styles.shell}
      data-route={isAIAssistantPage ? 'assistant' : isMessagesPage ? 'messages' : 'page'}
    >
      <aside aria-label="主导航" className={styles.rail}>
        {/* 产品标识是纯标识，不是可点入口（2026-09-02 裁决沿用）。 */}
        <div className={styles.brand} data-testid="app-brand" title="EternalAI 办事工作台">
          <Icon name="bolt" size={20} strokeWidth={2} />
          <span className={styles.srOnly}>EternalAI 办事工作台</span>
        </div>
        <button
          aria-label="搜索"
          className={styles.railButton}
          data-testid="rail-search"
          onClick={() => {
            setOpenPanel(null);
            setQuickSearchOpen(true);
          }}
          title="搜索  Ctrl K"
          type="button"
        >
          <Icon name="search" size={20} strokeWidth={1.75} />
        </button>
        <nav aria-label="工作区" className={styles.railNav}>
          <span
            aria-hidden="true"
            className={styles.railIndicator}
            data-visible={activeIndex >= 0 ? 'true' : 'false'}
            style={{ transform: `translateY(${Math.max(activeIndex, 0) * RAIL_ITEM_HEIGHT}px)` }}
          />
          {PRIMARY_NAVIGATION.map((item, index) => {
            const badge = item.to === MESSAGES_PATH ? messagesUnread : 0;
            const name = badge > 0 ? `${item.label}，${badge} 条未读` : item.label;
            return (
              <Link
                aria-current={index === activeIndex ? 'page' : undefined}
                aria-label={name}
                className={styles.railLink}
                key={item.to}
                title={name}
                to={item.to}
              >
                <Icon name={item.icon} size={22} strokeWidth={index === activeIndex ? 2.1 : 1.75} />
                <span aria-hidden="true">{item.short}</span>
                {badge > 0 ? (
                  <span aria-hidden="true" className={styles.railBadge}>{badge > 99 ? '99+' : badge}</span>
                ) : null}
              </Link>
            );
          })}
        </nav>
        <div className={styles.railFoot} data-testid="user-bar" ref={railFootRef}>
          <button
            aria-expanded={openPanel === 'admin'}
            aria-label="管理页面"
            className={styles.railLink}
            data-active={onAdminPath ? 'true' : 'false'}
            onClick={() => togglePanel('admin')}
            title="管理页面"
            type="button"
          >
            <Icon name="sliders" size={22} strokeWidth={1.75} />
            <span aria-hidden="true">管理</span>
          </button>
          <button
            aria-expanded={openPanel === 'user'}
            aria-label="设置"
            className={styles.railLink}
            data-testid="rail-settings"
            onClick={() => togglePanel('user')}
            title="设置：皮肤、字号、时钟、动效"
            type="button"
          >
            <Icon name="gear" size={22} strokeWidth={1.75} />
            <span aria-hidden="true">设置</span>
          </button>
          <button
            aria-expanded={openPanel === 'user'}
            aria-label={avatarLabel}
            className={styles.avatar}
            data-testid="user-bar-identity"
            onClick={() => togglePanel('user')}
            title={identity.displayName ?? '设置'}
            type="button"
          >
            <span className={styles.avatarFace} data-testid="user-bar-avatar">{avatar}</span>
            {/*
              系统状态收在设置菜单里；需要处理、读不到或正在重试时在头像角上留一个带颜色的点，
              不打开菜单也能看见（颜色之外，按钮的无障碍名称里也说明了状态）。
            */}
            {attentionCount > 0 || statusUnknown || statusRetrying ? (
              <span
                aria-hidden="true"
                className={styles.avatarDot}
                data-status-kind={statusRow.kind}
                data-testid="user-bar-status-dot"
              />
            ) : null}
          </button>
        </div>
      </aside>

      {/*
        窄栏底部的浮层：浮在正文之上，不占据也不推挤主内容。关着的时候不在 DOM 里，不参与绘制。
        四种面板共用这一个元素，所以毛玻璃只算一层。
      */}
      {openPanel === null ? null : (
        <section
          aria-label={PANEL_LABELS[openPanel]}
          className={`${styles.popover} wb-glass`}
          data-panel={openPanel}
          data-testid="shell-popover"
          ref={popoverRef}
          role="region"
        >
          {openPanel === 'user' ? (
            <>
              <div className={styles.who}>
                <b>{identity.displayName ?? NAME_UNAVAILABLE_LINE}</b>
                {/*
                  部门有数据源，职务**没有**——OA 的用户信息接口落盘字段里就没有职务字段。
                  雨爷 2026-09-04 裁定「留位 + 如实说明，不得编造」。
                */}
                <small data-testid="user-menu-meta">
                  {`${identity.departmentName ?? DEPARTMENT_UNAVAILABLE_LINE} · ${JOB_TITLE_UNAVAILABLE_LINE}`}
                </small>
              </div>
              <div className={styles.option}>
                <Icon name="droplet" size={18} />
                <span>皮肤</span>
                <div aria-label="皮肤" className={styles.optionChoices} role="group">
                  {SKINS.map((value) => (
                    <button
                      aria-label={SKIN_LABELS[value]}
                      aria-pressed={skin === value}
                      className={styles.swatch}
                      key={value}
                      onClick={() => setSkin(value)}
                      style={{ background: SKIN_SWATCHES[value] }}
                      title={SKIN_LABELS[value]}
                      type="button"
                    />
                  ))}
                </div>
              </div>
              <div className={styles.option}>
                <Icon name="type" size={18} />
                <span>字号</span>
                <div aria-label="字号" className={styles.optionChoices} role="group">
                  {FONT_SIZES.map((value) => (
                    <button
                      aria-pressed={size === value}
                      className={styles.mini}
                      key={value}
                      onClick={() => setSize(value)}
                      type="button"
                    >
                      {FONT_SIZE_LABELS[value]}
                    </button>
                  ))}
                </div>
              </div>
              <div className={styles.option}>
                <Icon name="clock" size={18} />
                <span>时钟</span>
                <div aria-label="时钟" className={styles.optionChoices} role="group">
                  {CLOCK_STYLES.map((value) => (
                    <button
                      aria-pressed={clock === value}
                      className={styles.mini}
                      key={value}
                      onClick={() => setClock(value)}
                      type="button"
                    >
                      {CLOCK_STYLE_LABELS[value]}
                    </button>
                  ))}
                </div>
              </div>
              <div className={styles.option}>
                <Icon name="bolt" size={18} />
                <span>动效</span>
                <div aria-label="动效" className={styles.optionChoices} role="group">
                  {FX_MODES.map((value) => (
                    <button
                      aria-pressed={fx === value}
                      className={styles.mini}
                      key={value}
                      onClick={() => setFx(value)}
                      type="button"
                    >
                      {FX_MODE_LABELS[value]}
                    </button>
                  ))}
                </div>
              </div>
              <button
                className={styles.menuRow}
                data-testid="user-menu-notifications"
                onClick={() => setOpenPanel('notifications')}
                type="button"
              >
                <Icon name="bell" size={18} />
                <span>通知</span>
                <em>暂无</em>
              </button>
              <button
                className={styles.menuRow}
                data-testid="user-menu-system-status"
                onClick={() => setOpenPanel('system-status')}
                type="button"
              >
                <Icon name="sliders" size={18} />
                <span>系统状态</span>
                <em data-testid="system-status-count">
                  <span
                    aria-hidden="true"
                    className={styles.statusDot}
                    data-status-kind={statusRow.kind}
                    data-testid="user-menu-system-status-dot"
                  />
                  {statusSummary.text}
                </em>
              </button>
              <div className={styles.menuFoot}>
                {logoutFailed ? <p role="alert">退出未完成，请重试</p> : null}
                <button
                  className={styles.menuRow}
                  disabled={logoutPending}
                  onClick={() => { void logout(); }}
                  type="button"
                >
                  <Icon name="logout" size={18} />
                  <span>{logoutPending ? '正在退出…' : '退出登录'}</span>
                  <em>只退工作台</em>
                </button>
              </div>
            </>
          ) : null}

          {openPanel === 'admin' ? (
            <>
              <div className={styles.popHead}>
                <h2 className={styles.popTitle}>管理页面</h2>
              </div>
              {ADMIN_NAVIGATION.map((item) => (
                <Link
                  aria-current={location.pathname === item.to ? 'page' : undefined}
                  className={styles.menuRow}
                  key={item.to}
                  to={item.to}
                >
                  <Icon name={item.icon} size={18} />
                  <span>{item.label}</span>
                </Link>
              ))}
              {showLeaderRoster ? (
                <Link
                  aria-current={location.pathname === LEADER_ROSTER_NAVIGATION.to ? 'page' : undefined}
                  className={styles.menuRow}
                  to={LEADER_ROSTER_NAVIGATION.to}
                >
                  <Icon name={LEADER_ROSTER_NAVIGATION.icon} size={18} />
                  <span>{LEADER_ROSTER_NAVIGATION.label}</span>
                </Link>
              ) : null}
            </>
          ) : null}

          {openPanel === 'system-status' ? (
            <>
              <div className={styles.popHead}>
                {backToMenu}
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
                这里只说各个系统还能不能用。要你办的事在设置里的「通知」。
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
                            className="wb-btn wb-btn-sm"
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
                {backToMenu}
                <h2 className={styles.popTitle}>通知</h2>
              </div>
              <div className={styles.popBody}>
                <p className={styles.popEmpty}>暂无通知。</p>
                <p className={styles.popHint}>系统还不会给你发提醒。</p>
                <p className={styles.popHint}>单位消息的新消息与未读，看左侧「消息」上的数字。</p>
                <p className={styles.popHint}>
                  要办的事请看
                  <Link
                    className={styles.inlineLink}
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

      {/* 纸：不透明的内容面，四角收在墨框里。页头不动，正文在页面里自己滚。 */}
      <div className={styles.sheet} data-testid="app-main">
        {isMessagesPage ? null : <Clock hideWhenNarrow={!isAIAssistantPage} />}
        <main className={styles.content} id="main-content">
          <Outlet />
        </main>
      </div>

      <QuickSearch
        canAskAssistant={!isAIAssistantPage}
        onAskAssistant={askAssistant}
        onClose={() => setQuickSearchOpen(false)}
        onSubmit={submitSearch}
        open={quickSearchOpen}
      />

      {isAIAssistantPage ? null : (
        <button
          aria-label="打开 AI 助手"
          className={`${styles.floatingEntry} wb-glass`}
          data-compact={isMessagesPage ? 'true' : 'false'}
          data-testid="ai-dock-launcher"
          onClick={openDock}
          title="问 AI"
          type="button"
        >
          <Icon name="sparkle" size={19} strokeWidth={1.9} />
          {isMessagesPage ? null : <span className={styles.floatingText}>问 AI</span>}
        </button>
      )}

      {hasOpenedDock ? (
        <RouteLoadBoundary label="正在打开 AI 助手" surface="dock">
          <LazyAIDock suppressed={isAIAssistantPage} />
        </RouteLoadBoundary>
      ) : null}
    </div>
  );
}

/** 仅在 ProtectedRoute 已放行后加载；Ant App 上下文持续包住 AppShell 的 Outlet。 */
export function AuthenticatedAppShell() {
  return (
    <AntApp>
      <AppShell />
    </AntApp>
  );
}
