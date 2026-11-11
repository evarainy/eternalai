/**
 * Zulip 12.3 官方 API 中本前端实际用到的字段。只声明用到的部分，其余字段照原样留在对象上不读。
 * 字段名与形状以 12.3 为准（合同 `IM_CLIENT_CONTRACT.md` §3），不使用 13.0 才有的字段。
 */

/** 合同 §2：当前绑定。四个字段都是非空字符串。 */
export interface ImIdentity {
  provider: string;
  im_instance_id: string;
  realm_id: string;
  im_user_id: string;
}

export interface ZulipProfileValue {
  value: string;
  rendered_value?: string;
}

export interface ZulipUser {
  user_id: number;
  full_name: string;
  email: string;
  avatar_url: string | null;
  is_active: boolean;
  is_bot: boolean;
  profile_data?: Record<string, ZulipProfileValue>;
}

export interface ZulipProfileField {
  id: number;
  name: string;
  type: number;
  order: number;
}

export interface ZulipSubscription {
  stream_id: number;
  name: string;
  description: string;
  invite_only: boolean;
  is_muted: boolean;
  pin_to_top: boolean;
  color: string;
  subscribers?: number[];
}

export interface ZulipRecentPrivateConversation {
  max_message_id: number;
  /** 不含本人；与自己的单聊为空数组。 */
  user_ids: number[];
}

export interface ZulipUnreadMessages {
  pms: { other_user_id: number; unread_message_ids: number[] }[];
  streams: { stream_id: number; topic: string; unread_message_ids: number[] }[];
  /** `user_ids_string` 含本人，逗号分隔、升序。 */
  huddles: { user_ids_string: string; unread_message_ids: number[] }[];
  mentions: number[];
}

export interface ZulipPresence {
  active_timestamp?: number;
  idle_timestamp?: number;
}

/** 话题偏好（12.3 `visibility_policy`）：0 无、1 静音、2 取消静音、3 关注。新群只用空话题。 */
export interface ZulipUserTopic {
  stream_id: number;
  topic_name: string;
  visibility_policy: number;
}

export interface ZulipRegisterResponse {
  queue_id: string;
  last_event_id: number;
  user_id: number;
  event_queue_longpoll_timeout_seconds: number;
  max_file_upload_size_mib: number;
  /** 单条消息正文上限（Unicode 字符数），超出时服务端截断。 */
  max_message_length: number;
  realm_users: ZulipUser[];
  custom_profile_fields: ZulipProfileField[];
  subscriptions: ZulipSubscription[];
  recent_private_conversations: ZulipRecentPrivateConversation[];
  unread_msgs: ZulipUnreadMessages;
  presences: Record<string, ZulipPresence>;
  server_timestamp: number;
  server_presence_offline_threshold_seconds?: number;
  server_presence_ping_interval_seconds?: number;
  enter_sends?: boolean;
  realm_emoji?: Record<string, ZulipRealmEmoji>;
  drafts?: ZulipDraft[];
  user_topics?: ZulipUserTopic[];
}

export interface ZulipRealmEmoji {
  id: string;
  name: string;
  source_url: string;
  deactivated: boolean;
}

/** 12.3 草稿：`to` 对群是 [stream_id]，对私聊是收件人 id（不含本人）；一个会话可以有多份。 */
export interface ZulipDraft {
  id?: number;
  type: 'stream' | 'private' | '';
  to: number[];
  topic: string;
  content: string;
  timestamp: number;
}

export interface ZulipEmojiRef {
  name: string;
  code: string;
  type: ZulipReaction['reaction_type'];
}

export interface ZulipUploadResult {
  url: string;
  filename: string;
}

export interface ZulipReaction {
  emoji_name: string;
  emoji_code: string;
  reaction_type: 'unicode_emoji' | 'realm_emoji' | 'zulip_extra_emoji';
  user_id: number;
}

export interface ZulipDisplayRecipientUser {
  id: number;
  full_name: string;
  email: string;
}

interface ZulipMessageBase {
  id: number;
  sender_id: number;
  sender_full_name: string;
  /** apply_markdown=true 时为服务端渲染的 HTML，只能经 MessageBody 的净化入口展示。 */
  content: string;
  timestamp: number;
  subject: string;
  flags: string[];
  reactions: ZulipReaction[];
  avatar_url?: string | null;
  local_message_id?: string;
  last_edit_timestamp?: number;
}

export interface ZulipStreamMessage extends ZulipMessageBase {
  type: 'stream';
  stream_id: number;
  display_recipient: string;
}

export interface ZulipDirectMessage extends ZulipMessageBase {
  type: 'private';
  display_recipient: ZulipDisplayRecipientUser[];
}

export type ZulipMessage = ZulipStreamMessage | ZulipDirectMessage;

export type ZulipNarrowTerm =
  | { operator: 'channel'; operand: number }
  | { operator: 'dm'; operand: number[] }
  | { operator: 'search'; operand: string }
  | { operator: 'is'; operand: string }
  | { operator: 'has'; operand: string };

export interface ZulipMessagesQuery {
  anchor: number | 'newest' | 'oldest' | 'first_unread';
  numBefore: number;
  numAfter: number;
  narrow: ZulipNarrowTerm[];
  /** 编辑时取原文才设 false；默认 true（合同 §3.2）。 */
  applyMarkdown?: boolean;
}

export interface ZulipMessagesResponse {
  messages: ZulipMessage[];
  found_newest: boolean;
  found_oldest: boolean;
  found_anchor: boolean;
}

export type ZulipSendTarget =
  | { type: 'stream'; streamId: number; topic: string }
  | { type: 'direct'; userIds: number[] };

export interface ZulipSendRequest {
  target: ZulipSendTarget;
  content: string;
  /** 成对、必填的 queue_id 与 local_id；message 事件里回显为 local_message_id（合同 §3.2）。 */
  queueId: string;
  localId: string;
}

/** 事件按 12.3 的形状松散声明；`events.ts` 按 `type` 分派并只读用到的字段。 */
export interface ZulipEvent {
  id: number;
  type: string;
  [key: string]: unknown;
}

export interface ZulipEventsResponse {
  events: ZulipEvent[];
}
