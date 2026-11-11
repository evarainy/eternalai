import { Fragment, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Icon } from '../../../shared/ui/Icon';
import { clearAnchor, retryConversation, returnToLatest } from '../zulip/imStore';
import type { ImState, MessageWindow } from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';
import { MessageItem } from './MessageItem';
import { needsSeparator, separatorTime } from './viewModel';
import styles from './ChatPane.module.css';

/** 每个会话离开时的滚动位置，只放内存（FE_PLAN §5.2「本机持久化」）。 */
const positions = new Map<ConversationKey, number>();

const BOTTOM_SLACK = 80;

/**
 * 消息记录：独立滚动。打开会话时按 FE_PLAN §2.1 的优先级定位——明确锚点 > 记住的位置 > 最新；
 * 只有点「回到最新」或「N 条新消息」才主动滚到底。翻看历史时显示「N 条新消息 ↓」和「有人@你」。
 */
export function MessageList({ conversationKey, state, messageWindow }: {
  conversationKey: ConversationKey;
  state: ImState;
  messageWindow: MessageWindow | undefined;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const track = useRef<HTMLDivElement>(null);
  const nearBottom = useRef(true);
  const placedKey = useRef<ConversationKey | null>(null);
  const seenLastId = useRef<number | undefined>(undefined);
  // 程序贴底触发的 scroll 事件不能改写「是否在底部」：它可能在图片撑高之后才到达。
  const autoTop = useRef(-1);
  const [newCount, setNewCount] = useState(0);
  const [mentionAbove, setMentionAbove] = useState<number | null>(null);
  const [dismissed, setDismissed] = useState<Record<number, true>>({});
  const messages = messageWindow?.messages ?? [];
  const lastId = messages[messages.length - 1]?.id;
  const hasMessages = lastId !== undefined;
  const anchorId = state.anchor?.key === conversationKey ? state.anchor.messageId : null;

  const stickToBottom = (element: HTMLDivElement) => {
    element.scrollTop = element.scrollHeight;
    autoTop.current = element.scrollTop;
    nearBottom.current = true;
    setNewCount(0);
  };

  const findMentionAbove = (element: HTMLDivElement) => {
    const top = element.getBoundingClientRect().top;
    const hidden = messages.find((message) => message.flags.includes('mentioned') && !message.flags.includes('read')
      && !dismissed[message.id]
      && (element.querySelector(`[data-message-id="${message.id}"]`)?.getBoundingClientRect().bottom ?? Infinity) < top);
    setMentionAbove(hidden?.id ?? null);
  };

  // 打开会话或窗口换批时定位；同一会话里追加消息时只在贴底状态下跟随。
  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element || lastId === undefined) return;
    if (placedKey.current !== conversationKey) {
      placedKey.current = conversationKey;
      seenLastId.current = lastId;
      setNewCount(0);
      const anchored = anchorId !== null ? element.querySelector(`[data-message-id="${anchorId}"]`) : null;
      if (anchored) {
        anchored.scrollIntoView({ block: 'center' });
        autoTop.current = element.scrollTop;
        nearBottom.current = false;
      } else if (positions.has(conversationKey)) {
        element.scrollTop = positions.get(conversationKey)!;
        autoTop.current = element.scrollTop;
        nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < BOTTOM_SLACK;
      } else {
        stickToBottom(element);
      }
      findMentionAbove(element);
      return;
    }
    const previous = seenLastId.current;
    seenLastId.current = lastId;
    if (previous === undefined || lastId <= previous) return;
    const arrived = messages.filter((message) => message.id > previous);
    const ownSend = arrived.some((message) => message.sender_id === state.selfId);
    if (nearBottom.current || ownSend) stickToBottom(element);
    else setNewCount((count) => count + arrived.length);
    // messages/state 每次渲染都变；这里只在会话或最后一条变化时定位。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationKey, lastId, anchorId]);

  // 锚点在已加载窗口里时直接定位（搜索命中、消息链接）。
  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element || anchorId === null || placedKey.current !== conversationKey) return;
    const anchored = element.querySelector(`[data-message-id="${anchorId}"]`);
    if (!anchored) return;
    anchored.scrollIntoView({ block: 'center' });
    autoTop.current = element.scrollTop;
    nearBottom.current = false;
  }, [anchorId, conversationKey, lastId]);

  // 图片等内容加载后高度会变；停在底部附近时继续贴底。
  useEffect(() => {
    const element = scroller.current;
    if (!element || !track.current) return;
    const observer = new ResizeObserver(() => {
      if (nearBottom.current) stickToBottom(element);
    });
    observer.observe(track.current);
    return () => observer.disconnect();
  }, [conversationKey, hasMessages]);

  // 离开会话时记住位置。
  useEffect(() => () => {
    placedKey.current = null;
  }, [conversationKey]);

  const onScroll = () => {
    const element = scroller.current;
    if (!element) return;
    positions.set(conversationKey, element.scrollTop);
    findMentionAbove(element);
    if (Math.abs(element.scrollTop - autoTop.current) < 2) return;
    nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < BOTTOM_SLACK;
    if (nearBottom.current) setNewCount(0);
  };

  const jumpToLatest = () => {
    clearAnchor();
    if (messageWindow && !messageWindow.foundNewest) {
      positions.delete(conversationKey);
      placedKey.current = null;
      returnToLatest(conversationKey);
      return;
    }
    if (scroller.current) stickToBottom(scroller.current);
  };

  const jumpToMention = () => {
    if (mentionAbove === null) return;
    scroller.current?.querySelector(`[data-message-id="${mentionAbove}"]`)?.scrollIntoView({ block: 'center' });
    setDismissed((current) => ({ ...current, [mentionAbove]: true }));
    setMentionAbove(null);
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
    <>
      {!messageWindow.foundNewest ? (
        <div className={styles.banner} role="status">
          <Icon name="search" size={14} />
          正在查看较早的位置
          <button onClick={jumpToLatest} type="button">回到最新消息</button>
        </div>
      ) : null}
      <div
        aria-label="消息记录"
        className={styles.messages}
        data-testid="im-messages"
        onScroll={onScroll}
        ref={scroller}
        role="log"
      >
        <div className={styles.track} ref={track}>
          {messages.map((message, index) => (
            <Fragment key={message.id}>
              {needsSeparator(messages[index - 1], message) ? (
                <div className={styles.separator}>{separatorTime(message.timestamp)}</div>
              ) : null}
              {message === firstUnread ? <div className={styles.unreadLine}>以下为新消息</div> : null}
              <MessageItem
                conversationKey={conversationKey}
                highlighted={message.id === anchorId}
                message={message}
                state={state}
              />
            </Fragment>
          ))}
        </div>
      </div>
      {mentionAbove !== null ? (
        <button className={`${styles.pill} ${styles.pillMention}`} onClick={jumpToMention} type="button">
          <Icon name="atsign" size={15} />有人@你
        </button>
      ) : null}
      {newCount > 0 ? (
        <button className={`${styles.pill} ${styles.pillNew}`} onClick={jumpToLatest} type="button">
          {newCount} 条新消息<Icon name="chevron" size={15} />
        </button>
      ) : null}
    </>
  );
}
