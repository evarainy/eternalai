import type { ReactNode } from 'react';
import { Popover } from 'antd';
import { Icon } from '../../../shared/ui/Icon';
import { startDirect, useImStore } from '../zulip/imStore';
import { Avatar } from './Avatar';
import { PRESENCE_LABEL, personAvatar, profileRows, userDept, userName, userPresence, userTitle } from './viewModel';
import { useImView } from './viewStore';
import styles from './ProfileCard.module.css';

function Card({ userId }: { userId: number }) {
  const state = useImStore();
  const presence = userPresence(state, userId);
  const org = [userDept(state, userId), userTitle(state, userId)].filter(Boolean).join(' · ');
  const rows = profileRows(state, userId);
  return (
    <div aria-label={`${userName(state, userId)}的资料`} className={styles.card}>
      <div className={styles.head}>
        <Avatar avatar={personAvatar(state, userId)} presence={presence} size={56} />
        <div>
          <b>{userName(state, userId)}</b>
          <span className={styles.presence} data-presence={presence}>{PRESENCE_LABEL[presence]}</span>
          {org ? <p>{org}</p> : null}
        </div>
      </div>
      {rows.length > 0 ? (
        <dl className={styles.rows}>
          {rows.map(([label, value]) => (
            <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
          ))}
        </dl>
      ) : null}
      <div className={styles.actions}>
        {userId !== state.selfId ? (
          <button className={styles.primary} onClick={() => startDirect([userId])} type="button">
            <Icon name="chat" size={15} />发消息
          </button>
        ) : null}
        <button className={styles.secondary} onClick={() => useImView.getState().showContact(userId)} type="button">
          查看资料
        </button>
      </div>
    </div>
  );
}

/** 点头像或姓名弹出资料卡（v3-4）。机器人与已不在目录里的人不弹。 */
export function ProfileCard({ children, userId }: { children: ReactNode; userId: number }) {
  const user = useImStore((state) => state.users[userId]);
  if (!user || user.is_bot) return <>{children}</>;
  return (
    <Popover arrow={false} content={<Card userId={userId} />} placement="bottomLeft" trigger="click">
      {children}
    </Popover>
  );
}
