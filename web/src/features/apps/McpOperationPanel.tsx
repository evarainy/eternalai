import { useEffect, useRef, useState } from 'react';
import { Button, Input } from 'antd';
import { getMcpOperation, listMcpOperations, resumeMcpOperation, safeMcpLink } from '../../api/mcp';
import type { OperationView, ResumeRequestAction } from '../../generated/mcp/mcp.schemas';
import { useAuthStore } from '../../stores/authStore';
import styles from './McpPanels.module.css';

const states: Record<OperationView['state'], string> = {
  READY: '等待执行', WAITING_LOCAL_CONFIRM: '等待你的确认', WAITING_EXTERNAL_CONFIRM: '等待业务平台本人确认',
  SENDING: '正在提交，请勿重复操作', UNKNOWN: '结果暂不确定，请核对原操作', VERIFIED_SUCCESS: '业务结果已核实',
  FAILED: '操作未完成', CANCELLED: '已取消', EXPIRED: '确认已过期',
};

export function McpOperationPanel() {
  const generation = useAuthStore((state) => state.generation);
  return <Operation key={generation} generation={generation} />;
}

function Operation({ generation }: { generation: number }) {
  const [reference, setReference] = useState(() => new URLSearchParams(window.location.search).get('operation') ?? '');
  const initialReference = useRef(reference);
  const [operations, setOperations] = useState<OperationView[]>([]);
  const [operation, setOperation] = useState<OperationView>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const live = useRef(true);
  const sequence = useRef(0);
  const inFlight = useRef(false);
  useEffect(() => {
    live.current = true;
    let cancelled = false;
    const initialSequence = sequence.current;
    void Promise.resolve().then(() => listMcpOperations()).then((rows) => {
      if (!Array.isArray(rows) || !rows.every(validOperation)) throw new Error('invalid_operation_list');
      if (cancelled || !live.current || useAuthStore.getState().generation !== generation) return;
      setOperations(rows);
      if (initialSequence === sequence.current) setOperation(rows.find((row) => row.operation_id === initialReference.current));
    }).catch(() => {
      if (!cancelled && live.current && useAuthStore.getState().generation === generation) setError('暂时无法读取原操作列表，请稍后刷新。');
    });
    return () => { cancelled = true; live.current = false; };
  }, [generation]);
  const current = (id: number) => live.current && sequence.current === id && useAuthStore.getState().generation === generation;

  async function run(action?: ResumeRequestAction) {
    if (inFlight.current || !/^[a-f0-9]{32}$/.test(reference)) return;
    inFlight.current = true;
    const id = ++sequence.current;
    setBusy(true); setError('');
    try {
      const result = action && operation
        ? await resumeMcpOperation(operation.operation_id, { action, expected_revision: operation.revision, preview_digest: operation.preview_digest })
        : await getMcpOperation(reference);
      if (!validOperation(result)) throw new Error('invalid_operation');
      if (current(id)) {
        setOperation(result);
        setOperations((rows) => [result, ...rows.filter((row) => row.operation_id !== result.operation_id)].slice(0, 50));
      }
    } catch {
      if (current(id)) { setOperation(undefined); setError('当前无法继续，请核对账号和原操作状态；不要重复新建业务。'); }
    } finally {
      inFlight.current = false;
      if (current(id)) setBusy(false);
    }
  }
  const reviewUrl = safeMcpLink(operation?.review_url);
  return <section className={styles.panel} aria-label="查看原操作">
    <h2>查看原操作</h2>
    <p className={styles.muted}>选择你在业务平台的原操作，核对动作和参数摘要后继续。不会自动重新提交。</p>
    <ul aria-label="我的原操作">
      {operations.map((item) => <li key={item.operation_id}>
        <Button disabled={busy} onClick={() => { sequence.current++; setReference(item.operation_id); setOperation(item); setError(''); }}>
          {item.service_name} · {item.action} · {states[item.state]}
        </Button>
      </li>)}
    </ul>
    <form className={styles.form} onSubmit={(event) => { event.preventDefault(); void run(); }}>
      <Input aria-label="原操作编号" value={reference} disabled={busy} onChange={(event) => { setReference(event.target.value.trim()); setOperation(undefined); sequence.current++; }} />
      <Button htmlType="submit" disabled={busy || !/^[a-f0-9]{32}$/.test(reference)}>查询状态</Button>
    </form>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {operation && <div className={styles.row}>
      <p role="status">{states[operation.state]}</p>
      <p>业务平台：{operation.service_name}；动作：{operation.action}</p>
      <dl aria-label="原参数安全摘要">{Object.entries(operation.argument_preview).map(([field, value]) => <div key={field}><dt>{field}</dt><dd>{String(value)}</dd></div>)}</dl>
      <p className={styles.muted}>仅显示允许展示的参数；确认绑定完整原参数，正文及敏感字段不会在此展开。</p>
      {operation.recovery_action === 'confirm' && <Button disabled={busy} onClick={() => void run('confirm')}>确认原操作</Button>}
      {operation.recovery_action === 'confirm' && <Button disabled={busy} onClick={() => void run('cancel')}>取消</Button>}
      {operation.recovery_action === 'takeover' && <Button disabled={busy} onClick={() => void run('takeover')}>核验重新授权并接管</Button>}
      {operation.recovery_action === 'recover' && <Button disabled={busy} onClick={() => void run('recover')}>检查中断状态</Button>}
      {operation.recovery_action === 'read_original' && <Button disabled={busy} onClick={() => void run('reconcile')}>核对原业务结果</Button>}
      {operation.recovery_action === 'manual_reconcile' && <p className={styles.muted}>请由业务平台核实原操作结果，勿重复创建。</p>}
      {reviewUrl && <a className={styles.link} href={reviewUrl} target="_blank" rel="noopener noreferrer">前往业务平台本人确认</a>}
    </div>}
  </section>;
}

function validOperation(value: unknown): value is OperationView {
  if (typeof value !== 'object' || value === null) return false;
  const op = value as Partial<OperationView>;
  return typeof op.operation_id === 'string' && /^[a-f0-9]{32}$/.test(op.operation_id) &&
    typeof op.state === 'string' && Object.prototype.hasOwnProperty.call(states, op.state) &&
    typeof op.service_name === 'string' && typeof op.action === 'string' &&
    typeof op.preview_digest === 'string' && /^[a-f0-9]{64}$/.test(op.preview_digest) &&
    Number.isInteger(op.revision) && (op.revision ?? 0) >= 1 &&
    op.argument_preview !== null && typeof op.argument_preview === 'object' && !Array.isArray(op.argument_preview) &&
    Object.values(op.argument_preview).every((item) => item === null || ['string', 'number', 'boolean'].includes(typeof item)) &&
    typeof op.recovery_action === 'string' && ['confirm', 'recover', 'read_original', 'manual_reconcile', 'takeover', 'none'].includes(op.recovery_action);
}
