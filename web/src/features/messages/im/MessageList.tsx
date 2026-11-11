import { Fragment, useEffect, useLayoutEffect, useRef } from 'react';
import { retryConversation } from '../zulip/imStore';
import type { ImState, MessageWindow } from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';
import { MessageItem } from './MessageItem';
import { needsSeparator, separatorTime } from './viewModel';
import styles from './ChatPane.module.css';

/**
 * 消息记录：独立滚动，打开会话时定位到最新；停在底部附近时新消息跟随到底。锚点定位、记住滚动
 * 位置与「N 条新消息 ↓」在 lane 2 按 FE_PLAN §2.1 的优先级接入。
 */
export function MessageList({ conversationKey, state, messageWindow }: {
  conversationKey: ConversationKey;
  state: ImState;
  messageWindow: MessageWindow | undefined;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const shownKey = useRef<ConversationKey | null>(null);
  // 程序贴底触发的 scroll 事件不能改写「是否在底部」：它可能在图片撑高之后才到达。
  const autoTop = useRef(-1);
  const stickToBottom = (element: HTMLDivElement) => {
    element.scrollTop = element.scrollHeight;
    autoTop.current = element.scrollTop;
  };
  const messages = messageWindow?.messages ?? [];
  const lastId = messages[messages.length - 1]?.id;
  const hasMessages = lastId !== undefined;

  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element || lastId === undefined) return;
    if (shownKey.current !== conversationKey || nearBottom.current) {
      stickToBottom(element);
      shownKey.current = conversationKey;
      nearBottom.current = true;
    }
  }, [conversationKey, lastId]);

  // 图片等内容加载后高度会变；停在底部附近时继续贴底。
  const track = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = scroller.current;
    if (!element || !track.current) return;
    const observer = new ResizeObserver(() => {
      if (nearBottom.current) stickToBottom(element);
    });
    observer.observe(track.current);
    return () => observer.disconnect();
  }, [conversationKey, hasMessages]);

  const onScroll = () => {
    const element = scroller.current;
    if (!element || Math.abs(element.scrollTop - autoTop.current) < 2) return;
    nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
  };

  if (!messageWindow || (messageWindow.status === 'loading' && messages.length === 0)) {
    return <div className={styles.messages}><p className={styles.state} role="status">正在读取消息…</p></div>;
  }
  if (messageWindow.status === 'failed' && messages.length === 0) {
    return (
      <div className={styles.messages}>
        <p className={styles.state} role="alert">
          消息读取失败。
          <button onClick={() => retryConversation(conversationKey)} type="button">重试</button>
        </p>
      </div>
    );
  }
  if (messages.length === 0) {
    return <div className={styles.messages}><p className={styles.state}>还没有消息，说点什么吧。</p></div>;
  }

  const firstUnread = messages.find((message) => !message.flags.includes('read') && message.sender_id !== state.selfId);
  return (
    <div className={styles.messages} data-testid="im-messages" onScroll={onScroll} ref={scroller} role="log" aria-label="消息记录">
      <div className={styles.track} ref={track}>
        {messages.map((message, index) => (
          <Fragment key={message.id}>
            {needsSeparator(messages[index - 1], message) ? (
              <div className={styles.separator}>{separatorTime(message.timestamp)}</div>
            ) : null}
            {message === firstUnread ? <div className={styles.unreadLine}>以下为新消息</div> : null}
            <MessageItem message={message} state={state} />
          </Fragment>
        ))}
      </div>
    </div>
  );
}
