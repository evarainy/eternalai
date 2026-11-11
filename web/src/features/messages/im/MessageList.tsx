import { Fragment, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Icon } from '../../../shared/ui/Icon';
import { clearAnchor, markVisibleRead, openConversation, retryConversation, returnToLatest } from '../zulip/imStore';
import type { ImState, MessageWindow } from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';
import { MessageItem } from './MessageItem';
import { needsSeparator, separatorTime, streamTopicNames } from './viewModel';
import styles from './ChatPane.module.css';

/** 每个会话离开时的滚动位置，只放内存（FE_PLAN §5.2「本机持久化」）。 */
const positions = new Map<ConversationKey, number>();

const BOTTOM_SLACK = 80;

const MENTION_FLAGS = ['mentioned', 'stream_wildcard_mentioned', 'topic_wildcard_mentioned', 'wildcard_mentioned'];

/** 页面在前台：标签页可见且窗口有焦点（FE_PLAN §5.2 自动标读的前提）。 */
function foreground(): boolean {
  return document.visibilityState === 'visible' && document.hasFocus();
}

/**
 * 消息记录：独立滚动。打开会话时按 FE_PLAN §2.1 的优先级定位——明确锚点 > 记住的位置 > 最新；
 * 只有点「回到最新」或「N 条新消息」才主动滚到底，贴底时新消息跟随。翻看历史时显示「N 条新消息 ↓」，
 * 视口外（上方或下方）有未读 @ 时显示「有人@你」。消息进入可见区域且页面在前台时批量标为已读。
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
  const [hiddenMention, setHiddenMention] = useState<number | null>(null);
  // 「以下为新消息」分隔线在打开会话时定下，标读后不立刻消失。
  const [unreadLine, setUnreadLine] = useState<number | null>(null);
  const visible = useRef(new Set<number>());
  const [dismissed, setDismissed] = useState<Record<number, true>>({});
  // 多话题群按话题看时，窗口里可能混进事件带来的其他话题消息，只显示这个话题的。
  const topicFilter = state.topicFilter?.key === conversationKey ? state.topicFilter.topic : null;
  const conversation = state.conversations[conversationKey];
  const showTopic = topicFilter === null && conversation !== undefined && streamTopicNames(state, conversation).length > 0;
  const loaded = messageWindow?.messages ?? [];
  const messages = topicFilter === null
    ? loaded
    : loaded.filter((message) => message.type === 'stream' && message.subject === topicFilter);
  const lastId = messages[messages.length - 1]?.id;
  const hasMessages = lastId !== undefined;
  const anchorId = state.anchor?.key === conversationKey ? state.anchor.messageId : null;

  const stickToBottom = (element: HTMLDivElement) => {
    element.scrollTop = element.scrollHeight;
    autoTop.current = element.scrollTop;
    nearBottom.current = true;
    setNewCount(0);
  };

  const findHiddenMention = (element: HTMLDivElement) => {
    const box = element.getBoundingClientRect();
    const hidden = messages.find((message) => {
      if (dismissed[message.id] || message.flags.includes('read')) return false;
      if (!message.flags.some((flag) => MENTION_FLAGS.includes(flag))) return false;
      const rect = element.querySelector(`[data-message-id="${message.id}"]`)?.getBoundingClientRect();
      return rect !== undefined && (rect.bottom < box.top || rect.top > box.bottom);
    });
    // 窗口停在较早位置时，未加载进来的未读 @ 也要提示（点按钮按锚点读取）。
    const loaded = new Set(messages.map((message) => message.id));
    const outside = (state.unread[conversationKey] ?? [])
      .find((id) => state.mentions[id] && !loaded.has(id) && !dismissed[id]);
    setHiddenMention(hidden?.id ?? outside ?? null);
  };

  const reportVisible = () => {
    if (visible.current.size > 0 && foreground()) markVisibleRead(conversationKey, [...visible.current]);
  };

  // 打开会话或窗口换批时定位；同一会话里追加消息时只在贴底状态下跟随。
  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element || lastId === undefined) return;
    if (placedKey.current !== conversationKey) {
      placedKey.current = conversationKey;
      seenLastId.current = lastId;
      setNewCount(0);
      setUnreadLine(messages.find((message) => !message.flags.includes('read') && message.sender_id !== state.selfId)?.id ?? null);
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
      findHiddenMention(element);
      return;
    }
    const previous = seenLastId.current;
    seenLastId.current = lastId;
    if (previous === undefined || lastId <= previous) return;
    const arrived = messages.filter((message) => message.id > previous);
    if (nearBottom.current) stickToBottom(element);
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

  // 新消息、标读、编辑后重算视口外的 @，并把仍在可见区的未读再报一次。
  useEffect(() => {
    if (scroller.current) findHiddenMention(scroller.current);
    reportVisible();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messageWindow, dismissed, state.unread[conversationKey]]);

  // 可见区跟踪：消息露出一半以上（或占满半屏）算看到。
  useEffect(() => {
    const element = scroller.current;
    if (!element || !track.current) return;
    const seen = visible.current;
    seen.clear();
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const id = Number((entry.target as HTMLElement).dataset.messageId);
        const enough = entry.intersectionRatio >= 0.5 || entry.intersectionRect.height >= element.clientHeight / 2;
        if (entry.isIntersecting && enough) seen.add(id);
        else seen.delete(id);
      }
      reportVisible();
    }, { root: element, threshold: [0, 0.5, 1] });
    for (const node of track.current.querySelectorAll('[data-message-id]')) observer.observe(node);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationKey, messageWindow?.messages]);

  // 页面回到前台时补报。
  useEffect(() => {
    const onFront = () => reportVisible();
    window.addEventListener('focus', onFront);
    document.addEventListener('visibilitychange', onFront);
    return () => {
      window.removeEventListener('focus', onFront);
      document.removeEventListener('visibilitychange', onFront);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationKey]);

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
    findHiddenMention(element);
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
    if (hiddenMention === null) return;
    const node = scroller.current?.querySelector(`[data-message-id="${hiddenMention}"]`);
    if (node) node.scrollIntoView({ block: 'center' });
    else openConversation(conversationKey, hiddenMention);
    setDismissed((current) => ({ ...current, [hiddenMention]: true }));
    setHiddenMention(null);
  };

  // 窗口不在最新处时，新消息不进窗口：按本会话比窗口更新的未读来提示。
  const pendingNew = messageWindow && !messageWindow.foundNewest && lastId !== undefined
    ? (state.unread[conversationKey] ?? []).filter((id) => id > lastId).length
    : newCount;

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

  return (
    <>
      {!messageWindow.foundNewest ? (
        <div className={styles.banner} role="status">
          <Icon name="search" size={14} />
          {state.anchor?.key === conversationKey && state.anchor.fromSearch ? '正在查看搜索结果所在位置' : '正在查看较早的位置'}
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
              {message.id === unreadLine ? <div className={styles.unreadLine}>以下为新消息</div> : null}
              <MessageItem
                conversationKey={conversationKey}
                highlighted={message.id === anchorId}
                message={message}
                state={state}
                topic={showTopic && message.type === 'stream' ? message.subject : null}
              />
            </Fragment>
          ))}
        </div>
      </div>
      {hiddenMention !== null ? (
        <button className={`${styles.pill} ${styles.pillMention}`} onClick={jumpToMention} type="button">
          <Icon name="atsign" size={15} />有人@你
        </button>
      ) : null}
      {pendingNew > 0 ? (
        <button className={`${styles.pill} ${styles.pillNew}`} onClick={jumpToLatest} type="button">
          {pendingNew} 条新消息<Icon name="chevron" size={15} />
        </button>
      ) : null}
    </>
  );
}
