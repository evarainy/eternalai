import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { SEARCH_LIMIT, openConversation, openMessage, searchMessages, useImStore } from '../zulip/imStore';
import { messageConversation, streamConversation } from '../zulip/narrow';
import type { Conversation } from '../zulip/narrow';
import type { ImState } from '../zulip/imStore';
import type { ZulipMessage } from '../zulip/types';
import { Avatar } from './Avatar';
import { FileIcon } from './MessageBody';
import {
  conversationAvatar, conversationTitle, listTime, messageShape, messageText, personAvatar, userDept, userName,
  userPresence, userTitle,
} from './viewModel';
import { chatWith } from './viewStore';
import list from './ConversationList.module.css';
import styles from './SearchResults.module.css';

type Tab = 'all' | 'messages' | 'files' | 'groups' | 'contacts';
const TABS: { key: Tab; label: string }[] = [
  { key: 'all', label: '全部' }, { key: 'messages', label: '消息' }, { key: 'files', label: '文件' },
  { key: 'groups', label: '群聊' }, { key: 'contacts', label: '联系人' },
];
/** 「全部」页签里每类先显示几条，其余点「查看全部」。 */
const PREVIEW = 3;
const DEBOUNCE_MS = 350;

type Remote = { status: 'loading' } | { status: 'failed' } | { status: 'ready'; messages: ZulipMessage[]; more: boolean };

function terms(query: string): string[] {
  return query.toLowerCase().split(/\s+/).filter(Boolean);
}

/** 命中词高亮：按空格拆开的每个词都标出来，不区分大小写。 */
function Highlight({ text, words }: { text: string; words: string[] }) {
  const pattern = new RegExp(`(${words.map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi');
  return <>{text.split(pattern).map((part, index) => (index % 2 === 1 ? <mark key={index}>{part}</mark> : part))}</>;
}

/** 摘要从第一个命中词前几个字开始，避免命中处被省略号截掉。 */
function snippet(text: string, words: string[]): string {
  const lower = text.toLowerCase();
  const index = Math.min(...words.map((word) => lower.indexOf(word)).filter((at) => at >= 0), Infinity);
  return index !== Infinity && index > 14 ? `…${text.slice(index - 10)}` : text;
}

/** 命中所在：群和多人私聊写会话名，一对一单聊写「单聊」。 */
function whereText(state: ImState, conversation: Conversation): string {
  return conversation.kind === 'dm' && conversation.userIds.length === 1 ? '单聊' : conversationTitle(state, conversation);
}

function countLabel(count: number, more: boolean): string {
  return more ? `${count}+ 条` : `${count} 条`;
}

function Section({ title, children, onMore }: { title: string; children: ReactNode; onMore?: (() => void) | null }) {
  return (
    <section className={styles.section}>
      <h3 className={styles.title}>{title}</h3>
      <ul className={styles.items}>{children}</ul>
      {onMore ? <button className={styles.more} onClick={onMore} type="button">查看全部</button> : null}
    </section>
  );
}

function MessageHit({ message, words }: { message: ZulipMessage; words: string[] }) {
  const state = useImStore.getState();
  const conversation = messageConversation(message, state.selfId);
  const where = whereText(state, conversation);
  return (
    <li>
      <button className={styles.hit} onClick={() => openMessage(message, true)} type="button">
        <Avatar avatar={conversationAvatar(state, conversation)} size={36} />
        <span className={styles.main}>
          <span className={styles.line1}>
            <span className={styles.name}>{userName(state, message.sender_id)} · {where}</span>
            <span className={styles.time}>{listTime(message.timestamp)}</span>
          </span>
          <span className={styles.text}><Highlight text={snippet(messageText(message.content), words)} words={words} /></span>
        </span>
      </button>
    </li>
  );
}

function FileHit({ message, words }: { message: ZulipMessage; words: string[] }) {
  const state = useImStore.getState();
  const shape = messageShape(message.content);
  const conversation = messageConversation(message, state.selfId);
  const where = whereText(state, conversation);
  const name = shape.kind === 'file' ? shape.name : messageText(message.content);
  return (
    <li>
      <button className={styles.hit} onClick={() => openMessage(message, true)} type="button">
        <span className={styles.fileIcon}>
          <FileIcon ext={shape.kind === 'file' ? shape.ext : ''} kind={shape.kind === 'file' ? shape.fileKind : 'other'} />
        </span>
        <span className={styles.main}>
          <span className={styles.line1}>
            <span className={styles.name}><Highlight text={name} words={words} /></span>
            <span className={styles.time}>{listTime(message.timestamp)}</span>
          </span>
          <span className={styles.meta}>{userName(state, message.sender_id)} · {where}</span>
        </span>
      </button>
    </li>
  );
}

/** 群聊：群名或成员姓名命中。只在本人已加入的群里查。 */
function matchGroups(state: ImState, words: string[]): { streamId: number; byMember: boolean }[] {
  const hit = (text: string) => words.every((word) => text.toLowerCase().includes(word));
  return Object.values(state.subscriptions).flatMap((sub): { streamId: number; byMember: boolean }[] => {
    if (hit(sub.name)) return [{ streamId: sub.stream_id, byMember: false }];
    const member = (sub.subscribers ?? []).some((id) => id !== state.selfId && hit(userName(state, id)));
    return member ? [{ streamId: sub.stream_id, byMember: true }] : [];
  });
}

function matchContacts(state: ImState, words: string[]): number[] {
  return Object.values(state.users)
    .filter((user) => user.is_active && user.user_id !== state.selfId)
    .filter((user) => {
      const text = [user.full_name, userDept(state, user.user_id), userTitle(state, user.user_id)].join(' ').toLowerCase();
      return words.every((word) => text.includes(word));
    })
    .map((user) => user.user_id);
}

function useRemote(query: string, files: boolean): Remote {
  const [remote, setRemote] = useState<Remote>({ status: 'loading' });
  useEffect(() => {
    let live = true;
    setRemote({ status: 'loading' });
    const timer = window.setTimeout(() => {
      searchMessages(query, files).then(
        (result) => live && setRemote({ status: 'ready', ...result }),
        () => live && setRemote({ status: 'failed' }),
      );
    }, DEBOUNCE_MS);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [query, files]);
  return remote;
}

function RemoteState({ status, what }: { status: 'loading' | 'failed'; what: string }) {
  return status === 'loading'
    ? <p className={styles.note} role="status">正在搜索{what}…</p>
    : <p className={styles.note} role="alert">{what}搜索失败，请稍后重试。</p>;
}

/**
 * 搜索页（v3-5，FE_PLAN §3 搜索页一行）：消息和文件走服务端搜索（`GET /messages` 的 search narrow），群聊和
 * 联系人在已取得的订阅和人员目录里查。数量用实际返回的条数；点消息或文件按锚点定位到原消息。
 */
export function SearchResults({ query }: { query: string }) {
  const state = useImStore();
  const [tab, setTab] = useState<Tab>('all');
  const words = terms(query);
  const messages = useRemote(query, false);
  const files = useRemote(query, true);
  const groups = matchGroups(state, words);
  const contacts = matchContacts(state, words);
  const all = tab === 'all';
  const limit = <T,>(items: T[]) => (all ? items.slice(0, PREVIEW) : items);
  const more = (items: unknown[], next: Tab) => (all && items.length > PREVIEW ? () => setTab(next) : null);

  const sections: ReactNode[] = [];
  if (all || tab === 'messages') {
    const found = messages.status === 'ready' ? messages.messages : [];
    sections.push(messages.status !== 'ready' ? <RemoteState key="m" status={messages.status} what="聊天记录" /> : found.length > 0 ? (
      <Section key="m" onMore={more(found, 'messages')} title={`聊天记录 · ${countLabel(found.length, messages.more)}`}>
        {limit(found).map((message) => <MessageHit key={message.id} message={message} words={words} />)}
      </Section>
    ) : null);
  }
  if (all || tab === 'files') {
    const found = files.status === 'ready' ? files.messages : [];
    sections.push(files.status !== 'ready' ? <RemoteState key="f" status={files.status} what="文件" /> : found.length > 0 ? (
      <Section key="f" onMore={more(found, 'files')} title={`文件 · ${countLabel(found.length, files.more)}`}>
        {limit(found).map((message) => <FileHit key={message.id} message={message} words={words} />)}
      </Section>
    ) : null);
  }
  if ((all || tab === 'groups') && groups.length > 0) {
    sections.push(
      <Section key="g" onMore={more(groups, 'groups')} title={`群聊 · ${groups.length} 个`}>
        {limit(groups).map(({ streamId, byMember }) => {
          const conversation = streamConversation(streamId);
          return (
            <li key={streamId}>
              <button className={styles.hit} onClick={() => openConversation(conversation.key)} type="button">
                <Avatar avatar={conversationAvatar(state, conversation)} size={36} />
                <span className={styles.main}>
                  <span className={styles.name}><Highlight text={conversationTitle(state, conversation)} words={words} /></span>
                  <span className={styles.meta}>{byMember ? '成员姓名匹配' : '群名称匹配'}</span>
                </span>
              </button>
            </li>
          );
        })}
      </Section>,
    );
  }
  if ((all || tab === 'contacts') && contacts.length > 0) {
    sections.push(
      <Section key="c" onMore={more(contacts, 'contacts')} title={`联系人 · ${contacts.length} 人`}>
        {limit(contacts).map((userId) => {
          const org = [userDept(state, userId), userTitle(state, userId)].filter(Boolean).join(' · ');
          const bot = state.users[userId]!.is_bot;
          return (
            <li key={userId}>
              <button className={styles.hit} onClick={() => chatWith([userId])} type="button">
                <Avatar avatar={personAvatar(state, userId)} presence={bot ? null : userPresence(state, userId)} size={36} />
                <span className={styles.main}>
                  <span className={styles.name}><Highlight text={userName(state, userId)} words={words} /></span>
                  {org ? <span className={styles.meta}><Highlight text={org} words={words} /></span> : null}
                </span>
              </button>
            </li>
          );
        })}
      </Section>,
    );
  }

  const settled = (tab !== 'messages' && tab !== 'files' && tab !== 'all')
    || (tab === 'messages' && messages.status === 'ready')
    || (tab === 'files' && files.status === 'ready')
    || (tab === 'all' && messages.status === 'ready' && files.status === 'ready');
  const empty = sections.every((section) => section === null);

  return (
    <>
      <div className={list.tabs} role="group" aria-label="搜索结果分类">
        {TABS.map((item) => (
          <button aria-pressed={tab === item.key} className={list.tab} key={item.key} onClick={() => setTab(item.key)} type="button">
            {item.label}
          </button>
        ))}
      </div>
      <div aria-label="搜索结果" className={styles.results}>
        {sections}
        {settled && empty ? <p className={styles.note}>没有找到「{query}」相关的内容。</p> : null}
        {(all || tab === 'messages') && messages.status === 'ready' && messages.more ? (
          <p className={styles.hint}>只显示最新的 {SEARCH_LIMIT} 条聊天记录，可以换更具体的词再搜。</p>
        ) : null}
      </div>
    </>
  );
}
