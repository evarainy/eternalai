import { useMemo, useState } from 'react';
import { Icon } from '../../../shared/ui/Icon';
import { openConversation, retryIm, useImStore } from '../zulip/imStore';
import { Avatar } from './Avatar';
import { NewChatButton } from './NewChatButton';
import { conversationAvatar, conversationRows, imFailureText, unreadTotal, userPresence } from './viewModel';
import type { ConversationRow } from './viewModel';
import styles from './ConversationList.module.css';

type Filter = 'all' | 'unread' | 'mentions' | 'dm' | 'group';
const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: '全部' }, { key: 'unread', label: '未读' }, { key: 'mentions', label: '@我' },
  { key: 'dm', label: '单聊' }, { key: 'group', label: '群聊' },
];

function matches(row: ConversationRow, filter: Filter, query: string): boolean {
  if (query && !row.title.toLowerCase().includes(query)) return false;
  if (filter === 'unread') return row.unread > 0;
  if (filter === 'mentions') return row.mentioned;
  if (filter === 'dm') return !row.isGroup;
  if (filter === 'group') return row.isGroup;
  return true;
}

export function ConversationList() {
  const state = useImStore();
  const [filter, setFilter] = useState<Filter>('all');
  const [query, setQuery] = useState('');
  const rows = useMemo(() => conversationRows(state), [state]);
  // 静音会话不计入未读总数（FE_PLAN §3 第 1 行）。
  const total = unreadTotal(state);
  const visible = rows.filter((row) => matches(row, filter, query.trim().toLowerCase()));

  let body;
  if (state.phase === 'loading' || state.phase === 'idle') {
    body = <p className={styles.state} role="status">正在读取会话…</p>;
  } else if (state.phase === 'disabled') {
    body = <p className={styles.state}>单位消息尚未启用，请联系维护人员核对连接配置。</p>;
  } else if (state.phase === 'failed') {
    const retryable = state.failure !== 'im_mapping_required' && state.failure !== 'im_identity_mismatch';
    body = (
      <p className={styles.state} role="alert">
        {imFailureText(state.failure)}
        {retryable ? <button className={styles.retry} onClick={retryIm} type="button">重新连接</button> : null}
      </p>
    );
  } else if (visible.length === 0) {
    body = <p className={styles.state}>{rows.length === 0 ? '还没有会话。' : '没有符合条件的会话。'}</p>;
  } else {
    body = (
      <ul className={styles.rows}>
        {visible.map((row) => {
          const conversation = state.conversations[row.key]!;
          const avatar = conversationAvatar(state, conversation);
          const presence = avatar.kind === 'person' && !avatar.bot ? userPresence(state, avatar.userId) : null;
          return (
            <li key={row.key}>
              <button
                aria-current={state.selected === row.key ? 'true' : undefined}
                className={styles.row}
                onClick={() => openConversation(row.key)}
                type="button"
              >
                <Avatar avatar={avatar} presence={presence} />
                <span className={styles.main}>
                  <span className={styles.line1}>
                    <span className={styles.name}>{row.title}</span>
                    {row.time ? <span className={styles.time}>{row.time}</span> : null}
                  </span>
                  <span className={styles.line2}>
                    <span className={styles.summary}>
                      {row.mentioned ? <em>[有人@我] </em> : row.draft ? <em>[草稿] </em> : null}
                      {row.summary}
                    </span>
                    {row.muted ? <span className={styles.muteIcon} title="已静音"><Icon name="belloff" size={14} /></span> : null}
                    {row.unread > 0 ? (
                      <span aria-label={`${row.unread} 条未读`} className={styles.badge} data-muted={row.muted ? 'true' : 'false'}>
                        {row.unread > 99 ? '99+' : row.unread}
                      </span>
                    ) : null}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <>
      <div className={styles.top} role="tablist" aria-label="消息与联系人">
        <button aria-selected="true" className={styles.topTab} role="tab" type="button">
          消息{total > 0 ? <span className={styles.topCount}>{total > 99 ? '99+' : total}</span> : null}
        </button>
        <button aria-selected="false" className={styles.topTab} disabled role="tab" title="联系人页后续接入" type="button">
          联系人
        </button>
      </div>
      <div className={styles.head}>
        <label className={styles.search}>
          <Icon name="search" size={15} />
          <input
            aria-label="搜索会话"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索联系人、群、聊天记录"
            type="search"
            value={query}
          />
        </label>
        <NewChatButton className={styles.iconButton ?? ''} />
      </div>
      {state.offline ? <p className={styles.offline} role="status">连接中断，正在重新连接…</p> : null}
      <div className={styles.tabs} role="group" aria-label="筛选会话">
        {FILTERS.map((item) => (
          <button
            aria-pressed={filter === item.key}
            className={styles.tab}
            key={item.key}
            onClick={() => setFilter(item.key)}
            type="button"
          >
            {item.label}
          </button>
        ))}
      </div>
      {body}
    </>
  );
}
