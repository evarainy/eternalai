import type { ImState } from '../zulip/imStore';
import type { ZulipMessage } from '../zulip/types';
import { Avatar } from './Avatar';
import { MessageBody } from './MessageBody';
import { clockTime, groupReactions, personAvatar, separatorTime, userDept, userName } from './viewModel';
import styles from './MessageItem.module.css';

/** 单条消息：「自己」按真实 sender_id 判断（FE_PLAN §3）；发送人旁的部门没有值时不显示。 */
export function MessageItem({ message, state }: { message: ZulipMessage; state: ImState }) {
  const mine = message.sender_id === state.selfId;
  const dept = mine ? null : userDept(state, message.sender_id);
  const reactions = groupReactions(message, state.selfId);
  return (
    <article
      aria-label={`${mine ? '我' : userName(state, message.sender_id)} ${separatorTime(message.timestamp)}`}
      className={styles.message}
      data-message-id={message.id}
      data-self={mine ? 'true' : 'false'}
    >
      <Avatar avatar={personAvatar(state, message.sender_id)} size={36} />
      <div className={styles.body} title={mine ? separatorTime(message.timestamp) : undefined}>
        {mine ? null : (
          <div className={styles.name}>
            <b>{userName(state, message.sender_id)}</b>
            {dept ? <span className={styles.dept}>{dept}</span> : null}
            {clockTime(message.timestamp)}
          </div>
        )}
        <MessageBody html={message.content} mine={mine} />
        {reactions.length > 0 ? (
          <div className={styles.reactions}>
            {reactions.map((reaction) => (
              <span className={styles.reaction} data-mine={reaction.mine ? 'true' : 'false'} key={reaction.key}>
                {reaction.label} {reaction.count}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </article>
  );
}
