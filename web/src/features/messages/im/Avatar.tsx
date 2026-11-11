import { useState } from 'react';
import { Icon } from '../../../shared/ui/Icon';
import { imFileUrl } from '../zulip/links';
import type { ConversationAvatar, PresenceState } from './viewModel';
import styles from './Avatar.module.css';

type AvatarSize = 40 | 36 | 28 | 56 | 72;
const SIZE_CLASS: Record<AvatarSize, string> = { 40: '', 36: styles.s36!, 28: styles.s28!, 56: styles.s56!, 72: styles.s72! };

/**
 * 头像只是装饰：名字由旁边的文字承担，所以固定 aria-hidden。图片地址只认改写到代理入口的
 * Zulip 头像；系统 bot 的静态头像和取不到的图都显示中性底色加姓名字（合同 §4.2 末条）。
 */
export function Avatar({ avatar, presence, size = 40 }: {
  avatar: ConversationAvatar;
  presence?: PresenceState | null;
  size?: AvatarSize;
}) {
  const [broken, setBroken] = useState(false);
  let face;
  if (avatar.kind === 'group') {
    face = (
      <div className={styles.group}>
        {avatar.cells.map((cell, index) => <i className={styles[cell.tone]} key={index}>{cell.text}</i>)}
      </div>
    );
  } else if (avatar.bot) {
    face = <div className={`${styles.av} ${styles.bot} ${SIZE_CLASS[size]}`}><Icon name="spark" size={20} /></div>;
  } else {
    const url = avatar.url ? imFileUrl(avatar.url) : null;
    face = (
      <div className={`${styles.av} ${styles[avatar.tone]} ${SIZE_CLASS[size]}`}>
        {url && !broken ? <img alt="" onError={() => setBroken(true)} src={url} /> : avatar.text}
      </div>
    );
  }
  return (
    <div aria-hidden="true" className={styles.wrap}>
      {face}
      {presence ? <span className={styles.dot} data-presence={presence} /> : null}
    </div>
  );
}
