import { create } from 'zustand';
import { ApiError } from '../../../api/mutator';
import type { ImTransport } from './transport';
import type {
  ZulipMessage, ZulipPresence, ZulipRegisterResponse, ZulipSubscription, ZulipUser,
} from './types';
import {
  conversationNarrow, conversationTarget, dmConversation, messageConversation, streamConversation,
} from './narrow';
import type { Conversation, ConversationKey } from './narrow';

/**
 * IM 状态（lane 1：快照 + 按会话的消息窗口 + 发送）。事件归并、队列重建、未读回写、草稿与
 * generation 冻结在 lane 3 按 FE_PLAN §5.2 补入；这里只保证迟到结果按 `session` 丢弃。
 */

export interface MessageWindow {
  status: 'loading' | 'ready' | 'failed';
  messages: ZulipMessage[];
  foundOldest: boolean;
}

export interface ImState {
  phase: 'idle' | 'loading' | 'ready' | 'failed';
  failure: string | null;
  session: number;
  selfId: number;
  users: Record<number, ZulipUser>;
  deptFieldId: string | null;
  titleFieldId: string | null;
  subscriptions: Record<number, ZulipSubscription>;
  presences: Record<number, ZulipPresence>;
  presenceThreshold: number;
  serverSkew: number;
  enterSends: boolean;
  conversations: Partial<Record<ConversationKey, Conversation>>;
  latest: Partial<Record<ConversationKey, ZulipMessage>>;
  order: Partial<Record<ConversationKey, number>>;
  unread: Partial<Record<ConversationKey, number[]>>;
  mentions: Record<number, true>;
  windows: Partial<Record<ConversationKey, MessageWindow>>;
  selected: ConversationKey | null;
}

const EMPTY: Omit<ImState, 'session'> = {
  phase: 'idle', failure: null, selfId: 0, users: {}, deptFieldId: null, titleFieldId: null,
  subscriptions: {}, presences: {}, presenceThreshold: 140, serverSkew: 0, enterSends: true,
  conversations: {}, latest: {}, order: {}, unread: {}, mentions: {}, windows: {}, selected: null,
};

export const useImStore = create<ImState>()(() => ({ ...EMPTY, session: 0 }));

let transport: ImTransport | null = null;

function failureCode(error: unknown): string {
  return error instanceof ApiError ? error.code : 'im_unavailable';
}

function isCurrent(session: number): boolean {
  return useImStore.getState().session === session;
}

function profileFieldId(register: ZulipRegisterResponse, name: string): string | null {
  const field = register.custom_profile_fields.find((item) => item.name === name);
  return field ? String(field.id) : null;
}

function indexSnapshot(register: ZulipRegisterResponse): Partial<ImState> {
  const selfId = register.user_id;
  const conversations: ImState['conversations'] = {};
  const order: ImState['order'] = {};
  const unread: ImState['unread'] = {};
  const add = (conversation: Conversation, maxId = 0) => {
    conversations[conversation.key] = conversation;
    order[conversation.key] = Math.max(order[conversation.key] ?? 0, maxId);
  };
  for (const sub of register.subscriptions) add(streamConversation(sub.stream_id));
  for (const recent of register.recent_private_conversations) {
    add(dmConversation(recent.user_ids, selfId), recent.max_message_id);
  }
  const pushUnread = (conversation: Conversation, ids: number[]) => {
    add(conversation, Math.max(0, ...ids));
    unread[conversation.key] = [...(unread[conversation.key] ?? []), ...ids];
  };
  for (const item of register.unread_msgs.streams) pushUnread(streamConversation(item.stream_id), item.unread_message_ids);
  for (const item of register.unread_msgs.pms) pushUnread(dmConversation([item.other_user_id], selfId), item.unread_message_ids);
  for (const item of register.unread_msgs.huddles) {
    pushUnread(dmConversation(item.user_ids_string.split(',').map(Number), selfId), item.unread_message_ids);
  }
  return {
    selfId,
    users: Object.fromEntries(register.realm_users.map((user) => [user.user_id, user])),
    deptFieldId: profileFieldId(register, '部门'),
    titleFieldId: profileFieldId(register, '职务'),
    subscriptions: Object.fromEntries(register.subscriptions.map((sub) => [sub.stream_id, sub])),
    presences: Object.fromEntries(Object.entries(register.presences).map(([id, value]) => [Number(id), value])),
    presenceThreshold: register.presence_offline_threshold_seconds ?? 140,
    serverSkew: register.server_timestamp - Date.now() / 1000,
    enterSends: register.enter_sends ?? true,
    conversations, order, unread,
    mentions: Object.fromEntries(register.unread_msgs.mentions.map((id) => [id, true as const])),
  };
}

function absorbLatest(state: ImState, messages: ZulipMessage[]): Partial<ImState> {
  const conversations = { ...state.conversations };
  const latest = { ...state.latest };
  const order = { ...state.order };
  for (const message of messages) {
    const conversation = messageConversation(message, state.selfId);
    conversations[conversation.key] ??= conversation;
    const current = latest[conversation.key];
    if (!current || current.id < message.id) latest[conversation.key] = message;
    order[conversation.key] = Math.max(order[conversation.key] ?? 0, message.id);
  }
  return { conversations, latest, order };
}

/** 换身份或离开时调用：丢掉全部状态，迟到结果按 session 失效。 */
export function resetIm(): void {
  transport = null;
  useImStore.setState((state) => ({ ...EMPTY, session: state.session + 1 }));
}

export async function startIm(next: ImTransport): Promise<void> {
  resetIm();
  transport = next;
  const session = useImStore.getState().session;
  useImStore.setState({ phase: 'loading' });
  try {
    const register = await next.register();
    if (!isCurrent(session)) return;
    useImStore.setState(indexSnapshot(register));
    // 会话摘要只取已加载窗口内的内容（FE_PLAN §3 第 1 行）。
    const recent = await next.messages({ anchor: 'newest', numBefore: 300, numAfter: 0, narrow: [] });
    if (!isCurrent(session)) return;
    useImStore.setState((state) => ({ ...absorbLatest(state, recent.messages), phase: 'ready' }));
  } catch (error) {
    if (isCurrent(session)) useImStore.setState({ phase: 'failed', failure: failureCode(error) });
  }
}

async function loadWindow(key: ConversationKey, session: number): Promise<void> {
  const conversation = useImStore.getState().conversations[key];
  if (!transport || !conversation) return;
  useImStore.setState((state) => ({
    windows: { ...state.windows, [key]: { messages: state.windows[key]?.messages ?? [], foundOldest: false, status: 'loading' } },
  }));
  try {
    const result = await transport.messages({
      anchor: 'newest', numBefore: 60, numAfter: 0, narrow: conversationNarrow(conversation),
    });
    if (!isCurrent(session)) return;
    useImStore.setState((state) => ({
      ...absorbLatest(state, result.messages),
      windows: { ...state.windows, [key]: { status: 'ready', messages: result.messages, foundOldest: result.found_oldest } },
    }));
  } catch {
    if (!isCurrent(session)) return;
    useImStore.setState((state) => ({
      windows: { ...state.windows, [key]: { messages: state.windows[key]?.messages ?? [], foundOldest: false, status: 'failed' } },
    }));
  }
}

export function openConversation(key: ConversationKey): void {
  const state = useImStore.getState();
  useImStore.setState({ selected: key });
  const existing = state.windows[key];
  if (!existing || existing.status === 'failed') void loadWindow(key, state.session);
}

export function retryConversation(key: ConversationKey): void {
  void loadWindow(key, useImStore.getState().session);
}

/**
 * 发送一条消息。结果不明确时不自动重发（合同 §3.2）；失败原样抛给输入框显示。
 * lane 1 发送成功后重读当前窗口；lane 3 改为按 message 事件与 local_message_id 归并。
 */
export async function sendMessage(key: ConversationKey, content: string): Promise<void> {
  const state = useImStore.getState();
  const conversation = state.conversations[key];
  if (!transport || !conversation) throw new ApiError(409, 'im_not_ready', '消息还没有准备好。');
  await transport.send({ target: conversationTarget(conversation), content, queueId: null, localId: null });
  if (isCurrent(state.session)) await loadWindow(key, state.session);
}
