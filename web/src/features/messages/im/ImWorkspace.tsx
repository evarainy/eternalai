import { useEffect } from 'react';
import type { KeyboardEvent } from 'react';
import { openConversation, resetIm, startIm, useImStore } from '../zulip/imStore';
import type { ImTransport } from '../zulip/transport';
import { ChatPane } from './ChatPane';
import { ConversationList } from './ConversationList';
import { ImLayout } from './ImLayout';
import { conversationRows } from './viewModel';
import styles from './ImLayout.module.css';

/**
 * 消息页 IM 区：挂载时按给定 transport 读快照，卸载时清空状态。由 MessagesPage 按 auth generation
 * 重新挂载；lane 3 把接收器移到 AppShell 的登录生命周期上（FE_PLAN §4.2「接收器挂在哪里」）。
 */
export function ImWorkspace({ createTransport }: { createTransport: () => ImTransport }) {
  const phase = useImStore((state) => state.phase);
  const selected = useImStore((state) => state.selected);

  useEffect(() => {
    void startIm(createTransport());
    return resetIm;
  }, [createTransport]);

  // 首次就绪时打开最上面的会话，之后保持用户的选择。
  useEffect(() => {
    if (phase !== 'ready' || selected) return;
    const state = useImStore.getState();
    const first = conversationRows(state)[0];
    if (first) openConversation(first.key);
  }, [phase, selected]);

  // 快捷键只在焦点处于 IM 区时生效（FE_PLAN §3「快捷键」）：Ctrl+F 会话内搜索，Alt+↑/↓ 切换会话。
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'f') {
      event.preventDefault();
      window.dispatchEvent(new CustomEvent('eternalai:im-search'));
      return;
    }
    if (event.altKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
      event.preventDefault();
      const state = useImStore.getState();
      const rows = conversationRows(state);
      const index = rows.findIndex((row) => row.key === state.selected);
      const next = rows[index + (event.key === 'ArrowDown' ? 1 : -1)];
      if (next) openConversation(next.key);
    }
  };

  return (
    <div className={styles.page} onKeyDown={onKeyDown}>
      <h1 className={styles.srOnly}>消息</h1>
      <ImLayout chat={<ChatPane />} list={<ConversationList />} />
    </div>
  );
}
