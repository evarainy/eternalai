import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App as AntApp,
  Button,
  Card,
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
import OACredentialBindingCard from '../components/OACredentialBindingCard';
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

const { Paragraph, Text, Title } = Typography;

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

function isTodayWorkObject(item: OAWorkObjectView, now: Date): boolean {
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
  const endOfToday = new Date(now);
  endOfToday.setHours(23, 59, 59, 999);
  return dueAt <= endOfToday;
}

function dueTimestamp(value: string | null): number {
  if (value === null) {
    return Number.POSITIVE_INFINITY;
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Number.POSITIVE_INFINITY : parsed;
}

function dueStatus(value: string | null): {
  className: string | undefined;
  icon: IconName;
  label: string;
} {
  if (value === null) {
    return { className: styles.neutralStatus, icon: 'clock', label: '未提供截止时间' };
  }
  const dueAt = new Date(value);
  if (Number.isNaN(dueAt.getTime())) {
    return { className: styles.neutralStatus, icon: 'clock', label: '截止时间格式异常' };
  }
  const now = new Date();
  const endOfToday = new Date(now);
  endOfToday.setHours(23, 59, 59, 999);
  if (dueAt < now) {
    return { className: styles.errorStatus, icon: 'alert', label: '已逾期' };
  }
  if (dueAt <= endOfToday) {
    return { className: styles.warningStatus, icon: 'alert', label: '今日截止' };
  }
  return { className: styles.neutralStatus, icon: 'clock', label: '尚未到期' };
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

export default function WorkObjectsPage() {
  const [selectedWorkObjectId, setSelectedWorkObjectId] = useState<string>();
  const [view, setView] = useState<'today' | 'all'>('today');
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

  const visibleItems = useMemo(
    () =>
      view === 'today'
        ? oaItems.filter((item) => isTodayWorkObject(item, new Date()))
        : oaItems,
    [oaItems, view],
  );
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
        title: '标题',
        dataIndex: 'source_title',
        key: 'source_title',
        width: '42%',
        render: (value: string, item) => (
          <div className={styles.workItemTitle}>
            <Text strong>{value}</Text>
            <div className={styles.sourceLine}>
              <span>OA</span>
              <span>{item.source_ref}</span>
              <span>当前步骤：{item.source_status}</span>
              <span>数据截至：{formatTimestamp(item.source_fetched_at)}</span>
            </div>
            {item.handling_mark === null ? null : handlingMarkTag(item.handling_mark)}
          </div>
        ),
      },
      {
        title: '责任人或责任部门',
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
        width: '20%',
        sorter: (left, right) => dueTimestamp(left.due_at) - dueTimestamp(right.due_at),
        render: (value: string | null) => {
          const status = dueStatus(value);
          return (
            <div className={styles.dueCell}>
              <span className={status.className}>
                <Icon name={status.icon} size={16} /> {status.label}
              </span>
              <span>{value ? formatTimestamp(value) : 'OA 未提供'}</span>
            </div>
          );
        },
      },
      {
        title: '下一动作',
        key: 'handling_action',
        width: '20%',
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
      <Card className={styles.hero} styles={{ body: { padding: 28 } }}>
        <Flex align="center" justify="space-between" gap={24} wrap>
          <div>
            <Title level={1} className={styles.heroTitle}>
              工作事项
            </Title>
            <Paragraph className={styles.heroCopy}>
              每一行都写明责任人、截止时间和下一步。
            </Paragraph>
          </div>
          <Space orientation="vertical" align="end" className={styles.heroStatus}>
            <Text>
              <Icon name="list" size={16} /> <span>当前显示 {visibleItems.length} 项</span>
            </Text>
            <Text>
              <Icon name="clock" size={16} /> 最新数据截至：
              {newestFetchedAt ? formatTimestamp(newestFetchedAt) : '尚未取得 OA 数据'}
            </Text>
            <Button
              type="primary"
              loading={syncMutation.isPending}
              onClick={() => syncMutation.mutate(authGeneration)}
            >
              刷新 OA 事项
            </Button>
          </Space>
        </Flex>
      </Card>

      <OACredentialBindingCard />

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
          <div>
            <span className={styles.viewLabel} id="work-view-label">查看范围</span>
            <p className={styles.viewHint}>“今日”包括今天截止、已经逾期和等待你确认的事项。</p>
          </div>
          <Radio.Group
            aria-labelledby="work-view-label"
            buttonStyle="solid"
            optionType="button"
            value={view}
            onChange={(event) => setView(event.target.value as 'today' | 'all')}
          >
            <Radio.Button value="today">今日</Radio.Button>
            <Radio.Button value="all">全部</Radio.Button>
          </Radio.Group>
        </div>
      <QueryTable<OAWorkObjectView>
        rowKey="work_object_id"
        columns={columns}
        dataSource={visibleItems}
        emptyReason={
          view === 'today'
            ? '今日为空，因为没有今天截止、已经逾期或等待确认的事项。'
            : '全部为空，因为目前还没有取得可显示的 OA 事项。'
        }
        emptyNextStep={
          view === 'today'
            ? '下一步：可切换到“全部”查看以后要办的事项，或刷新 OA 事项。'
            : '下一步：先检查 OA 账号绑定状态，再选择“刷新 OA 事项”。'
        }
        loading={listQuery.isLoading}
        queryResetKey={view}
        tableLayout="fixed"
      />
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
