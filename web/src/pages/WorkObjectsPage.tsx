import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App as AntApp,
  Button,
  Descriptions,
  Drawer,
  Flex,
  Radio,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ApiError } from '../api/mutator';
import { usePageContextRegistration } from '../app/usePageContextRegistration';
import type { PageContextDeclaration } from '../contracts/pageContext';
import {
  getWorkObjectApiV1WorkObjectsWorkObjectIdGet as getWorkObject,
  listWorkObjectsApiV1WorkObjectsGet as listWorkObjects,
  setWorkObjectHandlingMarkApiV1WorkObjectsWorkObjectIdHandlingMarkPatch as setHandlingMark,
  syncWorkObjectsApiV1WorkObjectsSyncPost as syncWorkObjects,
} from '../generated/work-objects/work-objects';
import type {
  OAWorkObjectView,
  SetHandlingMarkRequestMark,
  WorkObjectListResponse,
  WorkObjectListResponseItemsItem,
} from '../generated/work-objects/work-objects.schemas';
import { Icon } from '../shared/ui/Icon';
import type { IconName } from '../shared/ui/Icon';
import { QueryTable } from '../shared/ui/QueryTable';
import { useAIDockStore } from '../stores/aiDockStore';
import { useAuthStore } from '../stores/authStore';
import styles from './WorkObjectsPage.module.css';

const { Text } = Typography;

/**
 * 2026-09-03 画板 `Main.dc.html` 定稿的三分类：互斥、各带计数，一件事只会落在一个分类里。
 *
 * `done`（已完成）**后端没有数据源**——`GET /api/v1/work-objects` 只返回 OA 待办快照，没有办结事项。
 * 按「UI 决定要有什么功能，不决定数据可不可信」这条护栏，这一格照常显示但计数写占位符，
 * 既不编数字也不写 `0`（`0` 会被读成「我没有已完成的」，同样是假信息）。
 */
type WorkObjectView = 'urgent' | 'todo' | 'done';

/** 「已完成」的计数占位：没有数据源时显示它，不显示任何数字。 */
const UNAVAILABLE_COUNT = '—';

function workObjectsQueryKey(authGeneration: number) {
  return ['work-objects', authGeneration] as const;
}

function workObjectDetailsQueryKey(authGeneration: number) {
  return ['work-objects', authGeneration, 'detail'] as const;
}

function workObjectDetailQueryKey(authGeneration: number, workObjectId: string) {
  return [...workObjectDetailsQueryKey(authGeneration), workObjectId] as const;
}

function timestampValue(value: string | null): number {
  if (value === null) {
    return Number.NEGATIVE_INFINITY;
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
}

function mergeWorkObjectView(
  current: WorkObjectListResponseItemsItem | undefined,
  incoming: WorkObjectListResponseItemsItem,
): WorkObjectListResponseItemsItem {
  if (
    current === undefined ||
    current.state_authority !== incoming.state_authority
  ) {
    return incoming;
  }
  if (incoming.state_authority === 'internal') {
    return incoming;
  }
  if (current.state_authority === 'internal') {
    return incoming;
  }
  const sourceOwner =
    timestampValue(incoming.source_fetched_at) >=
    timestampValue(current.source_fetched_at)
      ? incoming
      : current;
  const handlingOwner =
    timestampValue(incoming.handling_marked_at) >=
    timestampValue(current.handling_marked_at)
      ? incoming
      : current;
  return {
    ...sourceOwner,
    handling_mark: handlingOwner.handling_mark,
    handling_marked_at: handlingOwner.handling_marked_at,
    handling_action: handlingOwner.handling_action,
    handling_capability_id: handlingOwner.handling_capability_id,
    task_record_id: incoming.task_record_id ?? current.task_record_id,
  };
}

const handlingMarkLabels: Record<SetHandlingMarkRequestMark, string> = {
  pending_sync_confirmation: '待同步完成情况',
  handled_elsewhere: '已在别处处理',
};

const handlingActionLabels: Record<OAWorkObjectView['handling_action'], string> = {
  ai_draft: '让 AI 先写',
  self_serve: '我自己办',
  go_source_system: '去 OA 办',
  view_only: '先看看',
};

const handlingActionDescriptions: Record<OAWorkObjectView['handling_action'], string> = {
  ai_draft: '先核对事项信息；AI 起草功能将在后续接入。',
  self_serve: '请先核对事项信息；实际办理操作将在后续接入。',
  go_source_system: '这条事项的状态权威在 OA，请在 OA 中办理。',
  view_only: '当前只提供事项详情查看。',
};

function formatTimestamp(value: string): string {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'medium',
  }).format(timestamp);
}

function endOfDay(now: Date, dayOffset: number): Date {
  const boundary = new Date(now);
  boundary.setDate(boundary.getDate() + dayOffset);
  boundary.setHours(23, 59, 59, 999);
  return boundary;
}

/**
 * 紧急 = 已逾期（`due_at < now`）或今明两天到期或等你确认（`handling_mark`）。
 *
 * 画板上还有一条「派发时标了紧急」，**本页不实现**：后端 `OAWorkObjectView` 没有该字段，它由后续的
 * 任务派发模块提供。不拿别的字段凑，界面上也不出现这四个字，免得让人以为它已经生效。
 */
function isUrgentWorkObject(item: OAWorkObjectView, now: Date): boolean {
  if (item.handling_mark === 'pending_sync_confirmation') {
    return true;
  }
  if (item.due_at === null) {
    return false;
  }
  const dueAt = new Date(item.due_at);
  if (Number.isNaN(dueAt.getTime())) {
    return false;
  }
  return dueAt <= endOfDay(now, 1);
}

function dueTimestamp(value: string | null): number {
  if (value === null) {
    return Number.POSITIVE_INFINITY;
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Number.POSITIVE_INFINITY : parsed;
}

interface DueBadge {
  className: string | undefined;
  icon: IconName;
  label: string;
}

/**
 * 行内截止状态徽标：颜色 + 图标 + 文字三者齐备，不允许只靠颜色区分（2026-08-27 §五）。
 * 没到期、没给截止时间、时间格式异常都不出徽标——那一行本来就不紧急，多一个灰徽标只是噪声。
 */
function dueBadge(value: string | null, now: Date): DueBadge | null {
  if (value === null) {
    return null;
  }
  const dueAt = new Date(value);
  if (Number.isNaN(dueAt.getTime())) {
    return null;
  }
  if (dueAt < now) {
    return { className: styles.errorStatus, icon: 'alert', label: '已逾期' };
  }
  if (dueAt <= endOfDay(now, 1)) {
    return { className: styles.warningStatus, icon: 'clock', label: '今明到期' };
  }
  return null;
}

/** 截止时间按日历距离压成一格能放下的短句，避免整行被完整时间戳撑开。 */
function formatDueAt(value: string | null, now: Date): string {
  if (value === null) {
    return 'OA 未提供';
  }
  const dueAt = new Date(value);
  if (Number.isNaN(dueAt.getTime())) {
    return value;
  }
  const clock = new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(dueAt);
  for (const [offset, prefix] of [
    [-1, '昨天'],
    [0, '今天'],
    [1, '明天'],
  ] as const) {
    if (dueAt <= endOfDay(now, offset) && dueAt > endOfDay(now, offset - 1)) {
      return `${prefix} ${clock}`;
    }
  }
  return `${new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
  }).format(dueAt)} ${clock}`;
}

/** 顶部的数据截至写全日期但不写秒：秒对判断新鲜度没用，只是把这一行撑长。 */
function formatFreshness(value: string): string {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return value;
  }
  // `dateStyle` 不能和 `hour` / `minute` 同时给，Intl 会直接抛错，所以这里逐项写。
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(timestamp);
}

/** 行内的数据截至只保留时分：同一批快照的日期都一样，写全反而把副行挤到换行。 */
function formatFreshnessClock(value: string): string {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(timestamp);
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.code}: ${error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'unknown_error: 请求失败';
}

function handlingMarkTag(mark: OAWorkObjectView['handling_mark']) {
  if (mark === 'pending_sync_confirmation') {
    return <Tag color="gold"><Icon name="clock" size={16} /> {handlingMarkLabels[mark]}</Tag>;
  }
  if (mark === 'handled_elsewhere') {
    return <Tag color="green"><Icon name="check" size={16} /> {handlingMarkLabels[mark]}</Tag>;
  }
  return <Tag><Icon name="minus" size={16} /> 未标记</Tag>;
}

function dueBadgeOf(item: OAWorkObjectView) {
  const badge = dueBadge(item.due_at, new Date());
  if (badge === null) {
    return null;
  }
  return (
    <span className={badge.className}>
      <Icon name={badge.icon} size={16} /> {badge.label}
    </span>
  );
}

/** 行内处理痕迹徽标：只在有痕迹时出现，措辞按画板写成用户视角的一句话。 */
function markBadge(mark: OAWorkObjectView['handling_mark']) {
  if (mark === 'pending_sync_confirmation') {
    return (
      <span className={styles.pendingStatus}>
        <Icon name="clock" size={16} /> 等你确认
      </span>
    );
  }
  if (mark === 'handled_elsewhere') {
    return (
      <span className={styles.neutralStatus}>
        <Icon name="check" size={16} /> 已在别处处理
      </span>
    );
  }
  return null;
}

export default function WorkObjectsPage() {
  const [selectedWorkObjectId, setSelectedWorkObjectId] = useState<string>();
  const [view, setView] = useState<WorkObjectView>('urgent');
  const autoSyncGeneration = useRef<number>();
  const queryClient = useQueryClient();
  const authGeneration = useAuthStore((state) => state.generation);
  const markUnauthenticated = useAuthStore((state) => state.markUnauthenticated);
  const dockMode = useAIDockStore((state) => state.mode);
  const { message } = AntApp.useApp();
  const listQueryKey = workObjectsQueryKey(authGeneration);

  const listQuery = useQuery({
    queryKey: listQueryKey,
    queryFn: () => listWorkObjects(),
  });

  const syncMutation = useMutation({
    mutationFn: (requestedGeneration: number) => {
      if (useAuthStore.getState().generation !== requestedGeneration) {
        throw new Error('authentication_generation_changed');
      }
      return syncWorkObjects();
    },
    onSuccess: async (response, requestedGeneration) => {
      if (useAuthStore.getState().generation !== requestedGeneration) {
        return;
      }
      const responseDetailKeys = response.items.map((item) =>
        workObjectDetailQueryKey(requestedGeneration, item.work_object_id),
      );
      await Promise.all([
        queryClient.cancelQueries({
          queryKey: workObjectsQueryKey(requestedGeneration),
          exact: true,
        }),
        ...responseDetailKeys.map((queryKey) =>
          queryClient.cancelQueries({ queryKey, exact: true }),
        ),
      ]);
      if (useAuthStore.getState().generation !== requestedGeneration) {
        return;
      }
      queryClient.setQueryData<WorkObjectListResponse>(
        workObjectsQueryKey(requestedGeneration),
        (current) => ({
          ...response,
          items: response.items.map((item) =>
            mergeWorkObjectView(
              current?.items.find(
                (currentItem) =>
                  currentItem.work_object_id === item.work_object_id,
              ),
              item,
            ),
          ),
        }),
      );
      for (const item of response.items) {
        const detailKey = workObjectDetailQueryKey(
          requestedGeneration,
          item.work_object_id,
        );
        if (queryClient.getQueryState(detailKey) !== undefined) {
          queryClient.setQueryData<WorkObjectListResponseItemsItem>(
            detailKey,
            (current) => mergeWorkObjectView(current, item),
          );
        }
      }
    },
  });
  const triggerSync = syncMutation.mutate;

  useEffect(() => {
    if (
      listQuery.isSuccess &&
      autoSyncGeneration.current !== authGeneration
    ) {
      autoSyncGeneration.current = authGeneration;
      triggerSync(authGeneration);
    }
  }, [authGeneration, listQuery.isSuccess, triggerSync]);

  const detailQuery = useQuery({
    queryKey: workObjectDetailQueryKey(
      authGeneration,
      selectedWorkObjectId ?? '',
    ),
    queryFn: () => {
      if (selectedWorkObjectId === undefined) {
        throw new Error('work_object_id_required');
      }
      return getWorkObject(selectedWorkObjectId);
    },
    enabled: selectedWorkObjectId !== undefined,
  });

  const markMutation = useMutation({
    mutationFn: ({
      workObjectId,
      mark,
      authGeneration: requestedGeneration,
    }: {
      workObjectId: string;
      mark: SetHandlingMarkRequestMark;
      authGeneration: number;
    }) => {
      if (useAuthStore.getState().generation !== requestedGeneration) {
        throw new Error('authentication_generation_changed');
      }
      return setHandlingMark(workObjectId, { mark });
    },
    onSuccess: async (updated, variables) => {
      if (useAuthStore.getState().generation !== variables.authGeneration) {
        return;
      }
      await Promise.all([
        queryClient.cancelQueries({
          queryKey: workObjectsQueryKey(variables.authGeneration),
          exact: true,
        }),
        queryClient.cancelQueries({
          queryKey: workObjectDetailQueryKey(
            variables.authGeneration,
            updated.work_object_id,
          ),
          exact: true,
        }),
      ]);
      if (useAuthStore.getState().generation !== variables.authGeneration) {
        return;
      }
      queryClient.setQueryData<WorkObjectListResponse>(
        workObjectsQueryKey(variables.authGeneration),
        (current) =>
          current
            ? {
                ...current,
                items: current.items.map((item) =>
                  item.work_object_id === updated.work_object_id
                    ? mergeWorkObjectView(item, updated)
                    : item,
                ),
              }
            : current,
      );
      queryClient.setQueryData(
        workObjectDetailQueryKey(
          variables.authGeneration,
          updated.work_object_id,
        ),
        (current: WorkObjectListResponseItemsItem | undefined) =>
          mergeWorkObjectView(current, updated),
      );
      void message.success('处理痕迹已记录；OA 状态未被修改');
    },
    onError: (error, variables) => {
      if (useAuthStore.getState().generation !== variables.authGeneration) {
        return;
      }
      void message.error(errorText(error));
    },
  });

  const items = listQuery.data?.items;
  const oaItems = useMemo(
    () =>
      (items ?? []).filter(
        (item): item is OAWorkObjectView =>
          item.state_authority === 'external_snapshot',
      ),
    [items],
  );
  const newestFetchedAt = oaItems.reduce<string | undefined>((newest, item) => {
    if (!newest || new Date(item.source_fetched_at) > new Date(newest)) {
      return item.source_fetched_at;
    }
    return newest;
  }, undefined);

  const { todoItems, urgentItems } = useMemo(() => {
    const now = new Date();
    const urgent: OAWorkObjectView[] = [];
    const todo: OAWorkObjectView[] = [];
    for (const item of oaItems) {
      (isUrgentWorkObject(item, now) ? urgent : todo).push(item);
    }
    return { todoItems: todo, urgentItems: urgent };
  }, [oaItems]);

  /*
   * 「已完成」永远给空数组：后端没有办结数据源，任何非空列表都会是编出来的。空态里写明缺口。
   */
  const visibleItems =
    view === 'urgent' ? urgentItems : view === 'todo' ? todoItems : [];
  const contextWorkObject =
    selectedWorkObjectId !== undefined &&
    detailQuery.data?.state_authority === 'external_snapshot'
      ? detailQuery.data
      : undefined;
  const pageContextDeclaration = useMemo<PageContextDeclaration>(() => {
    return {
      surface_id: 'work-objects',
      organization_scope: null,
      work_object_refs:
        contextWorkObject === undefined
          ? []
          : [{ work_object_id: contextWorkObject.work_object_id }],
      source_refs:
        contextWorkObject === undefined
          ? []
          : [
              {
                source_system: contextWorkObject.source_system,
                source_ref: contextWorkObject.source_ref,
              },
            ],
      filters: [
        {
          field: 'view',
          operator: 'equals',
          value: view,
          source: 'visible_control',
        },
      ],
      selected_metric: null,
      allowed_capabilities:
        contextWorkObject?.handling_capability_id === null ||
        contextWorkObject?.handling_capability_id === undefined
          ? []
          : [contextWorkObject.handling_capability_id],
      freshness: newestFetchedAt
        ? { state: 'reported', observed_at: newestFetchedAt }
        : { state: 'unknown', observed_at: null },
      visibility: 'principal',
    };
  }, [contextWorkObject, newestFetchedAt, view]);

  usePageContextRegistration(pageContextDeclaration);

  const assigneeFilters = useMemo(
    () =>
      [...new Set(oaItems.map((item) => item.assignee_display_name))]
        .sort((left, right) => left.localeCompare(right, 'zh-CN'))
        .map((assignee) => ({ text: assignee, value: assignee })),
    [oaItems],
  );

  const columns = useMemo<ColumnsType<OAWorkObjectView>>(
    () => [
      {
        title: '事项',
        dataIndex: 'source_title',
        key: 'source_title',
        width: '48%',
        render: (value: string, item) => (
          <div className={styles.workItemTitle}>
            <div className={styles.titleLine}>
              <Text className={styles.titleText} strong>
                {value}
              </Text>
              {dueBadgeOf(item)}
              {markBadge(item.handling_mark)}
            </div>
            {/*
              来源系统 · 来源编号 · 当前步骤 · 数据截至：四段常驻可见，不折叠、不靠 hover
              （2026-08-27 §五）。
            */}
            <div className={styles.sourceLine}>
              <span>OA 办公系统</span>
              <span>{item.source_ref}</span>
              <span>当前步骤 {item.source_status}</span>
              <span>数据截至 {formatFreshnessClock(item.source_fetched_at)}</span>
            </div>
          </div>
        ),
      },
      {
        title: '责任人 / 部门',
        dataIndex: 'assignee_display_name',
        key: 'assignee_display_name',
        width: '18%',
        filters: assigneeFilters,
        filterIcon: () => <span className={styles.filterLabel}>筛选</span>,
        onFilter: (value, item) => item.assignee_display_name === value,
        sorter: (left, right) =>
          left.assignee_display_name.localeCompare(
            right.assignee_display_name,
            'zh-CN',
          ),
      },
      {
        title: '截止时间',
        dataIndex: 'due_at',
        key: 'due_at',
        width: '16%',
        sorter: (left, right) => dueTimestamp(left.due_at) - dueTimestamp(right.due_at),
        render: (value: string | null) => {
          const now = new Date();
          const dueAt = value === null ? null : new Date(value);
          const pressing =
            dueAt !== null &&
            !Number.isNaN(dueAt.getTime()) &&
            dueAt <= endOfDay(now, 0);
          return (
            <span className={pressing ? styles.duePressing : undefined}>
              {formatDueAt(value, now)}
            </span>
          );
        },
      },
      {
        title: '下一动作',
        key: 'handling_action',
        width: '18%',
        render: (_, item) => (
          <Button
            className={styles.actionButton}
            onClick={() => setSelectedWorkObjectId(item.work_object_id)}
            type="primary"
          >
            {handlingActionLabels[item.handling_action]}
          </Button>
        ),
      },
    ],
    [assigneeFilters],
  );

  /*
   * 空态按 `Empty.dc.html` 的语言：先说清「为什么是空的」，再给一条能立刻做的下一步。
   * 「已完成」是后端缺口，空态必须把缺口本身说出来，不能写成「你没有已完成的事项」。
   */
  const [emptyReason, emptyNextStep] =
    view === 'done'
      ? [
          '办结数据还没有接进来。',
          '下一步：办结记录接进来后，这里会自动出现。',
        ]
      : oaItems.length === 0
        ? [
            '还没有取得可显示的 OA 事项。',
            '下一步：先在顶栏确认 OA 绑定，再点「刷新 OA 事项」。',
          ]
        : view === 'urgent'
          ? [
              '现在没有要紧的事。',
              `下一步：到「待办」里还有 ${todoItems.length} 件。`,
            ]
          : [
              '「待办」里现在没有事项。',
              `下一步：到「紧急」里还有 ${urgentItems.length} 件。`,
            ];

  const syncError = syncMutation.error;
  const requiresReauthentication =
    syncError instanceof ApiError && syncError.code === 'oa_reauthentication_required';
  const requiresBindingScope =
    syncError instanceof ApiError && syncError.code === 'oa_binding_scope_required';

  return (
    <Space
      className={styles.page}
      data-density={dockMode === 'pinned' ? 'compact' : 'comfortable'}
      orientation="vertical"
      size="large"
    >
      {/*
        画板上没有独立的大标题块，也没有页面内的凭证卡：所在页由左导航高亮表明，OA 凭证状态与
        「重新绑定」入口都在顶栏系统状态面板与 `/admin/bindings`。这里只保留一个不占版面的
        标题，供读屏软件定位本页。
      */}
      <h1 className={styles.pageTitle}>工作事项</h1>

      {listQuery.error ? (
        <Alert
          showIcon
          type="error"
          title="无法读取已保存的工作事项"
          description={errorText(listQuery.error)}
        />
      ) : null}

      {syncError ? (
        <Alert
          showIcon
          type="error"
          title={
            requiresReauthentication
              ? 'OA 凭证已失效，需要重新认证'
              : requiresBindingScope
                ? '需要先明确 OA 账号范围'
                : 'OA 同步失败'
          }
          description={
            <Space orientation="vertical">
              <span>{errorText(syncError)}</span>
              <span>仍在显示上次成功拉取的数据；请以每项的数据截至时间为准。</span>
              {requiresReauthentication ? (
                <Button
                  danger
                  onClick={() => markUnauthenticated(authGeneration)}
                >
                  重新认证
                </Button>
              ) : null}
              {requiresBindingScope ? (
                <span>请先在账号绑定中明确 OA 账号范围后再刷新。</span>
              ) : null}
            </Space>
          }
        />
      ) : null}

      {listQuery.data?.limit_exceeded ? (
        <Alert
          showIcon
          type="warning"
          title={`事项超过首版展示上限 ${listQuery.data.limit} 条`}
          description="分页只整理已取得的部分，不代表 OA 里的全部事项。"
        />
      ) : null}

      <section className={styles.listSection} aria-labelledby="work-view-label">
        <div className={styles.viewBar}>
          <span className={styles.viewLabel} id="work-view-label">
            事项分类
          </span>
          <Radio.Group
            aria-labelledby="work-view-label"
            className={styles.segmented}
            buttonStyle="solid"
            optionType="button"
            value={view}
            onChange={(event) => setView(event.target.value as WorkObjectView)}
          >
            <Radio.Button value="urgent">
              紧急<span className={styles.segmentCount} data-testid="work-count-urgent">
                {urgentItems.length}
              </span>
            </Radio.Button>
            <Radio.Button value="todo">
              待办<span className={styles.segmentCount} data-testid="work-count-todo">
                {todoItems.length}
              </span>
            </Radio.Button>
            <Radio.Button value="done">
              已完成<span className={styles.segmentCount} data-testid="work-count-done">
                {UNAVAILABLE_COUNT}
              </span>
            </Radio.Button>
          </Radio.Group>
          <p className={styles.stamp}>
            数据截至{' '}
            {newestFetchedAt ? formatFreshness(newestFetchedAt) : '尚未取得 OA 数据'}
          </p>
          <Button
            className={styles.refreshButton}
            loading={syncMutation.isPending}
            onClick={() => syncMutation.mutate(authGeneration)}
          >
            刷新 OA 事项
          </Button>
        </div>
        <p className={styles.rule}>
          紧急 = 已逾期、今明两天到期，或等你确认。一件事只会出现在一个分类里。
        </p>
        <QueryTable<OAWorkObjectView>
          rowKey="work_object_id"
          columns={columns}
          dataSource={visibleItems}
          emptyReason={emptyReason}
          emptyNextStep={emptyNextStep}
          loading={listQuery.isLoading}
          queryResetKey={view}
          tableLayout="fixed"
        />
        {/*
          汇总句照画板，但去掉「加起来就是你的全部事项」——「已完成」还没有数据源，那句话会变成
          一个查不出来的承诺。
        */}
        <p className={styles.after}>
          紧急 {urgentItems.length} 件、待办 {todoItems.length} 件，互不重叠。「已完成」还没有接进来。
        </p>
      </section>

      <Drawer
        title="工作事项详情"
        open={selectedWorkObjectId !== undefined}
        onClose={() => setSelectedWorkObjectId(undefined)}
        closable={false}
        extra={(
          <Button onClick={() => setSelectedWorkObjectId(undefined)}>
            关闭详情
          </Button>
        )}
        size={720}
        destroyOnHidden
      >
        {detailQuery.error ? (
          <Alert
            showIcon
            type="error"
            title="详情读取失败"
            description={errorText(detailQuery.error)}
          />
        ) : detailQuery.isLoading ? (
          <Spin />
        ) : detailQuery.data?.state_authority === 'internal' ? (
          <Alert
            showIcon
            type="info"
            title="内部事项暂未在此页面展示"
            description="内部事项以后在交办功能里看。"
          />
        ) : detailQuery.data ? (
          <Space orientation="vertical" size="large" style={{ width: '100%' }}>
            <Alert
              showIcon
              type="info"
              title={`OA 状态数据截至 ${formatTimestamp(detailQuery.data.source_fetched_at)}`}
              description="处理痕迹只记录你在 EternalAI 中的声明，不会改写 OA 状态。"
            />
            <Alert
              showIcon
              type="info"
              title={handlingActionLabels[detailQuery.data.handling_action]}
              description={handlingActionDescriptions[detailQuery.data.handling_action]}
            />
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="事项编号">
                {detailQuery.data.work_object_id}
              </Descriptions.Item>
              <Descriptions.Item label="来源">
                OA 待办 · {detailQuery.data.source_ref}
              </Descriptions.Item>
              <Descriptions.Item label="事项">
                {detailQuery.data.source_title}
              </Descriptions.Item>
              <Descriptions.Item label="责任人">
                {detailQuery.data.assignee_display_name}
              </Descriptions.Item>
              <Descriptions.Item label="时限">
                {detailQuery.data.due_at
                  ? formatTimestamp(detailQuery.data.due_at)
                  : 'OA 未提供'}
              </Descriptions.Item>
              <Descriptions.Item label="OA 状态">
                {detailQuery.data.source_status}
              </Descriptions.Item>
              <Descriptions.Item label="数据截至时间">
                {formatTimestamp(detailQuery.data.source_fetched_at)}
              </Descriptions.Item>
              <Descriptions.Item label="OA 接收时间">
                {detailQuery.data.source_received_at}
              </Descriptions.Item>
              <Descriptions.Item label="OA 创建时间">
                {detailQuery.data.source_created_at}
              </Descriptions.Item>
              <Descriptions.Item label="OA 流程类型">
                {detailQuery.data.source_workflow_type_id}
              </Descriptions.Item>
              <Descriptions.Item label="办理记录编号">
                {detailQuery.data.task_record_id ?? '无'}
              </Descriptions.Item>
              <Descriptions.Item label="我的处理痕迹">
                {handlingMarkTag(detailQuery.data.handling_mark)}
              </Descriptions.Item>
              <Descriptions.Item label="处理痕迹记录时间">
                {detailQuery.data.handling_marked_at
                  ? formatTimestamp(detailQuery.data.handling_marked_at)
                  : '未记录'}
              </Descriptions.Item>
            </Descriptions>
            <Flex gap={12} wrap>
              {(
                [
                  'pending_sync_confirmation',
                  'handled_elsewhere',
                ] as SetHandlingMarkRequestMark[]
              ).map((mark) => (
                <Button
                  key={mark}
                  type={detailQuery.data.handling_mark === mark ? 'primary' : 'default'}
                  loading={
                    markMutation.isPending && markMutation.variables?.mark === mark
                  }
                  disabled={markMutation.isPending}
                  onClick={() =>
                    markMutation.mutate({
                      workObjectId: detailQuery.data.work_object_id,
                      mark,
                      authGeneration,
                    })
                  }
                >
                  标记为{handlingMarkLabels[mark]}
                </Button>
              ))}
            </Flex>
          </Space>
        ) : null}
      </Drawer>
    </Space>
  );
}
