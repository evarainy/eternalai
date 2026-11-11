import { useMemo, useState } from 'react';
import { Icon } from '../../../shared/ui/Icon';
import { openConversation, useImStore } from '../zulip/imStore';
import type { ImState } from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';
import { Avatar } from './Avatar';
import {
  conversationAvatar, conversationTitle, listTime, messageSummary, userPresence,
} from './viewModel';
import styles from './ConversationList.module.css';

type Filter = 'all' | 'unread' | 'mentions' | 'dm' | 'group';
const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: '全部' }, { key: 'unread', label: '未读' }, { key: 'mentions', label: '@我' },
  { key: 'dm', label: '单聊' }, { key: 'group', label: '群聊' },
];

interface Row {
  key: ConversationKey;
  title: string;
  time: string;
  summary: string;
  unread: number;
  mentioned: boolean;
  muted: boolean;
  isGroup: boolean;
}

function buildRows(state: ImState): Row[] {
  const rows: (Row & { order: number; pinned: boolean })[] = [];
  for (const conversation of Object.values(state.conversations)) {
    if (!conversation) continue;
    const sub = conversation.kind === 'stream' ? state.subscriptions[conversation.streamId] : undefined;
    const latest = state.latest[conversation.key];
    const unreadIds = state.unread[conversation.key] ?? [];
    rows.push({
      key: conversation.key,
      title: conversationTitle(state, conversation),
      time: latest ? listTime(latest.timestamp) : '',
      summary: latest ? messageSummary(state, latest) : '',
      unread: unreadIds.length,
      mentioned: unreadIds.some((id) => state.mentions[id]),
      muted: sub?.is_muted ?? false,
      isGroup: conversation.kind === 'stream' || conversation.userIds.length > 1,
      order: state.order[conversation.key] ?? 0,
      pinned: sub?.pin_to_top ?? false,
    });
  }
  return rows.sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.order - a.order);
}

function matches(row: Row, filter: Filter, query: string): boolean {
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
  const rows = useMemo(() => buildRows(state), [state]);
  // 静音群不计入未读总数（FE_PLAN §3 第 1 行）。
  const total = rows.reduce((sum, row) => sum + (row.muted ? 0 : row.unread), 0);
  const visible = rows.filter((row) => matches(row, filter, query.trim().toLowerCase()));

  let body;
  if (state.phase === 'loading' || state.phase === 'idle') {
    body = <p className={styles.state} role="status">正在读取会话…</p>;
  } else if (state.phase === 'failed') {
    body = <p className={styles.state} role="alert">会话列表读取失败，请稍后刷新页面重试。</p>;
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
                      {row.mentioned ? <em>[有人@我] </em> : null}
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
        <button aria-label="发起单聊或新建群" className={styles.iconButton} disabled title="发起单聊、新建群后续接入" type="button">
          <Icon name="plus" size={17} />
        </button>
      </div>
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
