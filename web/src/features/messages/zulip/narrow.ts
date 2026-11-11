import type { ZulipMessage, ZulipNarrowTerm, ZulipSendTarget } from './types';

/**
 * 会话键：群为 `stream:<id>`，单聊和多人私聊为 `dm:<除本人外的 user id 升序>`；与自己的单聊为
 * `dm:<本人 id>`。所有按会话存的状态（消息窗口、草稿、滚动位置）都用这个键。
 */
export type ConversationKey = `stream:${number}` | `dm:${string}`;

export type Conversation =
  | { kind: 'stream'; key: ConversationKey; streamId: number }
  | { kind: 'dm'; key: ConversationKey; userIds: number[] };

export function streamConversation(streamId: number): Conversation {
  return { kind: 'stream', key: `stream:${streamId}`, streamId };
}

/** `userIds` 可含本人；只剩本人时视为与自己的单聊。 */
export function dmConversation(userIds: number[], selfId: number): Conversation {
  const others = [...new Set(userIds.filter((id) => id !== selfId))].sort((a, b) => a - b);
  const ids = others.length === 0 ? [selfId] : others;
  return { kind: 'dm', key: `dm:${ids.join(',')}`, userIds: ids };
}

export function messageConversation(message: ZulipMessage, selfId: number): Conversation {
  return message.type === 'stream'
    ? streamConversation(message.stream_id)
    : dmConversation(message.display_recipient.map((user) => user.id), selfId);
}

export function conversationNarrow(conversation: Conversation): ZulipNarrowTerm[] {
  return conversation.kind === 'stream'
    ? [{ operator: 'channel', operand: conversation.streamId }]
    : [{ operator: 'dm', operand: conversation.userIds }];
}

/** 新群只用空话题（`topics_policy=empty_topic_only`）；旧的多话题群在 lane 4 接话题选择。 */
export function conversationTarget(conversation: Conversation): ZulipSendTarget {
  return conversation.kind === 'stream'
    ? { type: 'stream', streamId: conversation.streamId, topic: '' }
    : { type: 'direct', userIds: conversation.userIds };
}
