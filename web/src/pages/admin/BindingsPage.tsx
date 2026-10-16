import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  Alert,
  App as AntApp,
  Button,
  Form,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { ApiError } from '../../api/mutator';
import { getDFormState, performDFormAction } from '../../api/dForm';
import type { DFormChoice, DFormGap, DFormHandoffView, DFormOperation } from '../../api/dForm';
import { decideBrowserFillStop, readBrowserRun } from '../../features/browser/api';
import type { BrowserRunRead } from '../../features/browser/api';
import OACredentialBindingCard from '../../components/OACredentialBindingCard';
import {
  listBindings,
  resetBinding,
  revokeBinding,
} from '../../generated/admin/admin';
import { TargetSystem } from '../../generated/admin/admin.schemas';
import type {
  AdminBindingView,
  ListBindingsParams,
} from '../../generated/admin/admin.schemas';

const { Title, Text } = Typography;

interface BindingFilterValues {
  ai_user_id?: string;
  target_system?: ListBindingsParams['target_system'];
  binding_scope?: string;
  account_set_id?: string;
  device_domain_id?: string;
}

interface SubmittedBindingQuery {
  filters: ListBindingsParams;
  revision: number;
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

const targetSystemOptions = Object.values(TargetSystem).map((value) => ({
  label: value,
  value,
}));

const dFormGaps: Record<DFormGap, string> = {
  d_form_identity_missing: '缺少已认证身份引用',
  d_form_identity_invalid: '身份引用无效或与配置不一致',
  d_form_key_reference_missing: '缺少现有密钥引用',
  d_form_pool_missing: '缺少容量池配置',
  d_form_pool_mismatch: '容量池与当前代码版本不一致',
  d_form_authorization_missing: '缺少独立授权身份或明确操作范围',
  d_form_reference_missing: '缺少明确的资源交接记录',
  d_form_binding_unavailable: '当前无法核实已有绑定',
  d_form_binding_unverified: '已有绑定尚未验证',
  d_form_subject_proof_unavailable: '缺少可核实的当前 OA 账号标记或资源范围',
  d_form_handoff_pending: '资源交接范围尚未确认',
  d_form_publication_pending: '当前版本尚未激活发布',
  d_form_review_pending: '缺少当前有效的验证与人工审核证据',
  d_form_authorization_denied: '当前身份无权执行此操作',
  d_form_resource_scope_denied: '交接记录与当前运行或资源版本不一致',
  d_form_subject_mismatch: '当前浏览器账号与已验证业务身份不一致',
  d_form_owned_deployment_missing: '缺少受控浏览器部署',
  d_form_login_contract_missing: '缺少自动登录控件配置',
};

const dFormActions: Record<DFormOperation, string> = {
  confirm_handoff: '确认已有交接范围', prepare: '准备当前版本', validate: '申请初次验证',
  request_review: '申请发布审核', confirm_review: '确认审核', activate: '激活已审核版本',
  close_owned_browser: '关闭当前受控浏览器',
};

interface DFormValues {
  selection: DFormChoice;
  session_id?: string;
  task_id?: string;
  run_id?: string;
  request_id?: string;
  category?: string;
  position?: string;
  description?: string;
}

export function DFormReadiness() {
  const [form] = Form.useForm<DFormValues>();
  const [actionError, setActionError] = useState<string>();
  const [viewedHandoff, setViewedHandoff] = useState<DFormHandoffView>();
  const [validationRun, setValidationRun] = useState<BrowserRunRead>();
  const [admittedRun, setAdmittedRun] = useState<{ task: string; run: string; session: string }>();
  const readiness = useQuery({ queryKey: ['admin', 'd-form'], queryFn: getDFormState, enabled: false });
  const action = useMutation({
    mutationFn: performDFormAction,
    onSuccess: (state, command) => {
      setActionError(undefined);
      form.setFieldsValue({ task_id: state.task_id ?? undefined, run_id: state.run_id ?? undefined });
      // request_id is the caller's canonical reference; preserve it across admission.
      if (state.request_id) form.setFieldValue('request_id', state.request_id);
      if (command.operation === 'validate' && command.parameters && command.session_id && state.task_id && state.run_id) {
        form.setFieldsValue(command.parameters);
        setAdmittedRun({ task: state.task_id, run: state.run_id, session: command.session_id });
        setValidationRun(undefined);
      }
      void readiness.refetch();
    },
    onError: (error: unknown) => setActionError(errorText(error)),
  });
  const submit = (operation: DFormOperation) => {
    const values = form.getFieldsValue();
    const fixed = readiness.data?.fixed_activation === true;
    if (!values.selection) {
      setActionError('请选择服务端已配置的对象');
      return;
    }
    if (operation !== 'confirm_handoff' &&
        !(fixed && ['prepare', 'activate'].includes(operation)) && !values.session_id) {
      setActionError('填写已有会话引用');
      return;
    }
    if (operation === 'confirm_handoff' && !viewedHandoff) {
      setActionError('请先查看服务端返回的交接版本和运行范围');
      return;
    }
    if (operation === 'validate' &&
        (!values.request_id || !values.category || !values.position || !values.description)) {
      setActionError('初次验证需要固定请求引用及完整三项输入');
      return;
    }
    if (['request_review', 'confirm_review'].includes(operation) ||
        (operation === 'activate' && !fixed)) {
      if (!values.task_id || !values.run_id || !values.request_id) {
        setActionError('填写已有验证运行与审核请求引用');
        return;
      }
    }
    if (operation === 'close_owned_browser' &&
        (!values.task_id || !values.run_id || !values.session_id)) {
      setActionError('填写当前受控浏览器对应的任务、运行与会话引用');
      return;
    }
    const review = ['request_review', 'confirm_review'].includes(operation) ||
      (operation === 'activate' && !fixed);
    action.mutate({
      operation, selection: values.selection,
      ...(values.session_id && !(fixed && ['prepare', 'activate'].includes(operation))
        ? { session_id: values.session_id } : {}),
      ...(operation === 'confirm_handoff' ? { handoff_confirmation: viewedHandoff } : {}),
      ...(review ? { task_id: values.task_id, run_id: values.run_id, request_id: values.request_id } : {}),
      ...(operation === 'close_owned_browser' ? { task_id: values.task_id, run_id: values.run_id } : {}),
      ...(operation === 'validate' ? {
        request_id: values.request_id,
        parameters: {
          category: values.category ?? '', position: values.position ?? '', description: values.description ?? '',
        },
      } : {}),
    });
  };
  const runAction = useMutation({
    mutationFn: async (decision: 'confirmed' | 'rejected' | 'read') => {
      const values = form.getFieldsValue();
      if (!values.task_id || !values.run_id || !values.session_id) throw new Error('填写已有运行与客户端会话引用');
      if (decision !== 'read' && (!validationRun?.writePreview || !admittedRun ||
          validationRun.run.task_id !== values.task_id || validationRun.run.run_id !== values.run_id ||
          admittedRun.task !== values.task_id || admittedRun.run !== values.run_id || admittedRun.session !== values.session_id)) {
        throw new Error('先读取本页申请的同一验证运行，核对封存输入后再确认');
      }
      return decision === 'read'
        ? readBrowserRun(values.task_id, values.run_id, values.session_id)
        : decideBrowserFillStop(values.task_id, values.run_id, values.session_id, decision);
    },
    onSuccess: (run) => { setActionError(undefined); setValidationRun(run); },
    onError: (error: unknown) => { setValidationRun(undefined); setActionError(errorText(error)); },
  });
  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Title level={4}>填写后停止：准备与授权</Title>
      <Text>每项操作单独确认。交接确认不证明浏览器身份；执行前仍核对真实账号。不会保存或提交表单。</Text>
      <Button onClick={() => { void readiness.refetch(); }} loading={readiness.isFetching}>读取准备状态</Button>
      {readiness.error && <Alert type="error" title="准备状态不可用" description={errorText(readiness.error)} />}
      {readiness.data && (
        <>
          <Text>配置结构：{readiness.data.structure_valid ? '有效' : '无效'}；执行条件：尚需实际运行时核验</Text>
          <Alert type="info" title="当前待办" description={
            readiness.data.gaps.length ? readiness.data.gaps.map((gap) => dFormGaps[gap]).join('；') :
              '已具备配置与发布条件；尚未读取或验证当前浏览器账号'
          } />
          <Form form={form} layout="vertical">
            <Form.Item label="已有对象" name="selection">
              <Select options={readiness.data.choices.map((choice) => ({
                value: choice, label: choice === 'installed_binding' ? '任务配置中的已有绑定' : '明确登记的 D 资源',
              }))} />
            </Form.Item>
            {(!readiness.data.fixed_activation ||
              readiness.data.available_operations.includes('close_owned_browser')) &&
              <Form.Item label="已有客户端会话引用（UUID）" name="session_id"><Input autoComplete="off" /></Form.Item>}
            {(!readiness.data.fixed_activation ||
              readiness.data.available_operations.includes('close_owned_browser')) && <Space wrap>
              <Form.Item label="验证任务引用" name="task_id"><Input /></Form.Item>
              <Form.Item label="验证运行引用" name="run_id"><Input /></Form.Item>
              {!readiness.data.fixed_activation &&
                <Form.Item label="固定请求／审核引用" name="request_id"><Input /></Form.Item>}
            </Space>}
            <Text type="secondary">{readiness.data.fixed_activation
              ? '当前批准版本可由本人准备并激活；实际填写仍须从 Chat 发起，每次填写后停止。'
              : '初次发布验证仍会等待已有填写确认。实际业务验收须从 Chat 发起完整输入。'}</Text>
            {!readiness.data.fixed_activation && <>
              <Form.Item label="报修类别（初次验证）" name="category"><Input maxLength={100} disabled={!!admittedRun} /></Form.Item>
              <Form.Item label="故障位置（初次验证）" name="position"><Input maxLength={500} disabled={!!admittedRun} /></Form.Item>
              <Form.Item label="故障描述（初次验证）" name="description"><Input.TextArea maxLength={500} disabled={!!admittedRun} /></Form.Item>
            </>}
          </Form>
          <Space wrap>
            {readiness.data.available_operations.map((operation) => (
              <Popconfirm key={operation} title={`确认${operation === 'activate' && readiness.data.fixed_activation ? '激活当前批准版本' : dFormActions[operation]}？`}
                onOpenChange={(open) => {
                  if (open && operation === 'confirm_handoff') {
                    setViewedHandoff(readiness.data?.handoff_preview ?? undefined);
                  }
                }}
                description={operation === 'confirm_handoff' && viewedHandoff
                  ? `资源版本：${viewedHandoff.resource_version}；任务：${viewedHandoff.task_id}；运行：${viewedHandoff.run_id}；版本标识：${viewedHandoff.version_token}`
                  : '只授权这一项操作；其他步骤仍须各自确认。'}
                onConfirm={() => submit(operation)} okText="确认" cancelText="取消">
                <Button disabled={action.isPending}>{operation === 'activate' && readiness.data.fixed_activation
                  ? '激活当前批准版本' : dFormActions[operation]}</Button>
              </Popconfirm>
            ))}
          </Space>
          {!readiness.data.fixed_activation && <Space wrap>
            <Button onClick={() => runAction.mutate('read')} disabled={runAction.isPending}>读取已有验证运行</Button>
            <Popconfirm title="确认本次验证填写后停止？"
              description="须先核对上方三项输入、当前运行及 D 交接；不保存或提交。"
              onConfirm={() => runAction.mutate('confirmed')} okText="确认填写" cancelText="取消">
              <Button disabled={runAction.isPending || !validationRun?.writePreview || !admittedRun}>确认本次验证填写</Button>
            </Popconfirm>
            <Button onClick={() => runAction.mutate('rejected')} disabled={runAction.isPending || !validationRun?.writePreview}>
              拒绝本次验证填写
            </Button>
          </Space>}
          {!readiness.data.fixed_activation && validationRun && <Text>验证运行状态：{validationRun.run.status}；
            {validationRun.fillStop ? `核验：${validationRun.fillStop.verification}，结果：${validationRun.fillStop.execution_status}` : '尚无填写结果'}
          </Text>}
          {(readiness.data.task_id || readiness.data.run_id) && <Text>
            当前验证运行：{readiness.data.task_id} / {readiness.data.run_id}。填写确认沿用 Chat 的运行确认入口。
          </Text>}
        </>
      )}
      {actionError && <Alert type="error" title="操作未完成" description={actionError} />}
    </Space>
  );
}

export default function BindingsPage() {
  const [submittedQuery, setSubmittedQuery] = useState<SubmittedBindingQuery>();
  const [filterError, setFilterError] = useState<string>();
  const [actionError, setActionError] = useState<string>();
  const { message } = AntApp.useApp();

  const bindingQuery = useQuery({
    queryKey: ['admin', 'bindings', submittedQuery?.filters, submittedQuery?.revision],
    queryFn: () => listBindings(submittedQuery?.filters),
    enabled: submittedQuery !== undefined,
  });

  const reportMutationError = (error: unknown) => {
    const text = errorText(error);
    setActionError(text);
    void message.error(text);
  };

  const refreshBindings = async () => {
    setActionError(undefined);
    await bindingQuery.refetch();
  };

  const revokeMutation = useMutation({
    mutationFn: (bindingId: string) => revokeBinding(bindingId),
    onSuccess: async () => {
      await refreshBindings();
      void message.success('Binding 已撤销');
    },
    onError: reportMutationError,
  });

  const resetMutation = useMutation({
    mutationFn: (bindingId: string) => resetBinding(bindingId),
    onSuccess: async () => {
      await refreshBindings();
      void message.success('Binding 已重置，请重新认证');
    },
    onError: reportMutationError,
  });

  const submitFilters = (values: BindingFilterValues) => {
    const aiUserId = values.ai_user_id?.trim();
    if (!aiUserId) {
      setSubmittedQuery(undefined);
      setFilterError('ai_user_id 必填');
      return;
    }

    const filters: ListBindingsParams = { ai_user_id: aiUserId };
    if (values.target_system) {
      filters.target_system = values.target_system;
    }
    const bindingScope = values.binding_scope?.trim();
    const accountSetId = values.account_set_id?.trim();
    const deviceDomainId = values.device_domain_id?.trim();
    if (bindingScope) {
      filters.binding_scope = bindingScope;
    }
    if (accountSetId) {
      filters.account_set_id = accountSetId;
    }
    if (deviceDomainId) {
      filters.device_domain_id = deviceDomainId;
    }

    setFilterError(undefined);
    setActionError(undefined);
    setSubmittedQuery((current) => ({
      filters,
      revision: (current?.revision ?? 0) + 1,
    }));
  };

  const columns: ColumnsType<AdminBindingView> = [
    {
      title: 'binding_id',
      dataIndex: 'binding_id',
      key: 'binding_id',
      render: (value: AdminBindingView['binding_id']) => value ?? '-',
    },
    { title: 'target_system', dataIndex: 'target_system', key: 'target_system' },
    {
      title: 'execution_identity',
      dataIndex: 'execution_identity',
      key: 'execution_identity',
    },
    { title: 'bind_status', dataIndex: 'bind_status', key: 'bind_status' },
    {
      title: 'binding_scope',
      dataIndex: 'binding_scope',
      key: 'binding_scope',
      render: (value: AdminBindingView['binding_scope']) => value ?? '-',
    },
    {
      title: 'account_set_id',
      dataIndex: 'account_set_id',
      key: 'account_set_id',
      render: (value: AdminBindingView['account_set_id']) => value ?? '-',
    },
    {
      title: 'device_domain_id',
      dataIndex: 'device_domain_id',
      key: 'device_domain_id',
      render: (value: AdminBindingView['device_domain_id']) => value ?? '-',
    },
    {
      title: 'reason_code',
      dataIndex: 'reason_code',
      key: 'reason_code',
      render: (value: AdminBindingView['reason_code']) => value ?? '-',
    },
    {
      title: '操作',
      key: 'actions',
      fixed: 'right',
      render: (_, binding) => {
        const bindingId = binding.binding_id;
        const mutationPending = revokeMutation.isPending || resetMutation.isPending;
        return (
          <Space>
            <Popconfirm
              title="确认撤销此 Binding？"
              description="撤销后，后续调用将拒绝使用该身份绑定。"
              okText="确认撤销"
              cancelText="取消"
              disabled={!bindingId}
              onConfirm={() => {
                if (bindingId) {
                  revokeMutation.mutate(bindingId);
                }
              }}
            >
              <Button
                danger
                size="small"
                disabled={!bindingId || mutationPending}
                loading={revokeMutation.isPending && revokeMutation.variables === bindingId}
              >
                撤销
              </Button>
            </Popconfirm>
            <Popconfirm
              title="确认重置此 Binding？"
              description="重置会撤销当前绑定，并要求用户重新认证。"
              okText="确认重置"
              cancelText="取消"
              disabled={!bindingId}
              onConfirm={() => {
                if (bindingId) {
                  resetMutation.mutate(bindingId);
                }
              }}
            >
              <Button
                size="small"
                disabled={!bindingId || mutationPending}
                loading={resetMutation.isPending && resetMutation.variables === bindingId}
              >
                重置
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  const queryError = bindingQuery.error ? errorText(bindingQuery.error) : undefined;

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      {/*
        2026-09-03：工作事项页按画板去掉了页面内的凭证卡，OA 密码的绑定 / 重新绑定 / 解除绑定
        落到「账号绑定」这一页——顶栏系统状态面板的「重新绑定」本来就指向这里。
      */}
      <OACredentialBindingCard />

      <div>
        <Title level={3} style={{ marginBottom: 0 }}>
          Binding 查看
        </Title>
        <Text type="secondary">查询身份绑定，并对有 binding_id 的记录执行撤销或重置。</Text>
      </div>

      <Form<BindingFilterValues> layout="inline" onFinish={submitFilters}>
        <Form.Item label="ai_user_id" name="ai_user_id">
          <Input allowClear />
        </Form.Item>
        <Form.Item label="target_system" name="target_system">
          <Select allowClear options={targetSystemOptions} style={{ width: 170 }} />
        </Form.Item>
        <Form.Item label="binding_scope" name="binding_scope">
          <Input allowClear />
        </Form.Item>
        <Form.Item label="account_set_id" name="account_set_id">
          <Input allowClear />
        </Form.Item>
        <Form.Item label="device_domain_id" name="device_domain_id">
          <Input allowClear />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={bindingQuery.isFetching}>
            查询
          </Button>
        </Form.Item>
      </Form>

      {filterError && <Alert type="info" showIcon title={filterError} />}
      {queryError && (
        <Alert type="error" showIcon title="Binding 请求失败" description={queryError} />
      )}
      {actionError && (
        <Alert type="error" showIcon title="Binding 操作失败" description={actionError} />
      )}

      {bindingQuery.data && (
        <Title level={4} style={{ marginBottom: 0 }}>
          绑定：{bindingQuery.data.ai_user_id}
        </Title>
      )}
      <Table<AdminBindingView>
        rowKey={(binding) =>
          binding.binding_id ??
          [
            binding.target_system,
            binding.execution_identity,
            binding.binding_scope,
            binding.account_set_id,
            binding.device_domain_id,
          ].join(':')
        }
        columns={columns}
        dataSource={bindingQuery.data?.items ?? []}
        loading={bindingQuery.isLoading}
        pagination={false}
        scroll={{ x: 1450 }}
      />
      <DFormReadiness />
    </Space>
  );
}
