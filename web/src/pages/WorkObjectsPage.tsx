import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Descriptions,
  Drawer,
  Flex,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ApiError } from '../api/mutator';
import {
  getWorkObjectApiV1WorkObjectsWorkObjectIdGet as getWorkObject,
  listWorkObjectsApiV1WorkObjectsGet as listWorkObjects,
  setWorkObjectHandlingMarkApiV1WorkObjectsWorkObjectIdHandlingMarkPatch as setHandlingMark,
  syncWorkObjectsApiV1WorkObjectsSyncPost as syncWorkObjects,
} from '../generated/work-objects/work-objects';
import type {
  SetHandlingMarkRequestMark,
  WorkObjectListResponse,
  WorkObjectView,
} from '../generated/work-objects/work-objects.schemas';
import { useAuthStore } from '../stores/authStore';

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
  current: WorkObjectView | undefined,
  incoming: WorkObjectView,
): WorkObjectView {
  if (current === undefined) {
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
    task_record_id: incoming.task_record_id ?? current.task_record_id,
  };
}

const handlingMarkLabels: Record<SetHandlingMarkRequestMark, string> = {
  pending_sync_confirmation: '待同步完成情况',
  handled_elsewhere: '已在别处处理',
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

function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.code}: ${error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'unknown_error: 请求失败';
}

function handlingMarkTag(mark: WorkObjectView['handling_mark']) {
  if (mark === 'pending_sync_confirmation') {
    return <Tag color="gold">{handlingMarkLabels[mark]}</Tag>;
  }
  if (mark === 'handled_elsewhere') {
    return <Tag color="blue">{handlingMarkLabels[mark]}</Tag>;
  }
  return <Tag>未标记</Tag>;
}

export default function WorkObjectsPage() {
  const [selectedWorkObjectId, setSelectedWorkObjectId] = useState<string>();
  const autoSyncGeneration = useRef<number>();
  const queryClient = useQueryClient();
  const authGeneration = useAuthStore((state) => state.generation);
  const markUnauthenticated = useAuthStore((state) => state.markUnauthenticated);
  const { message } = AntApp.useApp();
  const listQueryKey = workObjectsQueryKey(authGeneration);

  const listQuery = useQuery({
    queryKey: listQueryKey,
    queryFn: listWorkObjects,
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
          queryClient.setQueryData<WorkObjectView>(
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
    queryFn: () => getWorkObject(selectedWorkObjectId as string),
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
        (current: WorkObjectView | undefined) =>
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

  const items = listQuery.data?.items ?? [];
  const newestFetchedAt = items.reduce<string | undefined>((newest, item) => {
    if (!newest || new Date(item.source_fetched_at) > new Date(newest)) {
      return item.source_fetched_at;
    }
    return newest;
  }, undefined);

  const columns: ColumnsType<WorkObjectView> = [
    {
      title: '来源',
      key: 'source',
      width: 180,
      render: (_, item) => (
        <Space orientation="vertical" size={0}>
          <Tag color="cyan">OA 待办</Tag>
          <Text type="secondary">{item.source_ref}</Text>
        </Space>
      ),
    },
    {
      title: '事项',
      dataIndex: 'source_title',
      key: 'source_title',
      width: 260,
      render: (value: string) => <Text strong>{value}</Text>,
    },
    {
      title: '责任人',
      dataIndex: 'assignee_display_name',
      key: 'assignee_display_name',
      width: 150,
    },
    {
      title: '时限',
      dataIndex: 'due_at',
      key: 'due_at',
      width: 170,
      render: (value: string | null) =>
        value ? formatTimestamp(value) : <Text type="secondary">OA 未提供</Text>,
    },
    {
      title: 'OA 状态',
      dataIndex: 'source_status',
      key: 'source_status',
      width: 140,
      render: (value: string) => <Tag color="green">{value}</Tag>,
    },
    {
      title: '数据截至时间',
      dataIndex: 'source_fetched_at',
      key: 'source_fetched_at',
      width: 220,
      render: (value: string) => (
        <Text strong style={{ color: '#7a4a00' }}>
          截至 {formatTimestamp(value)}
        </Text>
      ),
    },
    {
      title: '我的处理痕迹',
      dataIndex: 'handling_mark',
      key: 'handling_mark',
      width: 180,
      render: (value: WorkObjectView['handling_mark']) => handlingMarkTag(value),
    },
    {
      title: '操作',
      key: 'actions',
      fixed: 'right',
      width: 110,
      render: (_, item) => (
        <Button size="small" onClick={() => setSelectedWorkObjectId(item.work_object_id)}>
          查看详情
        </Button>
      ),
    },
  ];

  const syncError = syncMutation.error;
  const requiresReauthentication =
    syncError instanceof ApiError && syncError.code === 'oa_reauthentication_required';
  const requiresBindingScope =
    syncError instanceof ApiError && syncError.code === 'oa_binding_scope_required';

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Card
        styles={{ body: { padding: 28 } }}
        style={{
          border: 0,
          background:
            'linear-gradient(120deg, rgba(9, 47, 45, 0.98), rgba(25, 91, 79, 0.94))',
          boxShadow: '0 18px 48px rgba(9, 47, 45, 0.18)',
        }}
      >
        <Flex align="flex-end" justify="space-between" gap={24} wrap>
          <div>
            <Text style={{ color: '#9fe3d1', letterSpacing: 2 }}>MY WORK OBJECTS</Text>
            <Title level={2} style={{ color: '#fff', margin: '8px 0 4px' }}>
              我的工作台
            </Title>
            <Paragraph style={{ color: 'rgba(255,255,255,0.78)', marginBottom: 0 }}>
              OA 是业务状态权威；这里保存上次看到的 OA 快照和你的处理痕迹。
            </Paragraph>
          </div>
          <Space orientation="vertical" align="end">
            <Text style={{ color: '#fff' }}>当前显示 {items.length} 项</Text>
            <Text style={{ color: '#ffe0a3' }}>
              最新数据截至：{newestFetchedAt ? formatTimestamp(newestFetchedAt) : '暂无数据'}
            </Text>
            <Button
              type="primary"
              ghost
              loading={syncMutation.isPending}
              onClick={() => syncMutation.mutate(authGeneration)}
            >
              手动刷新 OA
            </Button>
          </Space>
        </Flex>
      </Card>

      {listQuery.error ? (
        <Alert
          showIcon
          type="error"
          title="无法读取已保存的 Work Object"
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
          description="当前只显示一个有界批次；本页面没有服务端分页，也不会用本地分页伪装完整数据。"
        />
      ) : null}

      <Table<WorkObjectView>
        rowKey="work_object_id"
        columns={columns}
        dataSource={items}
        loading={listQuery.isLoading}
        pagination={false}
        scroll={{ x: 1450 }}
      />

      <Drawer
        title="Work Object 详情"
        open={selectedWorkObjectId !== undefined}
        onClose={() => setSelectedWorkObjectId(undefined)}
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
        ) : detailQuery.data ? (
          <Space orientation="vertical" size="large" style={{ width: '100%' }}>
            <Alert
              showIcon
              type="info"
              title={`OA 状态数据截至 ${formatTimestamp(detailQuery.data.source_fetched_at)}`}
              description="处理痕迹只记录你在 EternalAI 中的声明，不会改写 OA 状态。"
            />
            <Descriptions bordered column={1} size="small">
              <Descriptions.Item label="Work Object ID">
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
              <Descriptions.Item label="Task Record 引用">
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
