import { useEffect, useState } from 'react';
import { App as AntApp } from 'antd';
import { sendToAi } from '../zulip/aiSource';
import { useImStore } from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';
import { ForwardDialog } from './ForwardDialog';
import type { ForwardMode } from './ForwardDialog';
import { SELECT_LIMIT, useSelection } from './selectionStore';
import styles from './SelectionBar.module.css';

/**
 * 多选时替换输入框的操作条（FE_PLAN §11）：逐条转发、合并转发、交给我的 AI、取消；Esc 退出多选。
 * 交给 AI 时所选各条作为一组来源（同一会话；旧的多话题群须同一话题）。
 */
export function SelectionBar({ conversationKey }: { conversationKey: ConversationKey }) {
  const ids = useSelection((selection) => selection.ids);
  const clear = useSelection((selection) => selection.clear);
  const [mode, setMode] = useState<ForwardMode | null>(null);
  const [sending, setSending] = useState(false);
  const { message: toast } = AntApp.useApp();
  const messages = useImStore((state) => state.windows[conversationKey]?.messages);

  // 所选消息被撤回（delete_message 事件移出窗口）时同步去掉，条数与实际一致。
  useEffect(() => {
    if (!messages) return;
    const present = new Set(messages.map((message) => message.id));
    const { ids: selected } = useSelection.getState();
    const kept = selected.filter((id) => present.has(id));
    if (kept.length !== selected.length) useSelection.setState({ ids: kept });
  }, [messages]);

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
      <button
        className={styles.action}
        disabled={empty || sending}
        onClick={() => {
          setSending(true);
          void sendToAi(conversationKey, ordered).then((result) => {
            setSending(false);
            if (result.kind === 'failed') void toast.error(result.text);
            // 只清理本次发起时的这一轮选择：期间换了会话或重新选过就不动。
            else if (result.kind === 'ok' && useSelection.getState().key === conversationKey) clear();
          });
        }}
        type="button"
      >
        {sending ? '正在交给 AI…' : '交给我的 AI'}
      </button>
      <button className={styles.cancel} onClick={clear} type="button">取消</button>
      {mode && !empty ? (
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
