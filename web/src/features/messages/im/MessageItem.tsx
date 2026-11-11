import { App as AntApp } from 'antd';
import type { MouseEvent } from 'react';
import { toggleReaction, useImStore } from '../zulip/imStore';
import type { ImState } from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';
import type { ZulipMessage } from '../zulip/types';
import { MessageMenu } from './MessageMenu';
import { Avatar } from './Avatar';
import { MessageBody } from './MessageBody';
import { ProfileCard } from './ProfileCard';
import { useSelection } from './selectionStore';
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
  const selecting = useSelection((selection) => selection.key === conversationKey);
  const selected = useSelection((selection) => selection.ids.includes(message.id));

  // 多选时点消息任意处即勾选（链接、表态都不触发），按住 Shift 连选。
  const onPick = (event: MouseEvent) => {
    if (!selecting) return;
    event.preventDefault();
    event.stopPropagation();
    const ordered = (useImStore.getState().windows[conversationKey]?.messages ?? []).map((item) => item.id);
    useSelection.getState().toggle(message.id, event.shiftKey, ordered);
  };

  return (
    <article
      aria-label={`${mine ? '我' : userName(state, message.sender_id)} ${separatorTime(message.timestamp)}`}
      aria-selected={selecting ? selected : undefined}
      className={styles.message}
      data-message-id={message.id}
      data-selecting={selecting ? 'true' : 'false'}
      onClickCapture={onPick}
      data-highlighted={highlighted ? 'true' : 'false'}
      data-self={mine ? 'true' : 'false'}
    >
      {selecting ? (
        <span aria-hidden="true" className={styles.check} data-checked={selected ? 'true' : 'false'} />
      ) : null}
      {mine || selecting ? <Avatar avatar={personAvatar(state, message.sender_id)} size={36} /> : (
        <ProfileCard userId={message.sender_id}>
          <button aria-label={`${userName(state, message.sender_id)}的资料`} className={styles.avatarButton} type="button">
            <Avatar avatar={personAvatar(state, message.sender_id)} size={36} />
          </button>
        </ProfileCard>
      )}
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
