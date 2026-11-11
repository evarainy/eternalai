import { create } from 'zustand';
import { ApiError } from '../../../api/mutator';
import type { ImTransport } from './transport';
import type {
  ZulipDraft, ZulipEmojiRef, ZulipMessage, ZulipPresence, ZulipRealmEmoji, ZulipRegisterResponse, ZulipSubscription,
  ZulipUser,
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
  /** 按锚点读出的窗口可能不含最新消息；此时显示「回到最新」。 */
  foundNewest: boolean;
}

const EMPTY_WINDOW: MessageWindow = { status: 'loading', messages: [], foundOldest: false, foundNewest: true };

export type StoredDraft = ZulipDraft & { id: number };

export interface ImState {
  phase: 'idle' | 'loading' | 'ready' | 'failed';
  failure: string | null;
  session: number;
  /** register 返回的事件队列；发送时与 local_id 成对提交（合同 §3.2）。 */
  queueId: string | null;
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
  /** 打开会话时要定位的消息（搜索命中、返回原消息、消息链接）。 */
  anchor: { key: ConversationKey; messageId: number } | null;
  drafts: Partial<Record<number, StoredDraft>>;
  realmEmoji: ZulipRealmEmoji[];
  maxUploadMib: number;
  /** 本人点过「标为未读」的消息，自动标读不能立刻清掉（lane 3 消费）。 */
  manualUnread: Record<number, true>;
}

const EMPTY: Omit<ImState, 'session'> = {
  phase: 'idle', failure: null, queueId: null, selfId: 0, users: {}, deptFieldId: null, titleFieldId: null,
  subscriptions: {}, presences: {}, presenceThreshold: 140, serverSkew: 0, enterSends: true,
  conversations: {}, latest: {}, order: {}, unread: {}, mentions: {}, windows: {}, selected: null,
  anchor: null, drafts: {}, realmEmoji: [], maxUploadMib: 25, manualUnread: {},
};

export const useImStore = create<ImState>()(() => ({ ...EMPTY, session: 0 }));

let transport: ImTransport | null = null;
let localCounter = 0;

/** 模块级的可变缓存（如输入框未发出的文字）登记到这里，随 resetIm 一起清空，不跨身份残留。 */
const resetHooks = new Set<() => void>();

export function onImReset(hook: () => void): void {
  resetHooks.add(hook);
}

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
    queueId: register.queue_id,
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
    drafts: Object.fromEntries((register.drafts ?? [])
      .filter((draft): draft is StoredDraft => draft.id !== undefined)
      .map((draft) => [draft.id, draft])),
    realmEmoji: Object.values(register.realm_emoji ?? {}).filter((emoji) => !emoji.deactivated),
    maxUploadMib: register.max_file_upload_size_mib,
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
  for (const hook of resetHooks) hook();
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

/**
 * 读一个会话的消息窗口。`anchor` 为消息 id 时取它前后各 30 条（搜索命中、返回原消息、消息链接），
 * 否则取最新 60 条。
 */
async function loadWindow(key: ConversationKey, session: number, anchor?: number): Promise<void> {
  const conversation = useImStore.getState().conversations[key];
  if (!transport || !conversation) return;
  useImStore.setState((state) => ({
    windows: { ...state.windows, [key]: { ...(state.windows[key] ?? EMPTY_WINDOW), status: 'loading' } },
  }));
  try {
    const result = await transport.messages({
      anchor: anchor ?? 'newest', numBefore: anchor ? 30 : 60, numAfter: anchor ? 30 : 0,
      narrow: conversationNarrow(conversation),
    });
    if (!isCurrent(session)) return;
    useImStore.setState((state) => ({
      ...absorbLatest(state, result.messages),
      windows: {
        ...state.windows,
        [key]: {
          status: 'ready', messages: result.messages, foundOldest: result.found_oldest,
          foundNewest: result.found_newest,
        },
      },
    }));
  } catch {
    if (!isCurrent(session)) return;
    useImStore.setState((state) => ({
      windows: { ...state.windows, [key]: { ...(state.windows[key] ?? EMPTY_WINDOW), status: 'failed' } },
    }));
  }
}

/**
 * 打开会话。滚动定位的优先级（FE_PLAN §2.1）：明确锚点 > 记住的位置 > 最新；锚点不在已加载窗口里时
 * 按锚点重读窗口。
 */
export function openConversation(key: ConversationKey, anchorMessageId?: number): void {
  const state = useImStore.getState();
  useImStore.setState({ selected: key, anchor: anchorMessageId ? { key, messageId: anchorMessageId } : null });
  const existing = state.windows[key];
  const anchorLoaded = anchorMessageId === undefined || existing?.messages.some((item) => item.id === anchorMessageId);
  if (!existing || existing.status === 'failed' || !anchorLoaded) void loadWindow(key, state.session, anchorMessageId);
}

export function clearAnchor(): void {
  useImStore.setState({ anchor: null });
}

/** 「回到最新」：窗口不在最新处时重读最新一段。 */
export function returnToLatest(key: ConversationKey): void {
  const state = useImStore.getState();
  useImStore.setState({ anchor: null });
  if (!state.windows[key]?.foundNewest) void loadWindow(key, state.session);
}

export function retryConversation(key: ConversationKey): void {
  void loadWindow(key, useImStore.getState().session);
}

function requireTransport(): ImTransport {
  if (!transport) throw new ApiError(409, 'im_not_ready', '消息还没有准备好。');
  return transport;
}

/** lane 2 的写操作成功后重读当前窗口；lane 3 改为按事件归并。 */
async function afterWrite(key: ConversationKey, session: number): Promise<void> {
  if (isCurrent(session)) await loadWindow(key, session, useImStore.getState().anchor?.messageId);
}

/**
 * 发送一条消息。结果不明确时不自动重发（合同 §3.2）；失败原样抛给输入框显示。
 */
export async function sendMessage(key: ConversationKey, content: string): Promise<void> {
  const state = useImStore.getState();
  const conversation = state.conversations[key];
  if (!conversation || !state.queueId) throw new ApiError(409, 'im_not_ready', '消息还没有准备好。');
  const localId = `${Date.now()}.${++localCounter}`;
  await requireTransport().send({ target: conversationTarget(conversation), content, queueId: state.queueId, localId });
  useImStore.setState({ anchor: null });
  if (isCurrent(state.session)) await loadWindow(key, state.session);
}

export function readRawContent(messageId: number): Promise<string> {
  return requireTransport().rawContent(messageId);
}

export async function editMessage(key: ConversationKey, messageId: number, content: string): Promise<void> {
  const { session } = useImStore.getState();
  await requireTransport().editMessage(messageId, content);
  await afterWrite(key, session);
}

export async function deleteMessage(key: ConversationKey, messageId: number): Promise<void> {
  const { session } = useImStore.getState();
  await requireTransport().deleteMessage(messageId);
  await afterWrite(key, session);
}

export async function toggleReaction(key: ConversationKey, message: ZulipMessage, emoji: ZulipEmojiRef): Promise<void> {
  const { session, selfId } = useImStore.getState();
  const mine = message.reactions.some((item) => item.user_id === selfId && item.emoji_code === emoji.code);
  await requireTransport().react(message.id, emoji, !mine);
  await afterWrite(key, session);
}

export async function toggleStar(key: ConversationKey, message: ZulipMessage): Promise<void> {
  const { session } = useImStore.getState();
  await requireTransport().setFlag([message.id], 'starred', !message.flags.includes('starred'));
  await afterWrite(key, session);
}

/** 标为未读：同时登记到本会话未读，避免自动标读立刻清掉（FE_PLAN §5.2）。 */
export async function markUnread(key: ConversationKey, message: ZulipMessage): Promise<void> {
  const { session } = useImStore.getState();
  await requireTransport().setFlag([message.id], 'read', false);
  if (!isCurrent(session)) return;
  useImStore.setState((state) => ({
    unread: { ...state.unread, [key]: [...new Set([...(state.unread[key] ?? []), message.id])] },
    manualUnread: { ...state.manualUnread, [message.id]: true },
  }));
  await afterWrite(key, session);
}

/** 会话内搜索（Ctrl+F）：本会话 narrow 加 search 词，取最新 50 条命中。 */
export async function searchConversation(key: ConversationKey, query: string): Promise<ZulipMessage[]> {
  const conversation = useImStore.getState().conversations[key];
  if (!conversation) return [];
  const result = await requireTransport().messages({
    anchor: 'newest', numBefore: 50, numAfter: 0,
    narrow: [...conversationNarrow(conversation), { operator: 'search', operand: query }],
  });
  return result.messages.reverse();
}

/** 发起单聊：会话已存在就直接打开，否则先在列表里建一行。 */
export function startDirect(userIds: number[]): void {
  const state = useImStore.getState();
  const conversation = dmConversation(userIds, state.selfId);
  if (!state.conversations[conversation.key]) {
    useImStore.setState({ conversations: { ...state.conversations, [conversation.key]: conversation } });
  }
  openConversation(conversation.key);
}

/** 新建群：由本人确认后才提交（FE_PLAN §3「＋」一行）。成功后先在本地登记，lane 3 由事件校正。 */
export async function createGroup(name: string, userIds: number[]): Promise<void> {
  const { session, selfId } = useImStore.getState();
  const { id } = await requireTransport().createGroup(name, userIds);
  if (!isCurrent(session)) return;
  const conversation = streamConversation(id);
  useImStore.setState((state) => ({
    subscriptions: {
      ...state.subscriptions,
      [id]: {
        stream_id: id, name, description: '', invite_only: true, is_muted: false, pin_to_top: false,
        color: '#76ce90', subscribers: [...new Set([selfId, ...userIds])],
      },
    },
    conversations: { ...state.conversations, [conversation.key]: conversation },
    order: { ...state.order, [conversation.key]: Number.MAX_SAFE_INTEGER },
  }));
  openConversation(conversation.key);
}

export function uploadAttachment(file: File, onProgress: (ratio: number) => void, signal: AbortSignal) {
  return requireTransport().upload(file, onProgress, signal);
}

/* ---------- 草稿（/drafts） ---------- */

export function draftTarget(state: ImState, key: ConversationKey): Omit<ZulipDraft, 'content' | 'timestamp'> | null {
  const conversation = state.conversations[key];
  if (!conversation) return null;
  return conversation.kind === 'stream'
    ? { type: 'stream', to: [conversation.streamId], topic: '' }
    : { type: 'private', to: conversation.userIds, topic: '' };
}

export function draftKey(state: ImState, draft: ZulipDraft): ConversationKey | null {
  if (draft.type === 'stream' && draft.to[0] !== undefined) return streamConversation(draft.to[0]).key;
  if (draft.type === 'private') return dmConversation(draft.to.length > 0 ? draft.to : [state.selfId], state.selfId).key;
  return null;
}

/** 本会话的草稿，最新的在前。 */
export function conversationDrafts(state: ImState, key: ConversationKey): StoredDraft[] {
  return Object.values(state.drafts)
    .filter((draft): draft is StoredDraft => draft !== undefined && draftKey(state, draft) === key)
    .sort((a, b) => b.timestamp - a.timestamp);
}

/**
 * 保存草稿：有 id 就改，没有就新建；内容为空时删除。返回服务端确认后的草稿 id（删除时为 null）。
 */
export async function saveDraft(key: ConversationKey, draftId: number | null, content: string): Promise<number | null> {
  const state = useImStore.getState();
  const target = draftTarget(state, key);
  const api = requireTransport();
  if (!target) return draftId;
  if (!content.trim()) {
    if (draftId !== null) await api.deleteDraft(draftId);
    if (isCurrent(state.session) && draftId !== null) {
      useImStore.setState((current) => {
        const drafts = { ...current.drafts };
        delete drafts[draftId];
        return { drafts };
      });
    }
    return null;
  }
  const draft: ZulipDraft = { ...target, content, timestamp: Math.floor(Date.now() / 1000) };
  let id = draftId;
  if (id === null) id = await api.createDraft(draft);
  else await api.editDraft(id, draft);
  const savedId = id;
  if (isCurrent(state.session)) {
    useImStore.setState((current) => ({ drafts: { ...current.drafts, [savedId]: { ...draft, id: savedId } } }));
  }
  return savedId;
}
