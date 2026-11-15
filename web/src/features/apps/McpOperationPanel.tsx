import { useEffect, useRef, useState } from 'react';
import { getMcpOperation, listMcpOperations, resumeMcpOperation, safeMcpLink } from '../../api/mcp';
import type { OperationView, ResumeRequestAction } from '../../generated/mcp/mcp.schemas';
import { fieldLabel } from '../../contracts/businessLabels';
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
  const act = (label: string, action: ResumeRequestAction, primary = false) => (
    <button
      className={primary ? 'wb-btn wb-btn-primary wb-btn-sm' : 'wb-btn wb-btn-sm'}
      disabled={busy}
      onClick={() => { void run(action); }}
      type="button"
    >
      {label}
    </button>
  );
  return (
    <section aria-label="查看原操作" className={styles.panel}>
      <h2>查看原操作</h2>
      <p className={styles.muted}>选择你在业务平台的原操作，核对动作和参数摘要后继续。不会自动重新提交。</p>
      <ul aria-label="我的原操作">
        {operations.map((item) => (
          <li key={item.operation_id}>
            <button
              aria-pressed={operation?.operation_id === item.operation_id}
              className={styles.pick}
              disabled={busy}
              onClick={() => { sequence.current++; setReference(item.operation_id); setOperation(item); setError(''); }}
              type="button"
            >
              {item.service_name} · {item.action} · {states[item.state]}
            </button>
          </li>
        ))}
      </ul>
      <form className={styles.form} onSubmit={(event) => { event.preventDefault(); void run(); }}>
        <label className="wb-field" data-focus-ring="host">
          <input
            aria-label="原操作编号"
            disabled={busy}
            onChange={(event) => { setReference(event.target.value.trim()); setOperation(undefined); sequence.current++; }}
            placeholder="原操作编号"
            type="text"
            value={reference}
          />
        </label>
        <button className="wb-btn wb-btn-sm" disabled={busy || !/^[a-f0-9]{32}$/.test(reference)} type="submit">查询状态</button>
      </form>
      {error ? <p className={styles.error} role="alert">{error}</p> : null}
      {operation ? (
        <div className={styles.row}>
          <p className={styles.state} role="status">{states[operation.state]}</p>
          <p>业务平台：{operation.service_name}；操作：{operation.action}</p>
          <dl aria-label="原参数安全摘要">
            {Object.entries(operation.argument_preview).map(([field, value]) => (
              <div key={field}><dt>{fieldLabel(field)}</dt><dd>{String(value)}</dd></div>
            ))}
          </dl>
          {operation.state === 'VERIFIED_SUCCESS' && operation.result
            ? <pre aria-label="已核实业务结果">{JSON.stringify(operation.result, null, 2)}</pre> : null}
          <p className={styles.muted}>这里只列出部分内容，确认后按原操作的全部内容办理。</p>
          {operation.recovery_action === 'confirm' ? act('确认原操作', 'confirm', true) : null}
          {operation.recovery_action === 'confirm' ? act('取消', 'cancel') : null}
          {operation.recovery_action === 'takeover' ? act('核验重新授权并接管', 'takeover', true) : null}
          {operation.recovery_action === 'recover' ? act('检查中断状态', 'recover', true) : null}
          {operation.recovery_action === 'read_original' ? act('核对原业务结果', 'reconcile', true) : null}
          {operation.recovery_action === 'manual_reconcile'
            ? <p className={styles.muted}>请由业务平台核实原操作结果，勿重复创建。</p> : null}
          {reviewUrl ? (
            <a className={styles.link} href={reviewUrl} rel="noopener noreferrer" target="_blank">前往业务平台本人确认</a>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function validOperation(value: unknown): value is OperationView {
  if (typeof value !== 'object' || value === null) return false;
  if (Object.keys(value).some((key) => ![
    'operation_id', 'service_config_id', 'state', 'revision', 'expires_at', 'action',
    'service_name', 'argument_preview', 'preview_digest', 'review_url', 'recovery_action', 'result',
  ].includes(key))) return false;
  const op = value as Partial<OperationView>;
  if (op.result !== undefined && op.result !== null &&
      (op.state !== 'VERIFIED_SUCCESS' || typeof op.result !== 'object' || Array.isArray(op.result))) return false;
  return typeof op.operation_id === 'string' && /^[a-f0-9]{32}$/.test(op.operation_id) &&
    typeof op.state === 'string' && Object.prototype.hasOwnProperty.call(states, op.state) &&
    typeof op.service_name === 'string' && typeof op.action === 'string' &&
    typeof op.preview_digest === 'string' && /^[a-f0-9]{64}$/.test(op.preview_digest) &&
    Number.isInteger(op.revision) && (op.revision ?? 0) >= 1 &&
    op.argument_preview !== null && typeof op.argument_preview === 'object' && !Array.isArray(op.argument_preview) &&
    Object.values(op.argument_preview).every((item) => item === null || ['string', 'number', 'boolean'].includes(typeof item)) &&
    typeof op.recovery_action === 'string' && ['confirm', 'recover', 'read_original', 'manual_reconcile', 'takeover', 'none'].includes(op.recovery_action);
}
