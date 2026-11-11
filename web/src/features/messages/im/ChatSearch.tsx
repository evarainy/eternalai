import { useEffect, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import { Icon } from '../../../shared/ui/Icon';
import { openConversation, searchConversation, useImStore } from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';
import type { ZulipMessage } from '../zulip/types';
import { messageSummary, separatorTime, userName } from './viewModel';
import styles from './ChatSearch.module.css';

type SearchState =
  | { phase: 'idle' }
  | { phase: 'loading'; query: string }
  | { phase: 'done'; query: string; results: ZulipMessage[] }
  | { phase: 'failed'; query: string };

function highlight(text: string, query: string) {
  const index = text.toLowerCase().indexOf(query.toLowerCase());
  if (!query || index < 0) return text;
  return <>{text.slice(0, index)}<mark>{text.slice(index, index + query.length)}</mark>{text.slice(index + query.length)}</>;
}

/**
 * 会话内搜索（Ctrl+F 或输入框工具栏的「聊天记录」）：在本会话 narrow 上加 search 词，点命中后按锚点
 * 定位（FE_PLAN §2.1 第 1 条）。Esc 关闭。
 */
export function ChatSearch({ conversationKey, onClose }: { conversationKey: ConversationKey; onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState<SearchState>({ phase: 'idle' });
  const input = useRef<HTMLInputElement>(null);
  const sequence = useRef(0);

  useEffect(() => {
    input.current?.focus();
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const words = query.trim();
    if (!words) return;
    const current = ++sequence.current;
    setSearch({ phase: 'loading', query: words });
    searchConversation(conversationKey, words).then(
      (results) => { if (sequence.current === current) setSearch({ phase: 'done', query: words, results }); },
      () => { if (sequence.current === current) setSearch({ phase: 'failed', query: words }); },
    );
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
    }
  };

  const state = useImStore.getState();
  return (
    <div className={styles.panel} onKeyDown={onKeyDown}>
      <form className={styles.bar} onSubmit={submit} role="search">
        <Icon name="search" size={15} />
        <input
          aria-label="在本会话中搜索"
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索本会话的聊天记录，按 Enter 查找"
          ref={input}
          type="search"
          value={query}
        />
        <button aria-label="关闭搜索" className={styles.close} onClick={onClose} type="button">
          <Icon name="close" size={15} />
        </button>
      </form>
      {search.phase === 'loading' ? <p className={styles.note} role="status">正在搜索…</p> : null}
      {search.phase === 'failed' ? <p className={styles.note} role="alert">搜索失败，请稍后重试。</p> : null}
      {search.phase === 'done' && search.results.length === 0 ? <p className={styles.note}>没有找到「{search.query}」。</p> : null}
      {search.phase === 'done' && search.results.length > 0 ? (
        <ul aria-label={`${search.results.length} 条结果`} className={styles.results}>
          {search.results.map((message) => (
            <li key={message.id}>
              <button onClick={() => openConversation(conversationKey, message.id)} type="button">
                <span className={styles.meta}>
                  <b>{userName(state, message.sender_id)}</b>
                  <span>{separatorTime(message.timestamp)}</span>
                </span>
                <span className={styles.snippet}>{highlight(messageSummary(state, message), search.query)}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
