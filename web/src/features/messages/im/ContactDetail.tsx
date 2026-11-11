import { useEffect, useState } from 'react';
import { Icon } from '../../../shared/ui/Icon';
import { readUserChannels, useImStore } from '../zulip/imStore';
import { streamConversation } from '../zulip/narrow';
import { Avatar } from './Avatar';
import { PRESENCE_LABEL, personAvatar, profileRows, userDept, userPresence, userTitle } from './viewModel';
import { chatWith, showConversation, useImView } from './viewStore';
import styles from './ContactDetail.module.css';

type Channels = { status: 'loading' } | { status: 'failed' } | { status: 'ready'; value: { streamId: number; name: string }[] };

/** 所在群：只列本人有权看到的群；本人已加入的群可以直接点开。 */
function UserChannels({ userId }: { userId: number }) {
  const subscriptions = useImStore((state) => state.subscriptions);
  const [channels, setChannels] = useState<Channels>({ status: 'loading' });
  useEffect(() => {
    let live = true;
    setChannels({ status: 'loading' });
    readUserChannels(userId).then(
      (value) => live && setChannels({ status: 'ready', value }),
      () => live && setChannels({ status: 'failed' }),
    );
    return () => {
      live = false;
    };
  }, [userId]);

  if (channels.status === 'loading') return <span className={styles.muted}>读取中…</span>;
  if (channels.status === 'failed') return <span className={styles.muted} role="alert">读取失败</span>;
  if (channels.value.length === 0) return <span className={styles.muted}>没有共同可见的群</span>;
  return (
    <span className={styles.channels}>
      {channels.value.map((channel) => (subscriptions[channel.streamId] ? (
        <button key={channel.streamId} onClick={() => showConversation(streamConversation(channel.streamId).key)} type="button">
          {channel.name}
        </button>
      ) : <span key={channel.streamId}>{channel.name}</span>))}
    </span>
  );
}

/** 联系人页右栏（v3-6）：头像、在线状态、部门职务、资料行、所在群、发消息。没有值的资料行不显示。 */
export function ContactDetail() {
  const state = useImStore();
  const userId = useImView((view) => view.contact);
  const user = userId === null ? undefined : state.users[userId];
  if (userId === null || !user) {
    return <div className={styles.empty}>{state.phase === 'ready' ? '选择左侧的联系人查看资料' : ''}</div>;
  }
  const presence = user.is_bot ? null : userPresence(state, userId);
  const org = user.is_bot ? 'AI 助手' : [userDept(state, userId), userTitle(state, userId)].filter(Boolean).join(' · ');
  return (
    <div className={styles.page}>
      <section aria-label={`${user.full_name}的资料`} className={styles.card}>
        <Avatar avatar={personAvatar(state, userId)} presence={presence} size={72} />
        <h2 className={styles.name}>
          {user.full_name}
          {presence ? <span className={styles.presence} data-presence={presence}>{PRESENCE_LABEL[presence]}</span> : null}
        </h2>
        {org ? <p className={styles.org}>{org}</p> : null}
        <dl className={styles.rows}>
          {user.is_bot ? null : profileRows(state, userId).map(([label, value]) => (
            <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
          ))}
          {userId !== state.selfId ? <div><dt>所在群</dt><dd><UserChannels userId={userId} /></dd></div> : null}
        </dl>
        {userId !== state.selfId ? (
          <div className={styles.actions}>
            <button className={styles.primary} onClick={() => chatWith([userId])} type="button">
              <Icon name="chat" size={15} />发消息
            </button>
          </div>
        ) : null}
      </section>
    </div>
  );
}
