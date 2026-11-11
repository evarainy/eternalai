import { useEffect, useState } from 'react';
import type { ConversationKey } from '../zulip/narrow';
import { ForwardDialog } from './ForwardDialog';
import type { ForwardMode } from './ForwardDialog';
import { SELECT_LIMIT, useSelection } from './selectionStore';
import styles from './SelectionBar.module.css';

/**
 * 多选时替换输入框的操作条（FE_PLAN §11）：逐条转发、合并转发、交给我的 AI、取消；Esc 退出多选。
 * 「交给我的 AI」等 V1 多条来源接口（P2-IM-V1-MULTISOURCE-001）上线后接入。
 */
export function SelectionBar({ conversationKey }: { conversationKey: ConversationKey }) {
  const ids = useSelection((selection) => selection.ids);
  const clear = useSelection((selection) => selection.clear);
  const [mode, setMode] = useState<ForwardMode | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && mode === null) clear();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [clear, mode]);

  const ordered = [...ids].sort((a, b) => a - b);
  const empty = ids.length === 0;
  return (
    <div aria-label="多选操作" className={styles.bar} role="toolbar">
      <span className={styles.count}>
        已选 {ids.length} 条{ids.length >= SELECT_LIMIT ? `（最多 ${SELECT_LIMIT} 条）` : ''}
        <small>按住 Shift 点击可连选</small>
      </span>
      <button className={styles.action} disabled={empty} onClick={() => setMode('single')} type="button">逐条转发</button>
      <button className={styles.action} disabled={empty} onClick={() => setMode('merged')} type="button">合并转发</button>
      <button className={styles.action} disabled title="多条交给我的 AI（后端接口上线后开放）" type="button">交给我的 AI</button>
      <button className={styles.cancel} onClick={clear} type="button">取消</button>
      {mode ? (
        <ForwardDialog
          conversationKey={conversationKey}
          messageIds={ordered}
          mode={mode}
          onClose={() => setMode(null)}
          onSent={() => {
            setMode(null);
            clear();
          }}
        />
      ) : null}
    </div>
  );
}
