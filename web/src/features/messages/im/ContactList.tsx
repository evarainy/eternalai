import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Icon } from '../../../shared/ui/Icon';
import { readUserGroups, useImStore } from '../zulip/imStore';
import { streamConversation } from '../zulip/narrow';
import type { ImState } from '../zulip/imStore';
import type { ZulipUserGroup } from '../zulip/types';
import { Avatar } from './Avatar';
import { NewChatButton } from './NewChatButton';
import {
  conversationAvatar, conversationMemberCount, personAvatar, recentContacts, userDept, userPresence, userTitle,
} from './viewModel';
import { showConversation, useImView } from './viewStore';
import styles from './ConversationList.module.css';
import own from './ContactList.module.css';

type Tab = 'people' | 'groups' | 'userGroups' | 'bots';
const TABS: { key: Tab; label: string }[] = [
  { key: 'people', label: '全部人员' }, { key: 'groups', label: '我的群组' },
  { key: 'userGroups', label: '用户组' }, { key: 'bots', label: '机器人' },
];
const RECENT = 5;

type Load<T> = { status: 'loading' } | { status: 'failed' } | { status: 'ready'; value: T };

const collator = new Intl.Collator('zh-CN');

function personMatches(state: ImState, userId: number, query: string): boolean {
  if (!query) return true;
  const user = state.users[userId];
  return [user?.full_name, userDept(state, userId), userTitle(state, userId)]
    .some((text) => text?.toLowerCase().includes(query));
}

function PersonRow({ userId }: { userId: number }) {
  const state = useImStore();
  const contact = useImView((view) => view.contact);
  const user = state.users[userId];
  const bot = user?.is_bot ?? false;
  const sub = bot ? 'AI 助手' : [userDept(state, userId), userTitle(state, userId)].filter(Boolean).join(' · ');
  return (
    <li>
      <button
        aria-current={contact === userId ? 'true' : undefined}
        className={`${styles.row} ${own.row}`}
        onClick={() => useImView.getState().showContact(userId)}
        type="button"
      >
        <Avatar avatar={personAvatar(state, userId)} presence={bot ? null : userPresence(state, userId)} />
        <span className={styles.main}>
          <span className={styles.name}>{user?.full_name ?? `用户 ${userId}`}</span>
          {sub ? <span className={own.sub}>{sub}</span> : null}
        </span>
      </button>
    </li>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <li>
        <button aria-expanded={open} className={own.section} onClick={() => setOpen(!open)} type="button">
          <Icon name="chevron" size={12} />{title}
        </button>
      </li>
      {open ? children : null}
    </>
  );
}

/** 用户组：首次切到这个页签时读一次；不显示系统组。点组名展开成员。 */
function UserGroups({ query }: { query: string }) {
  const state = useImStore();
  const [groups, setGroups] = useState<Load<ZulipUserGroup[]>>({ status: 'loading' });
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    readUserGroups().then(
      (value) => live && setGroups({ status: 'ready', value: value.filter((group) => !group.is_system_group) }),
      () => live && setGroups({ status: 'failed' }),
    );
    return () => {
      live = false;
    };
  }, []);

  if (groups.status === 'loading') return <p className={styles.state} role="status">正在读取用户组…</p>;
  if (groups.status === 'failed') return <p className={styles.state} role="alert">用户组读取失败，请稍后重试。</p>;
  const visible = groups.value.filter((group) => !query || group.name.toLowerCase().includes(query));
  if (visible.length === 0) return <p className={styles.state}>{groups.value.length === 0 ? '还没有用户组。' : '没有符合条件的用户组。'}</p>;
  return (
    <ul className={styles.rows}>
      {visible.map((group) => {
        const members = group.members.filter((id) => state.users[id]?.is_active);
        return (
          <li key={group.id}>
            <button aria-expanded={open === group.id} className={`${styles.row} ${own.row}`} onClick={() => setOpen(open === group.id ? null : group.id)} type="button">
              <span className={own.groupIcon}><Icon name="users" size={18} /></span>
              <span className={styles.main}>
                <span className={styles.name}>{group.name}</span>
                <span className={own.sub}>{members.length} 人{group.description ? ` · ${group.description}` : ''}</span>
              </span>
            </button>
            {open === group.id ? (
              <ul className={own.nested}>{members.map((id) => <PersonRow key={id} userId={id} />)}</ul>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * 联系人页左栏（v3-6，FE_PLAN §3 联系人页一行）：全部人员（最近联系在前）、我的群组、用户组、机器人。
 * 「添加联系人」就是发起单聊；「最近联系」代替「常用联系人」，不提供「设为常用」（§9 第 2 件）。
 */
export function ContactList() {
  const state = useImStore();
  const [tab, setTab] = useState<Tab>('people');
  const [query, setQuery] = useState('');
  const words = query.trim().toLowerCase();

  const active = Object.values(state.users).filter((user) => user.is_active);
  const byName = (a: number, b: number) => collator.compare(state.users[a]!.full_name, state.users[b]!.full_name);

  let body;
  if (state.phase !== 'ready') {
    body = <p className={styles.state} role="status">{state.phase === 'failed' || state.phase === 'disabled' ? '联系人暂时读不到。' : '正在读取联系人…'}</p>;
  } else if (tab === 'userGroups') {
    body = <UserGroups query={words} />;
  } else if (tab === 'groups') {
    const groups = Object.values(state.subscriptions)
      .filter((sub) => !words || sub.name.toLowerCase().includes(words))
      .sort((a, b) => collator.compare(a.name, b.name));
    body = groups.length === 0 ? <p className={styles.state}>{words ? '没有符合条件的群。' : '还没有加入群。'}</p> : (
      <ul className={styles.rows}>
        {groups.map((sub) => {
          const conversation = streamConversation(sub.stream_id);
          const count = conversationMemberCount(state, conversation);
          return (
            <li key={sub.stream_id}>
              <button className={`${styles.row} ${own.row}`} onClick={() => showConversation(conversation.key)} type="button">
                <Avatar avatar={conversationAvatar(state, conversation)} />
                <span className={styles.main}>
                  <span className={styles.name}>{sub.name}</span>
                  {count !== null ? <span className={own.sub}>{count} 人</span> : null}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    );
  } else {
    const bots = tab === 'bots';
    const ids = active.filter((user) => user.is_bot === bots).map((user) => user.user_id)
      .filter((id) => personMatches(state, id, words)).sort(byName);
    const recent = bots || words ? [] : recentContacts(state, RECENT);
    body = ids.length === 0 ? <p className={styles.state}>{words ? '没有符合条件的人。' : bots ? '还没有机器人。' : '还没有人员。'}</p> : (
      <ul className={styles.rows}>
        {recent.length > 0 ? (
          <Section title={`最近联系 · ${recent.length}`}>
            {recent.map((id) => <PersonRow key={`recent-${id}`} userId={id} />)}
          </Section>
        ) : null}
        {bots ? ids.map((id) => <PersonRow key={id} userId={id} />) : (
          <Section title={`全部人员 · ${ids.length}`}>
            {ids.map((id) => <PersonRow key={id} userId={id} />)}
          </Section>
        )}
      </ul>
    );
  }

  return (
    <>
      <div className={styles.head}>
        <label className={styles.search} data-focus-ring="host">
          <Icon name="search" size={15} />
          <input
            aria-label="搜索联系人"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索姓名、部门"
            type="search"
            value={query}
          />
        </label>
        <NewChatButton className={styles.iconButton ?? ''} directOnly />
      </div>
      <div className={styles.tabs} role="group" aria-label="联系人分类">
        {TABS.map((item) => (
          <button aria-pressed={tab === item.key} className={styles.tab} key={item.key} onClick={() => setTab(item.key)} type="button">
            {item.label}
          </button>
        ))}
      </div>
      {body}
    </>
  );
}
