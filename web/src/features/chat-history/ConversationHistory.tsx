/** 「我问过的」：本人保存的对话，按最近更新排在前；可以打开续聊、改名、删除。 */

import { useEffect, useRef, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { Button, Dropdown, Input } from 'antd';
import { ApiError } from '../../api/mutator';
import {
  currentIdentityPending, IDENTITY_SWITCHING_NOTICE, useIdentityPending,
} from '../../app/identity';
import {
  conversationMessagesApiV1ConversationsSessionIdMessagesGet as readConversation,
  deleteConversationApiV1ConversationsSessionIdDelete as deleteConversation,
  listConversationsApiV1ConversationsGet as listConversations,
  renameConversationApiV1ConversationsSessionIdPatch as renameConversation,
} from '../../generated/conversations/conversations';
import type {
  ConversationMessages,
  ConversationSummary,
} from '../../generated/conversations/conversations.schemas';
import { Icon } from '../../shared/ui/Icon';
import { useAIDockStore } from '../../stores/aiDockStore';
import { useAuthStore } from '../../stores/authStore';
import { useRefreshConversations } from './useConversations';
import styles from './ConversationHistory.module.css';

const PAGE_SIZE = 20;
const TITLE_LIMIT = 60;

function updatedLabel(value: string): string {
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

/**
 * ``currentSessionId`` is the local conversation id; a saved conversation is matched by the
 * client session id it was started with (older rows have none and never match).
 */
export function ConversationHistory({ currentSessionId, onOpen, onDeleted }: {
  currentSessionId: string | null;
  /** Called only while the login and the conversation the user left are still current. */
  onOpen: (item: ConversationSummary, conversation: ConversationMessages) => void;
  onDeleted: (item: ConversationSummary) => void;
}) {
  const authStatus = useAuthStore((state) => state.status);
  const generation = useAuthStore((state) => state.generation);
  const pending = useIdentityPending();
  const refresh = useRefreshConversations();
  const list = useInfiniteQuery({
    queryKey: ['conversations', generation],
    queryFn: ({ pageParam }) => listConversations({
      limit: PAGE_SIZE, ...(pageParam === null ? {} : { cursor: pageParam }),
    }),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    // Paused while the login is being confirmed (revalidation cancels what is in flight):
    // a reply could belong to another account.
    enabled: authStatus === 'authenticated' && pending === null,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const items = list.data?.pages.flatMap((page) => page.items) ?? [];
  const [editing, setEditing] = useState<{ sessionId: string; title: string } | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const openSequence = useRef(0);
  // Closing the panel abandons a conversation it is still reading.
  useEffect(() => {
    const sequence = openSequence;
    return () => { sequence.current += 1; };
  }, []);

  const open = async (item: ConversationSummary) => {
    const sequence = ++openSequence.current;
    const left = useAIDockStore.getState().sessionId;
    setNotice('');
    setBusy(item.session_id);
    try {
      const conversation = await readConversation(item.session_id);
      const auth = useAuthStore.getState();
      // A later click, another login or a conversation started meanwhile wins over this reply.
      if (sequence !== openSequence.current || auth.status !== 'authenticated'
          || auth.generation !== generation || currentIdentityPending() === 'switching'
          || useAIDockStore.getState().sessionId !== left) return;
      onOpen(item, conversation);
    } catch (error) {
      if (sequence !== openSequence.current) return;
      if (error instanceof ApiError && error.status === 404) {
        setNotice('这段对话已不存在。');
        refresh();
      } else {
        setNotice('暂时打不开这段对话，请稍后再试。');
      }
    } finally {
      if (sequence === openSequence.current) setBusy(null);
    }
  };

  const rename = async (sessionId: string, title: string) => {
    setNotice('');
    setBusy(sessionId);
    try {
      await renameConversation(sessionId, { title });
      setEditing(null);
    } catch {
      setNotice('改名没有成功，请稍后再试。');
    } finally {
      setBusy(null);
      refresh();
    }
  };

  const remove = async (item: ConversationSummary) => {
    setNotice('');
    setBusy(item.session_id);
    try {
      await deleteConversation(item.session_id);
      setConfirming(null);
      onDeleted(item);
    } catch {
      setNotice('删除没有成功，请稍后再试。');
    } finally {
      setBusy(null);
      refresh();
    }
  };

  return (
    <aside aria-label="我问过的" className={styles.history}>
      <h2>我问过的</h2>
      {pending === 'switching' ? <p role="status">{IDENTITY_SWITCHING_NOTICE}</p> : <>
      {list.isPending && authStatus === 'authenticated' ? <p role="status">正在读取…</p> : null}
      {list.isError ? <p role="alert">历史对话暂时无法读取，请稍后再试。</p> : null}
      {list.isSuccess && items.length === 0 ? <p>还没有保存的对话。</p> : null}
      {items.length === 0 ? null : (
        <ul aria-label="历史对话">
          {items.map((item) => {
            const current = item.client_session_id !== null
              && item.client_session_id === currentSessionId;
            const itemBusy = busy === item.session_id;
            return (
              <li key={item.session_id} data-current={current ? 'true' : 'false'}>
                {editing?.sessionId === item.session_id ? (
                  <div className={styles.edit}>
                    <Input aria-label="新的对话名称" autoFocus maxLength={TITLE_LIMIT} size="small"
                      value={editing.title}
                      onChange={(event) => setEditing({ ...editing, title: event.target.value })}
                      onPressEnter={() => {
                        if (editing.title.trim()) void rename(item.session_id, editing.title.trim());
                      }} />
                    <Button size="small" type="primary" loading={itemBusy}
                      disabled={!editing.title.trim()}
                      onClick={() => void rename(item.session_id, editing.title.trim())}>
                      保存
                    </Button>
                    <Button size="small" disabled={itemBusy} onClick={() => setEditing(null)}>取消</Button>
                  </div>
                ) : (
                  <div className={styles.row}>
                    <button aria-current={current ? 'true' : undefined} className={styles.open}
                      disabled={busy !== null} onClick={() => void open(item)} type="button">
                      <span className={styles.title}>{item.title}</span>
                      <small className="wb-num">{updatedLabel(item.updated_at)}</small>
                    </button>
                    <Dropdown trigger={['click']} disabled={busy !== null} menu={{
                      items: [
                        { key: 'rename', label: '改名' },
                        { key: 'delete', label: '删除', danger: true },
                      ],
                      onClick: ({ key }) => {
                        setNotice('');
                        if (key === 'rename') {
                          setConfirming(null);
                          setEditing({ sessionId: item.session_id, title: item.title });
                        } else {
                          setEditing(null);
                          setConfirming(item.session_id);
                        }
                      },
                    }}>
                      <button aria-label={`更多操作：${item.title}`}
                        className="wb-btn wb-btn-quiet wb-btn-icon" type="button">
                        <Icon name="more" size={16} strokeWidth={2} />
                      </button>
                    </Dropdown>
                  </div>
                )}
                {confirming === item.session_id ? (
                  <div className={styles.confirm} role="alert">
                    <span>删除后无法恢复这段对话。</span>
                    <Button danger size="small" loading={itemBusy}
                      onClick={() => void remove(item)}>删除</Button>
                    <Button size="small" disabled={itemBusy} onClick={() => setConfirming(null)}>取消</Button>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {list.hasNextPage ? (
        <Button block size="small" type="text" loading={list.isFetchingNextPage}
          disabled={pending !== null} onClick={() => void list.fetchNextPage()}>
          更多
        </Button>
      ) : null}
      {notice ? <p role="alert">{notice}</p> : null}
      </>}
    </aside>
  );
}
