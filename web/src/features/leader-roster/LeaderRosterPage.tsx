import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App as AntApp,
  Button,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ApiError } from '../../api/mutator';
import { useAuthStore } from '../../stores/authStore';
import {
  LEADER_ROLE_LABELS,
  addLeaderRosterEntry,
  leaderRosterQueryKey,
  readLeaderRoster,
  readLeaderRosterCandidates,
  revokeLeaderRosterEntry,
} from './leaderRosterApi';
import type { LeaderRole, LeaderRosterEntry } from './leaderRosterApi';

const { Title, Text } = Typography;

const ERROR_TEXT: Record<string, string> = {
  role_not_allowed: '需要本单位系统管理员权限。',
  directory_scope_denied: '本单位尚未接入通讯录，暂时不能添加。',
  organization_directory_missing: '通讯录尚未完成首次同步。',
  organization_directory_stale: '通讯录已过期，请等待下一次同步。',
  organization_directory_unavailable: '通讯录暂时不可用。',
  leader_roster_unavailable: '领导名单暂时不可用。',
  leader_roster_person_not_found: '通讯录中找不到该人员。',
  leader_roster_person_ambiguous: '该人员在通讯录中属于多个部门，无法确定，暂不能添加。',
  leader_roster_entry_exists: '该人员已在这一类名单中。',
  leader_roster_entry_not_found: '名单条目不存在。',
};

function errorText(error: unknown): string {
  if (error instanceof ApiError) return ERROR_TEXT[error.code] ?? `${error.code}: ${error.message}`;
  if (error instanceof Error) return error.message;
  return '请求失败';
}

function personText(entry: LeaderRosterEntry): string {
  if (entry.directory_status === 'found') return entry.display_name ?? entry.directory_user_id;
  if (entry.directory_status === 'missing') return '目录中不存在';
  if (entry.directory_status === 'ambiguous') return '目录中有多个部门';
  return '通讯录暂不可用';
}

const roleOptions = (Object.keys(LEADER_ROLE_LABELS) as LeaderRole[]).map((value) => ({
  label: LEADER_ROLE_LABELS[value],
  value,
}));

export default function LeaderRosterPage() {
  const { message } = AntApp.useApp();
  const queryClient = useQueryClient();
  const generation = useAuthStore((state) => state.generation);
  const [role, setRole] = useState<LeaderRole>('unit_leader');
  const [person, setPerson] = useState<string>();
  const [search, setSearch] = useState('');
  const rosterKey = leaderRosterQueryKey(generation);

  const roster = useQuery({ queryKey: rosterKey, queryFn: readLeaderRoster, retry: false });
  const candidates = useQuery({
    queryKey: ['admin', 'leader-roster-candidates', generation, search],
    queryFn: () => readLeaderRosterCandidates(search),
    enabled: roster.isSuccess,
    retry: false,
  });
  const add = useMutation({
    mutationFn: () => addLeaderRosterEntry(role, person ?? ''),
    onSuccess: (entry) => {
      setPerson(undefined);
      void message.success(`已添加${LEADER_ROLE_LABELS[entry.role]}`);
      void queryClient.invalidateQueries({ queryKey: rosterKey });
    },
  });
  const revoke = useMutation({
    mutationFn: revokeLeaderRosterEntry,
    onSuccess: (result) => {
      void message.success(result.changed ? '已撤销' : '该条目此前已撤销');
      void queryClient.invalidateQueries({ queryKey: rosterKey });
    },
  });

  const columns: ColumnsType<LeaderRosterEntry> = [
    { title: '姓名', key: 'person', render: (_, entry) => personText(entry) },
    { title: '部门', dataIndex: 'department_display_name', render: (value: string | null) => value ?? '—' },
    { title: '人员 ID', dataIndex: 'directory_user_id' },
    { title: '类别', dataIndex: 'role', render: (value: LeaderRole) => LEADER_ROLE_LABELS[value] },
    {
      title: '状态',
      dataIndex: 'active',
      render: (active: boolean) => (active ? <Tag color="green">生效</Tag> : <Tag>已撤销</Tag>),
    },
    {
      title: '添加',
      key: 'created',
      render: (_, entry) => `${entry.created_by_ai_user_id} · ${new Date(entry.created_at).toLocaleString()}`,
    },
    {
      title: '撤销',
      key: 'revoked',
      render: (_, entry) => (entry.revoked_at
        ? `${entry.revoked_by_ai_user_id ?? ''} · ${new Date(entry.revoked_at).toLocaleString()}`
        : '—'),
    },
    {
      title: '操作',
      key: 'action',
      render: (_, entry) => (entry.active ? (
        <Popconfirm
          title={`确认撤销该${LEADER_ROLE_LABELS[entry.role]}？`}
          description="只撤销这一类领导身份；此人若还有其他生效的领导条目，仍保留领导派发权限。已派发的任务不受影响。"
          okText="撤销"
          cancelText="取消"
          onConfirm={() => revoke.mutate(entry.entry_id)}
        >
          <Button danger size="small" loading={revoke.isPending && revoke.variables === entry.entry_id}>
            撤销
          </Button>
        </Popconfirm>
      ) : null),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Title level={3}>领导名单</Title>
        <Text type="secondary">
          单位领导、分管领导可以向所有部门和个人交办任务，但不会因此看到自己未参与的任务。只有本单位系统管理员可以维护本单位名单。
        </Text>
      </div>
      {roster.error ? <Alert type="error" title="领导名单读取失败" description={errorText(roster.error)} /> : null}
      {roster.data?.directory_unavailable_code ? (
        <Alert
          type="warning"
          title="通讯录暂不可用"
          description={`${ERROR_TEXT[roster.data.directory_unavailable_code] ?? roster.data.directory_unavailable_code}名单仍可查看和撤销，暂不能添加。`}
        />
      ) : null}
      {roster.isSuccess ? (
        <Space wrap>
          <Select<LeaderRole> aria-label="领导类别" options={roleOptions} value={role} onChange={setRole} style={{ width: 140 }} />
          <Select<string>
            aria-label="选择人员"
            showSearch
            filterOption={false}
            placeholder="输入姓名或人员 ID 搜索"
            style={{ width: 320 }}
            value={person}
            onChange={setPerson}
            onSearch={setSearch}
            loading={candidates.isFetching}
            notFoundContent={candidates.error ? errorText(candidates.error) : '没有匹配的人员'}
            options={(candidates.data?.items ?? []).map((item) => ({
              label: `${item.display_name}（${item.department_display_name}）`,
              value: item.directory_user_id,
            }))}
          />
          <Button type="primary" disabled={!person} loading={add.isPending} onClick={() => add.mutate()}>
            添加
          </Button>
        </Space>
      ) : null}
      {add.error ? <Alert type="error" title="添加失败" description={errorText(add.error)} /> : null}
      {revoke.error ? <Alert type="error" title="撤销失败" description={errorText(revoke.error)} /> : null}
      <Table<LeaderRosterEntry>
        rowKey="entry_id"
        columns={columns}
        dataSource={roster.data?.items ?? []}
        loading={roster.isLoading}
        pagination={false}
        locale={{ emptyText: '名单为空：本单位当前没有领导派发权限人员。' }}
      />
    </Space>
  );
}
