import { useEffect, useRef, useState } from 'react';
import Alert from 'antd/es/alert';
import Button from 'antd/es/button';
import Space from 'antd/es/space';
import { ApiError } from '../../api/mutator';
import { downloadWorkAttachment, requestWorkDispatch, uploadWorkAttachment } from '../../api/workDispatchTransport';
import type { AttachmentView } from '../../api/workDispatchTransport';
import { useAuthStore } from '../../stores/authStore';
import styles from './AttachmentsPanel.module.css';

export type { AttachmentView } from '../../api/workDispatchTransport';
export interface AttachmentsPanelProps {
  workObjectId?: string;
  handlingRelationId?: string;
  purpose: AttachmentView['purpose'];
  visibility: AttachmentView['visibility'];
  value: AttachmentView[];
  onChange: (items: AttachmentView[]) => void;
  readOnly: boolean;
  etag?: string;
  onEtagChange?: (etag: string) => void;
  onAccessLost?: () => void;
}

interface PendingUpload { file: File; uploadKey: string; linkKey: string; uploaded?: AttachmentView }
interface AttachmentMutation { items: AttachmentView[]; etag: string; replayed: boolean }

export default function AttachmentsPanel(props: AttachmentsPanelProps) {
  const { workObjectId, handlingRelationId, purpose, visibility, value, onChange, readOnly, etag, onEtagChange, onAccessLost } = props;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingUpload | null>(null);
  const detachRequests = useRef(new Map<string, { key: string; etag: string }>());
  const mounted = useRef(true);
  const currentProps = useRef(props);
  currentProps.current = props;
  const generation = useAuthStore((state) => state.generation);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { setPending(null); setError(null); detachRequests.current.clear(); }, [generation, workObjectId, handlingRelationId]);
  const current = (started: number) => mounted.current && useAuthStore.getState().generation === started
    && currentProps.current.workObjectId === workObjectId && currentProps.current.handlingRelationId === handlingRelationId;
  function failed(cause: unknown) {
    if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
      setPending(null); onChange([]); onAccessLost?.();
    }
    setError(cause instanceof ApiError ? ({
      work_object_attachment_too_large: '单文件不能超过 20 MiB。', work_object_attachment_invalid: '文件名、类型或实际格式不符合要求。',
      work_object_attachment_limit_exceeded: '附件超过数量或任务总量限制。', work_object_version_conflict: '事项已更新，请重新读取再操作。',
      work_object_action_forbidden: '当前无附件操作权限。', authentication_required: '请重新登录。',
    } as Record<string, string>)[cause.code] ?? '附件操作未确认成功，请核对后重试原请求。' : '附件操作未确认成功，请重试原请求。');
  }
  async function upload(item: PendingUpload) {
    const started = generation;
    setBusy(true); setError(null); setPending(item);
    try {
      const attachment = item.uploaded ?? await uploadWorkAttachment(item.file, { purpose, idempotencyKey: item.uploadKey });
      if (!current(started)) return;
      item.uploaded = attachment;
      if (workObjectId) {
        if (!etag) throw new ApiError(428, 'work_object_precondition_required', '请先读取事项版本。');
        const result = await requestWorkDispatch<AttachmentMutation>({
          url: `/api/v1/work-objects/${encodeURIComponent(workObjectId)}/attachments`, method: 'POST',
          headers: { 'If-Match': etag, 'Idempotency-Key': item.linkKey },
          data: { attachment_ids: [attachment.attachment_id], purpose, visibility, handling_relation_id: handlingRelationId ?? null },
        });
        if (!current(started)) return;
        onChange(result.data.items.filter((entry) => entry.purpose === purpose));
        onEtagChange?.(result.etag ?? result.data.etag);
      } else onChange([...currentProps.current.value, attachment]);
      setPending(null);
    } catch (cause) { if (current(started)) failed(cause); }
    finally { if (current(started)) setBusy(false); }
  }
  async function detach(item: AttachmentView) {
    if (!workObjectId) { onChange(value.filter((entry) => entry.attachment_id !== item.attachment_id)); return; }
    const started = generation;
    setBusy(true); setError(null);
    try {
      if (!etag) throw new ApiError(428, 'work_object_precondition_required', '请先读取事项版本。');
      const original = detachRequests.current.get(item.attachment_id) ?? { key: crypto.randomUUID(), etag };
      detachRequests.current.set(item.attachment_id, original);
      const result = await requestWorkDispatch<AttachmentMutation>({
        url: `/api/v1/work-objects/${encodeURIComponent(workObjectId)}/attachments/${encodeURIComponent(item.attachment_id)}`,
        method: 'DELETE', headers: { 'If-Match': original.etag, 'Idempotency-Key': original.key },
      });
      detachRequests.current.delete(item.attachment_id);
      if (!current(started)) return;
      onChange(result.data.items.filter((entry) => entry.purpose === purpose));
      onEtagChange?.(result.etag ?? result.data.etag);
    } catch (cause) { if (current(started)) failed(cause); }
    finally { if (current(started)) setBusy(false); }
  }
  async function download(item: AttachmentView) {
    if (!workObjectId) return;
    const started = generation;
    setBusy(true); setError(null);
    try {
      const content = await downloadWorkAttachment(workObjectId, item.attachment_id);
      if (!current(started)) return;
      const url = URL.createObjectURL(content);
      const link = document.createElement('a');
      link.href = url; link.download = item.filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (cause) { if (current(started)) failed(cause); }
    finally { if (current(started)) setBusy(false); }
  }
  return <div className={styles.panel}>
    <ul className={styles.files}>{value.map((item) => <li key={item.attachment_id}>
      <span>{item.filename} · {(item.size_bytes / 1024).toFixed(1)} KiB</span>
      <Space>{workObjectId ? <Button size="small" disabled={busy} onClick={() => { void download(item); }}>下载</Button> : null}
        {!readOnly ? <Button size="small" disabled={busy || pending !== null} onClick={() => { void detach(item); }}>移除引用</Button> : null}</Space>
    </li>)}</ul>
    {!readOnly ? <label className={styles.upload}>添加附件
      <input aria-label="添加附件" type="file" accept=".pdf,.docx,.xlsx,.pptx,.jpg,.jpeg,.png" disabled={busy || pending !== null || value.length >= 10}
        onChange={(event) => {
          const file = event.target.files?.[0]; event.target.value = '';
          if (!file) return;
          if (file.size === 0 || file.size > 20 * 1024 * 1024) { setError('文件须为非空，且单文件不超过 20 MiB。'); return; }
          void upload({ file, uploadKey: crypto.randomUUID(), linkKey: crypto.randomUUID() });
        }} />
      <span>PDF、DOCX、XLSX、PPTX、JPG、PNG；单文件 20 MiB，最多 10 个，任务总量 100 MiB。</span>
    </label> : null}
    {error ? <Alert type="error" showIcon title={error} /> : null}
    {pending && !busy && !readOnly ? <Button onClick={() => { void upload(pending); }}>重试原附件请求</Button> : null}
  </div>;
}
