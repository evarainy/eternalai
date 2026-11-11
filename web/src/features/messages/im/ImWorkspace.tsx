import { useEffect } from 'react';
import type { KeyboardEvent } from 'react';
import { openConversation, useImStore } from '../zulip/imStore';
import { ChatPane } from './ChatPane';
import { ContactDetail } from './ContactDetail';
import { ContactList } from './ContactList';
import { ConversationList } from './ConversationList';
import { ImLayout } from './ImLayout';
import { ListTabs } from './ListTabs';
import { conversationRows } from './viewModel';
import { useImView } from './viewStore';
import styles from './ImLayout.module.css';

/**
 * 消息页 IM 区：只负责呈现。快照与事件接收挂在 AppShell 的登录生命周期上（`useImLifecycle`），
 * 离开消息页也继续接收、更新角标。
 */
export function ImWorkspace() {
  const phase = useImStore((state) => state.phase);
  const selected = useImStore((state) => state.selected);
  const tab = useImView((view) => view.tab);

  // 首次就绪时打开最上面的会话，之后保持用户的选择。
  useEffect(() => {
    if (phase !== 'ready' || selected) return;
    const state = useImStore.getState();
    const first = conversationRows(state)[0];
    if (first) openConversation(first.key);
  }, [phase, selected]);

  // 按锚点打开消息（返回原消息、消息链接）时，从联系人页签切回消息页签。
  const anchor = useImStore((state) => state.anchor);
  useEffect(() => {
    if (anchor) useImView.getState().showMessages();
  }, [anchor]);

  // 快捷键只在焦点处于 IM 区时生效（FE_PLAN §3「快捷键」）：Ctrl+F 会话内搜索，Alt+↑/↓ 切换会话。
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (tab !== 'messages') return;
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
      <ImLayout
        chat={tab === 'contacts' ? <ContactDetail /> : <ChatPane />}
        list={<><ListTabs />{tab === 'contacts' ? <ContactList /> : <ConversationList />}</>}
      />
    </div>
  );
}
