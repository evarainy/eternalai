/** 「我的功能」卡片：保存确认、补填表单、选择功能、查询结果四种，全部用业务话显示。
 * 表格内容只来自服务端按功能定义取出的数据；卡片不改写结果。 */

import { useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Card, Checkbox, Flex, Input, InputNumber, Radio, Select, Space, Switch, Table,
  Tag, Typography,
} from 'antd';
import { ApiError } from '../../api/mutator';
import type {
  UserSkillChoice, UserSkillForm, UserSkillNeedsInput, UserSkillPayload, UserSkillProposal,
  UserSkillResult, UserSkillRowStatus,
} from '../../contracts/userSkill';
import {
  requestTakeover, runSkill, saveSkill, submitExport, trialSkill, type TrialResponse,
} from '../../api/userSkills';

const { Text, Title, Paragraph } = Typography;

type Row = UserSkillResult['tables'][number]['rows'][number] & { key: number };

const ROW_STATUS: Record<UserSkillRowStatus, { label: string; color: string }> = {
  ok: { label: '成功', color: 'green' },
  not_found: { label: '未找到', color: 'default' },
  failed: { label: '失败', color: 'red' },
  not_executed: { label: '未执行', color: 'orange' },
};

function errorText(error: unknown): string {
  if (error instanceof ApiError && typeof error.message === 'string' && error.message) {
    return error.message;
  }
  return '操作没有成功，请稍后再试。';
}

interface CardProps {
  payload: UserSkillPayload;
  sessionId: string | null;
  /** 导出已受理：由页面显示后台任务卡。 */
  onExportAccepted?: (jobId: string) => void;
  /** 把一句话填进输入框，由用户自己发出。 */
  onAsk?: (text: string) => void;
}

export function UserSkillCard(props: CardProps) {
  const [current, setCurrent] = useState<UserSkillPayload>(props.payload);
  const next = (payload: UserSkillPayload) => setCurrent(payload);
  switch (current.kind) {
    case 'proposal':
      return <ProposalView proposal={current} sessionId={props.sessionId} />;
    case 'needs_input':
      return <NeedsInputView payload={current} sessionId={props.sessionId} onResult={next} />;
    case 'choice':
      return <ChoiceView payload={current} sessionId={props.sessionId} onResult={next} />;
    default:
      return <ResultView result={current} {...props} />;
  }
}

// ---------------------------------------------------------------- form fields
export function FormFields({ form, values, onChange }: {
  form: UserSkillForm; values: Record<string, unknown>;
  onChange: (name: string, value: unknown) => void;
}) {
  const properties = form.schema.properties ?? {};
  const required = new Set(form.schema.required ?? []);
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      {Object.entries(properties).map(([name, schema]) => {
        const label = form.labels[name] ?? '填写项';
        const value = values[name];
        const enumValues = Array.isArray(schema.enum) ? schema.enum : null;
        let field;
        if (enumValues !== null) {
          field = (
            <Select style={{ minWidth: 200 }} value={value as string | undefined}
              options={enumValues.map((item) => ({ value: item as string, label: String(item) }))}
              onChange={(next) => onChange(name, next)} />
          );
        } else if (schema.type === 'boolean') {
          field = <Switch checked={value === true} onChange={(next) => onChange(name, next)} />;
        } else if (schema.type === 'integer' || schema.type === 'number') {
          field = (
            <InputNumber value={typeof value === 'number' ? value : null}
              onChange={(next) => onChange(name, next ?? undefined)} />
          );
        } else {
          field = (
            <Input value={typeof value === 'string' ? value : ''}
              placeholder={schema.format === 'date' ? '例如 2026-11-01' : undefined}
              maxLength={typeof schema.maxLength === 'number' ? schema.maxLength : undefined}
              onChange={(event) => onChange(name, event.target.value)} />
          );
        }
        return (
          <label key={name} style={{ display: 'block' }}>
            <Text>{label}{required.has(name) ? '（必填）' : ''}</Text>
            <div>{field}</div>
          </label>
        );
      })}
    </Space>
  );
}

// ---------------------------------------------------------------- result
function ResultView({ result, sessionId, onExportAccepted, onAsk }: {
  result: UserSkillResult; sessionId: string | null;
  onExportAccepted?: (jobId: string) => void; onAsk?: (text: string) => void;
}) {
  const [exportKey] = useState(() => crypto.randomUUID());
  const [phase, setPhase] = useState<'idle' | 'busy' | 'done'>('idle');
  const [notice, setNotice] = useState('');
  const statusTag = result.status === 'completed'
    ? <Tag color="green">查询完成</Tag>
    : result.status === 'partial' ? <Tag color="orange">部分完成</Tag> : <Tag color="red">没有成功</Tag>;

  const exportFile = async () => {
    if (sessionId === null || result.skill_id === null || result.version === null) return;
    setPhase('busy');
    try {
      const accepted = await submitExport(sessionId, exportKey, result.skill_id, result.version,
        result.input ?? {});
      setPhase('done');
      onExportAccepted?.(accepted.job_id);
    } catch (error) {
      setPhase('idle');
      setNotice(errorText(error));
    }
  };
  const takeover = async () => {
    if (sessionId === null || result.run_id === undefined) return;
    try {
      await requestTakeover(sessionId, result.run_id);
      onAsk?.('请接手处理刚才失败的查询');
    } catch (error) {
      setNotice(errorText(error));
    }
  };

  return (
    <Card aria-label="功能查询结果" size="small">
      <Flex align="center" gap={8} wrap>
        <Title level={5} style={{ margin: 0 }}>按「{result.name}」查询</Title>
        {statusTag}
      </Flex>
      {result.inputs.length > 0 ? (
        <Paragraph>
          <Text type="secondary">
            使用了：{result.inputs.map((item) => `${item.label} ${item.value}`).join('，')}
          </Text>
        </Paragraph>
      ) : null}
      {result.reason_label ? <Alert type="error" showIcon title={result.reason_label} /> : null}
      {result.notice_label ? <Paragraph>{result.notice_label}</Paragraph> : null}
      {result.tables.map((table) => (
        <div key={table.table_id} style={{ marginTop: 12 }}>
          <Text strong>{table.title}</Text>
          <Text type="secondary">
            （成功 {table.counts.ok ?? 0}，未找到 {table.counts.not_found ?? 0}，
            失败 {table.counts.failed ?? 0}，未执行 {table.counts.not_executed ?? 0}）
          </Text>
          {table.completeness_label ? <div><Text type="warning">{table.completeness_label}</Text></div> : null}
          <Table<Row> size="small" pagination={{ pageSize: 20, hideOnSinglePage: true }}
            rowKey={(_, index) => String(index)}
            dataSource={table.rows.map((row, index) => ({ ...row, key: index }))}
            columns={[
              ...table.columns.map((column, index) => ({
                title: column.label, key: `c${index}`,
                render: (_: unknown, row: Row) => row.cells[index] ?? '',
              })),
              {
                title: '状态', key: 'status',
                render: (_: unknown, row: Row) => (
                  <Tag color={ROW_STATUS[row.status]?.color}>
                    {ROW_STATUS[row.status]?.label ?? row.status}
                    {row.reason_label ? `：${row.reason_label}` : ''}
                  </Tag>
                ),
              },
            ]} />
        </div>
      ))}
      <Space style={{ marginTop: 8 }} wrap>
        {onExportAccepted && result.exportable && result.skill_id !== null
          && result.status !== 'failed' ? (
          <Button loading={phase === 'busy'} disabled={phase !== 'idle' || sessionId === null}
            onClick={() => void exportFile()}>
            {phase === 'done' ? '已开始导出' : '导出 Excel'}
          </Button>
        ) : null}
        {result.skill_id !== null && !result.exportable && result.status !== 'failed' ? (
          <Text type="secondary">这个功能暂不支持导出 Excel。</Text>
        ) : null}
        {onAsk && result.takeover_allowed ? (
          <Button onClick={() => void takeover()} disabled={sessionId === null}>让 AI 接手</Button>
        ) : null}
      </Space>
      {notice ? <Alert style={{ marginTop: 8 }} type="warning" showIcon title={notice} /> : null}
    </Card>
  );
}

// ---------------------------------------------------------------- needs input
function NeedsInputView({ payload, sessionId, onResult }: {
  payload: UserSkillNeedsInput; sessionId: string | null;
  onResult: (payload: UserSkillPayload) => void;
}) {
  const [values, setValues] = useState<Record<string, unknown>>(payload.form.prefill ?? {});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const run = async () => {
    if (sessionId === null) return;
    setBusy(true);
    try {
      const response = await runSkill(sessionId, payload.skill_id, payload.version, values);
      onResult(response.user_skill);
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card aria-label="补充查询内容" size="small">
      <Title level={5} style={{ marginTop: 0 }}>「{payload.name}」需要填写</Title>
      <FormFields form={payload.form} values={values}
        onChange={(name, value) => setValues((old) => ({ ...old, [name]: value }))} />
      <Button type="primary" style={{ marginTop: 8 }} loading={busy}
        disabled={sessionId === null} onClick={() => void run()}>开始查询</Button>
      {notice ? <Alert style={{ marginTop: 8 }} type="warning" showIcon title={notice} /> : null}
    </Card>
  );
}

// ---------------------------------------------------------------- choice
function ChoiceView({ payload, sessionId, onResult }: {
  payload: UserSkillChoice; sessionId: string | null;
  onResult: (payload: UserSkillPayload) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const choose = async (skillId: string, version: number) => {
    if (sessionId === null) return;
    setBusy(skillId);
    try {
      const response = await runSkill(sessionId, skillId, version, {});
      onResult(response.user_skill);
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setBusy(null);
    }
  };
  return (
    <Card aria-label="选择功能" size="small">
      <Title level={5} style={{ marginTop: 0 }}>请选择要使用的功能</Title>
      <Space direction="vertical" style={{ width: '100%' }}>
        {payload.options.map((option) => (
          <Flex key={option.skill_id} align="center" justify="space-between" gap={8} wrap>
            <span><Text strong>{option.name}</Text> <Text type="secondary">{option.purpose}</Text></span>
            <Button loading={busy === option.skill_id} disabled={busy !== null || sessionId === null}
              onClick={() => void choose(option.skill_id, option.version)}>使用</Button>
          </Flex>
        ))}
      </Space>
      {notice ? <Alert style={{ marginTop: 8 }} type="warning" showIcon title={notice} /> : null}
    </Card>
  );
}

// ---------------------------------------------------------------- proposal
function ProposalView({ proposal, sessionId }: { proposal: UserSkillProposal; sessionId: string | null }) {
  const [choices, setChoices] = useState<Record<string, string>>(() => Object.fromEntries(
    proposal.bindings.filter((b) => b.prefill !== null).map((b) => [b.binding_id, b.prefill!]),
  ));
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  const [columns, setColumns] = useState<string[]>(proposal.column_options.map((c) => c.column_id));
  const [name, setName] = useState(proposal.draft.name);
  const [purpose, setPurpose] = useState(proposal.draft.purpose);
  const [rules, setRules] = useState(proposal.draft.rules);
  const [target, setTarget] = useState<string>('new');
  const [inputs, setInputs] = useState<Record<string, unknown>>({});
  const [trial, setTrial] = useState<TrialResponse | null>(null);
  // Every edit bumps the version; a trial answer for an older version is dropped, so the
  // save button always belongs to what the card shows now.
  const editVersion = useRef(0);
  const [savedName, setSavedName] = useState('');
  const [phase, setPhase] = useState<'edit' | 'trying' | 'saving' | 'saved'>('edit');
  const [notice, setNotice] = useState('');

  const paramBindings = useMemo(() => new Map(proposal.params.map((p) => [p.binding_id, p])),
    [proposal.params]);
  const chosenKind = (binding: UserSkillProposal['bindings'][number]) => binding.candidates
    .find((candidate) => candidate.candidate_id === choices[binding.binding_id])?.kind;
  const typedBindings = proposal.bindings.filter((b) => chosenKind(b) === 'param');
  const formOfTyped: UserSkillForm = {
    schema: {
      properties: Object.fromEntries(typedBindings.map((b) => [b.binding_id,
        paramBindings.get(b.binding_id)?.schema ?? { type: 'string' }])),
      required: typedBindings.map((b) => b.binding_id),
    },
    labels: Object.fromEntries(typedBindings.map((b) => [b.binding_id, b.arg_label])),
  };
  const needsConfirm = proposal.bindings.filter((b) => {
    const choice = choices[b.binding_id];
    return choice === undefined || chosenKind(b) !== 'param';
  });
  const ready = proposal.bindings.every((b) => choices[b.binding_id] !== undefined)
    && needsConfirm.every((b) => confirmed[b.binding_id] === true) && columns.length > 0;
  const changed = () => { editVersion.current += 1; setTrial(null); setNotice(''); };

  const tryRun = async () => {
    if (sessionId === null) return;
    const version = editVersion.current;
    const trialName = name;
    setPhase('trying');
    const update = proposal.update_targets.find((item) => item.skill_id === target);
    const loopSteps = new Set(proposal.bindings.filter((b) => chosenKind(b) === 'loop_item')
      .map((b) => b.step_index));
    try {
      const response = await trialSkill(proposal.proposal_id, {
        session_id: sessionId,
        binding_choices: choices,
        for_each_choices: Object.fromEntries(proposal.for_each_offers.map(
          (offer) => [offer.offer_id, loopSteps.has(offer.step_index)])),
        column_ids: columns,
        inputs,
        name, purpose, rules,
        target: update === undefined ? { mode: 'new' }
          : { mode: 'update', skill_id: update.skill_id, expected_current_version: update.current_version },
      });
      if (version === editVersion.current) {
        setTrial(response);
        setSavedName(trialName);
      }
      setPhase('edit');
    } catch (error) {
      if (version === editVersion.current) setNotice(errorText(error));
      setPhase('edit');
    }
  };
  const save = async () => {
    if (sessionId === null || trial === null) return;
    setPhase('saving');
    try {
      await saveSkill(sessionId, trial.trial_id);
      setPhase('saved');
    } catch (error) {
      setNotice(errorText(error));
      setPhase('edit');
    }
  };

  if (phase === 'saved') {
    return (
      <Card aria-label="保存为功能" size="small">
        <Alert type="success" showIcon title={`已保存为「${savedName}」，以后可以直接说这个名字使用。`} />
      </Card>
    );
  }
  return (
    <Card aria-label="保存为功能" size="small">
      <Title level={5} style={{ marginTop: 0 }}>保存为我的功能</Title>
      {proposal.rejections.map((item) => <Alert key={item.code} type="info" showIcon title={item.label} />)}
      <Space direction="vertical" style={{ width: '100%' }}>
        <label><Text>功能名称</Text>
          <Input value={name} maxLength={40} onChange={(e) => { setName(e.target.value); changed(); }} /></label>
        <label><Text>用途</Text>
          <Input value={purpose} maxLength={120} onChange={(e) => { setPurpose(e.target.value); changed(); }} /></label>
        <label><Text>查询口径（查什么、什么范围、怎样算查完）</Text>
          <Input.TextArea value={rules} maxLength={300} autoSize
            onChange={(e) => { setRules(e.target.value); changed(); }} /></label>
        <div>
          <Text strong>步骤：</Text>
          <Text>{proposal.steps.map((step) => `${step.system_label}：${step.step_label}`).join(' → ')}</Text>
        </div>
        {proposal.bindings.length > 0 ? <Text strong>每一项怎么填：</Text> : null}
        {proposal.bindings.map((binding) => (
          <div key={binding.binding_id}>
            <Text>「{binding.step_label}」的「{binding.arg_label}」：</Text>
            <Radio.Group value={choices[binding.binding_id]}
              onChange={(event) => {
                setChoices((old) => ({ ...old, [binding.binding_id]: event.target.value }));
                setConfirmed((old) => ({ ...old, [binding.binding_id]: false }));
                changed();
              }}>
              {binding.candidates.map((candidate) => (
                <Radio key={candidate.candidate_id} value={candidate.candidate_id}>{candidate.label}</Radio>
              ))}
            </Radio.Group>
            {choices[binding.binding_id] !== undefined && chosenKind(binding) !== 'param' ? (
              <Checkbox checked={confirmed[binding.binding_id] === true}
                onChange={(event) => {
                  setConfirmed((old) => ({ ...old, [binding.binding_id]: event.target.checked }));
                  changed();
                }}>这样填是对的</Checkbox>
            ) : null}
          </div>
        ))}
        <div>
          <Text strong>显示哪些列：</Text>
          <Checkbox.Group value={columns}
            options={proposal.column_options.map((c) => ({ value: c.column_id, label: c.label }))}
            onChange={(next) => { setColumns(next as string[]); changed(); }} />
        </div>
        {proposal.update_targets.length > 0 ? (
          <div>
            <Text strong>保存方式：</Text>
            <Select style={{ minWidth: 240 }} value={target}
              onChange={(next) => { setTarget(next); changed(); }}
              options={[{ value: 'new', label: '新建一个功能' },
                ...proposal.update_targets.map((item) => ({ value: item.skill_id, label: `更新「${item.name}」` }))]} />
          </div>
        ) : null}
        {typedBindings.length > 0 ? (
          <div>
            <Text strong>试运行用的填写内容：</Text>
            <FormFields form={formOfTyped} values={inputs}
              onChange={(key, value) => { setInputs((old) => ({ ...old, [key]: value })); changed(); }} />
          </div>
        ) : null}
      </Space>
      <Space style={{ marginTop: 12 }}>
        <Button onClick={() => void tryRun()} loading={phase === 'trying'}
          disabled={!ready || sessionId === null || phase !== 'edit'}>试运行</Button>
        <Button type="primary" onClick={() => void save()} loading={phase === 'saving'}
          disabled={trial === null || !trial.savable || phase !== 'edit'}>保存</Button>
      </Space>
      {!ready ? <Paragraph type="secondary">请把每一项的来源选好并确认，至少选一列。</Paragraph> : null}
      {trial !== null && !trial.savable ? (
        <Alert type="warning" showIcon title={trial.not_savable_reason ?? '试运行没有成功，不能保存'} />
      ) : null}
      {trial !== null ? <ResultView result={trial.result} sessionId={null} /> : null}
      {notice ? <Alert style={{ marginTop: 8 }} type="warning" showIcon title={notice} /> : null}
    </Card>
  );
}
