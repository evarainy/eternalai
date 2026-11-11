import { App as AntApp } from 'antd';
import { toggleReaction } from '../zulip/imStore';
import type { ImState } from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';
import type { ZulipMessage } from '../zulip/types';
import { MessageMenu } from './MessageMenu';
import { Avatar } from './Avatar';
import { MessageBody } from './MessageBody';
import { clockTime, groupReactions, personAvatar, separatorTime, userDept, userName } from './viewModel';
import styles from './MessageItem.module.css';

/** 单条消息：「自己」按真实 sender_id 判断（FE_PLAN §3）；发送人旁的部门没有值时不显示。 */
export function MessageItem({ conversationKey, highlighted, message, state }: {
  conversationKey: ConversationKey;
  highlighted: boolean;
  message: ZulipMessage;
  state: ImState;
}) {
  const { message: toast } = AntApp.useApp();
  const mine = message.sender_id === state.selfId;
  const dept = mine ? null : userDept(state, message.sender_id);
  const reactions = groupReactions(message, state.selfId);
  return (
    <article
      aria-label={`${mine ? '我' : userName(state, message.sender_id)} ${separatorTime(message.timestamp)}`}
      className={styles.message}
      data-message-id={message.id}
      data-highlighted={highlighted ? 'true' : 'false'}
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
        <MessageMenu conversationKey={conversationKey} message={message}>
          <div className={styles.target}>
            <MessageBody html={message.content} mine={mine} />
          </div>
        </MessageMenu>
        {message.last_edit_timestamp || message.flags.includes('starred') ? (
          <div className={styles.marks}>
            {message.last_edit_timestamp ? <span>已编辑</span> : null}
            {message.flags.includes('starred') ? <span>已收藏</span> : null}
          </div>
        ) : null}
        {reactions.length > 0 ? (
          <div className={styles.reactions}>
            {reactions.map((reaction) => (
              <button
                aria-label={`${reaction.label} ${reaction.count} 人，${reaction.mine ? '点击取消' : '点击跟随'}`}
                aria-pressed={reaction.mine}
                className={styles.reaction}
                data-mine={reaction.mine ? 'true' : 'false'}
                key={reaction.key}
                onClick={() => {
                  toggleReaction(message, reaction.emoji).catch(() => void toast.error('表态没有成功，请重试'));
                }}
                type="button"
              >
                {reaction.label} {reaction.count}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </article>
  );
}
