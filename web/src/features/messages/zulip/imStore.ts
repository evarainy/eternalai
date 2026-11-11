import { create } from 'zustand';
import { ApiError } from '../../../api/mutator';
import { applyEvents, profileFieldIds } from './events';
import type { ImTransport } from './transport';
import type {
  ImIdentity, ZulipDraft, ZulipEmojiRef, ZulipMessage, ZulipPresence, ZulipRealmEmoji, ZulipRegisterResponse,
  ZulipSubscription, ZulipUser,
} from './types';
import {
  conversationNarrow, conversationTarget, dmConversation, messageConversation, streamConversation,
} from './narrow';
import type { Conversation, ConversationKey } from './narrow';

/**
 * IM 状态与接收循环（FE_PLAN §5.2）：register 快照 → events 长轮询逐批归并；队列失效时重新 register，
 * 并作废非当前会话的窗口与摘要。写操作只提交请求，结果以事件为准。迟到结果按 `session` 丢弃；
 * resetIm 中止挂起请求（身份暂停、generation 变化时由 `useImLifecycle` 调用）。
 */

export interface MessageWindow {
  status: 'loading' | 'ready' | 'failed';
  messages: ZulipMessage[];
  foundOldest: boolean;
  /** 按锚点读出的窗口可能不含最新消息；此时显示「回到最新」，新消息事件也不追加进来。 */
  foundNewest: boolean;
}

const EMPTY_WINDOW: MessageWindow = { status: 'loading', messages: [], foundOldest: false, foundNewest: true };

export type StoredDraft = ZulipDraft & { id: number };

export interface ImState {
  /** disabled：IM 未配置；failed：快照读不到或身份不符（`failure` 为错误码）。 */
  phase: 'idle' | 'loading' | 'ready' | 'failed' | 'disabled';
  failure: string | null;
  /** 事件长轮询断开、正在重连。 */
  offline: boolean;
  session: number;
  identity: ImIdentity | null;
  /** register 返回的事件队列；发送时与 local_id 成对提交（合同 §3.2）。 */
  queueId: string | null;
  selfId: number;
  users: Record<number, ZulipUser>;
  deptFieldId: string | null;
  titleFieldId: string | null;
  subscriptions: Record<number, ZulipSubscription>;
  /** 群（空话题）的话题偏好：1 静音、2 取消静音、3 关注；与群静音一起决定未读是否计入。 */
  topicPolicies: Record<number, number>;
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
  /** 本人点过「标为未读」的消息：离开这个会话之前，自动标读不处理它们。 */
  manualUnread: Record<number, true>;
}

const EMPTY: Omit<ImState, 'session'> = {
  phase: 'idle', failure: null, offline: false, identity: null, queueId: null, selfId: 0, users: {},
  deptFieldId: null, titleFieldId: null, subscriptions: {}, topicPolicies: {}, presences: {}, presenceThreshold: 140,
  serverSkew: 0, enterSends: true, conversations: {}, latest: {}, order: {}, unread: {}, mentions: {}, windows: {},
  selected: null, anchor: null, drafts: {}, realmEmoji: [], maxUploadMib: 25, manualUnread: {},
};

export const useImStore = create<ImState>()(() => ({ ...EMPTY, session: 0 }));

let transport: ImTransport | null = null;
let controller = new AbortController();
let localCounter = 0;
const pendingRead = new Set<number>();
const readInFlight = new Set<number>();
let readTimer: number | undefined;
const windowRequests = new Map<ConversationKey, number>();

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
    ...profileFieldIds(register.custom_profile_fields),
    subscriptions: Object.fromEntries(register.subscriptions.map((sub) => [sub.stream_id, sub])),
    topicPolicies: Object.fromEntries((register.user_topics ?? [])
      .filter((topic) => topic.topic_name === '')
      .map((topic) => [topic.stream_id, topic.visibility_policy])),
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

/** 换身份或离开时调用：中止挂起请求、丢掉全部状态，迟到结果按 session 失效。 */
export function resetIm(): void {
  controller.abort();
  controller = new AbortController();
  transport = null;
  window.clearTimeout(readTimer);
  readTimer = undefined;
  pendingRead.clear();
  readInFlight.clear();
  windowRequests.clear();
  for (const hook of resetHooks) hook();
  useImStore.setState((state) => ({ ...EMPTY, session: state.session + 1 }));
}

/** IM 未配置（configuration 为空）：停在说明状态，不发任何请求。 */
export function disableIm(): void {
  resetIm();
  useImStore.setState({ phase: 'disabled' });
}

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      window.clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}

/** 重试不会好转的错误：停止接收并显示对应说明。401 与 identity_changed 另由登录生命周期重置。 */
const FATAL_CODES = new Set([
  'im_disabled', 'im_mapping_required', 'im_identity_mismatch', 'authentication_required', 'identity_changed',
]);

/**
 * register → 摘要 → 事件循环。`rebuild` 为真时是队列失效后的重建：保留当前会话（并重读它），其余会话的
 * 窗口与摘要全部作废，避免离线期间已删除或已失去权限的内容再次出现。
 */
async function connect(session: number, rebuild: boolean): Promise<void> {
  const api = transport;
  const { signal } = controller;
  if (!api) return;
  for (let attempt = 0; isCurrent(session); attempt += 1) {
    try {
      const [identity, register] = await Promise.all([api.identity(signal), api.register(signal)]);
      if (!isCurrent(session)) return;
      // 合同 §2：绑定与上游本人必须一致，否则停止接收。
      if (identity.im_user_id !== String(register.user_id)) {
        useImStore.setState({ phase: 'failed', failure: 'im_identity_mismatch', offline: false });
        return;
      }
      useImStore.setState((state) => {
        const snapshot = indexSnapshot(register);
        const kept = rebuild && state.selected && snapshot.conversations?.[state.selected] ? state.selected : null;
        const window = kept ? state.windows[kept] : undefined;
        return {
          ...snapshot,
          identity,
          windows: kept && window ? { [kept]: window } : {},
          latest: {},
          selected: rebuild ? kept : state.selected,
          anchor: rebuild && state.anchor?.key !== kept ? null : state.anchor,
          manualUnread: {},
          offline: false,
        };
      });
      // 会话摘要只取已加载窗口内的内容（FE_PLAN §3 第 1 行）。
      const recent = await api.messages({ anchor: 'newest', numBefore: 300, numAfter: 0, narrow: [] }, signal);
      if (!isCurrent(session)) return;
      useImStore.setState((state) => ({ ...absorbLatest(state, recent.messages), phase: 'ready' }));
      const { selected, anchor } = useImStore.getState();
      if (rebuild && selected) void loadWindow(selected, session, anchor?.messageId);
      void receive(session, api, register.queue_id, register.last_event_id);
      return;
    } catch (error) {
      if (!isCurrent(session) || signal.aborted) return;
      const code = failureCode(error);
      if (FATAL_CODES.has(code) || (!rebuild && attempt >= 2)) {
        useImStore.setState({ phase: 'failed', failure: code, offline: false });
        return;
      }
      useImStore.setState({ offline: true });
      await wait(Math.min(30_000, 1000 * 2 ** Math.min(attempt, 5)), signal);
    }
  }
}

/**
 * 长轮询（合同 §3.2）：前端不设更短的超时，只按 AbortSignal 中止。整批归并成功后才推进游标；
 * 队列失效或归并出错都走重建。网络或上游暂时不可用时退避重连，期间显示断线。
 */
async function receive(session: number, api: ImTransport, queueId: string, lastEventId: number): Promise<void> {
  const { signal } = controller;
  let cursor = lastEventId;
  let failures = 0;
  while (isCurrent(session)) {
    let events;
    try {
      ({ events } = await api.events(queueId, cursor, signal));
    } catch (error) {
      if (!isCurrent(session) || signal.aborted) return;
      const code = failureCode(error);
      if (code === 'im_upstream:BAD_EVENT_QUEUE_ID') {
        void connect(session, true);
        return;
      }
      if (FATAL_CODES.has(code)) {
        useImStore.setState({ phase: 'failed', failure: code, offline: false });
        return;
      }
      failures += 1;
      useImStore.setState({ offline: true });
      await wait(Math.min(30_000, 1000 * 2 ** Math.min(failures, 5)), signal);
      continue;
    }
    if (!isCurrent(session)) return;
    const batch = events;
    try {
      useImStore.setState((state) => applyEvents(state, batch));
    } catch {
      void connect(session, true);
      return;
    }
    for (const event of batch) cursor = Math.max(cursor, event.id);
    if (useImStore.getState().offline) useImStore.setState({ offline: false });
    failures = 0;
  }
}

export async function startIm(next: ImTransport): Promise<void> {
  resetIm();
  transport = next;
  const session = useImStore.getState().session;
  useImStore.setState({ phase: 'loading' });
  await connect(session, false);
}

/** 读失败后手动重试（「重新连接」）。 */
export function retryIm(): void {
  const api = transport;
  if (api) void startIm(api);
}

/**
 * 读一个会话的消息窗口。`anchor` 为消息 id 时取它前后各 30 条（搜索命中、返回原消息、消息链接），
 * 否则取最新 60 条。读取期间由事件追加进来的更新消息保留，不被这次结果覆盖掉。
 */
async function loadWindow(key: ConversationKey, session: number, anchor?: number): Promise<void> {
  const conversation = useImStore.getState().conversations[key];
  if (!transport || !conversation) return;
  // 同一会话先后两次定位时，只有最后发起的那次能回写窗口。
  const sequence = (windowRequests.get(key) ?? 0) + 1;
  windowRequests.set(key, sequence);
  const latestRequest = () => isCurrent(session) && windowRequests.get(key) === sequence;
  useImStore.setState((state) => ({
    windows: { ...state.windows, [key]: { ...(state.windows[key] ?? EMPTY_WINDOW), status: 'loading' } },
  }));
  try {
    const result = await transport.messages({
      anchor: anchor ?? 'newest', numBefore: anchor ? 30 : 60, numAfter: anchor ? 30 : 0,
      narrow: conversationNarrow(conversation),
    }, controller.signal);
    if (!latestRequest()) return;
    useImStore.setState((state) => {
      const last = result.messages[result.messages.length - 1]?.id ?? 0;
      const arrived = result.found_newest
        ? (state.windows[key]?.messages ?? []).filter((message) => message.id > last)
        : [];
      return {
        ...absorbLatest(state, result.messages),
        windows: {
          ...state.windows,
          [key]: {
            status: 'ready', messages: [...result.messages, ...arrived], foundOldest: result.found_oldest,
            foundNewest: result.found_newest,
          },
        },
      };
    });
  } catch {
    if (!latestRequest()) return;
    useImStore.setState((state) => ({
      windows: { ...state.windows, [key]: { ...(state.windows[key] ?? EMPTY_WINDOW), status: 'failed' } },
    }));
  }
}

/**
 * 打开会话。滚动定位的优先级（FE_PLAN §2.1）：明确锚点 > 记住的位置 > 最新；锚点不在已加载窗口里时
 * 按锚点重读窗口。换到别的会话时，上一会话里「标为未读」的保护随之结束。
 */
export function openConversation(key: ConversationKey, anchorMessageId?: number): void {
  const state = useImStore.getState();
  useImStore.setState({
    selected: key,
    anchor: anchorMessageId ? { key, messageId: anchorMessageId } : null,
    manualUnread: state.selected === key ? state.manualUnread : {},
  });
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

/* ---------- 自动标读（FE_PLAN §5.2） ---------- */

/**
 * MessageList 在消息进入可见区域、且页面在前台时报上来；攒 300ms 一批提交。成功后先在本地归并，
 * 随后到达的 update_message_flags 事件同形、幂等。失败的 id 下次再看到时重试。
 */
export function markVisibleRead(key: ConversationKey, ids: number[]): void {
  const state = useImStore.getState();
  const unread = new Set(state.unread[key] ?? []);
  for (const id of ids) {
    if (unread.has(id) && !state.manualUnread[id] && !readInFlight.has(id)) pendingRead.add(id);
  }
  if (pendingRead.size === 0 || readTimer !== undefined) return;
  const { session } = state;
  readTimer = window.setTimeout(() => void flushRead(session), 300);
}

async function flushRead(session: number): Promise<void> {
  readTimer = undefined;
  const ids = [...pendingRead];
  pendingRead.clear();
  if (!transport || ids.length === 0 || !isCurrent(session)) return;
  for (const id of ids) readInFlight.add(id);
  try {
    await transport.setFlag(ids, 'read', true);
    if (!isCurrent(session)) return;
    useImStore.setState((state) => applyEvents(state, [
      { id: -1, type: 'update_message_flags', op: 'add', flag: 'read', messages: ids, all: false },
    ]));
  } catch {
    // 不在这里重试：消息仍未读，下次进入可见区域或页面回到前台时再报。
  } finally {
    for (const id of ids) readInFlight.delete(id);
  }
}

/* ---------- 写操作：只提交请求，界面以随后到达的事件为准 ---------- */

/**
 * 发送一条消息。结果不明确时不自动重发（合同 §3.2）；失败原样抛给输入框显示。成功后由 message 事件
 * 插入窗口；不改当前锚点和滚动位置（FE_PLAN §2.1：只有点「回到最新」「N 条新消息」才滚到底）。
 */
export async function sendMessage(key: ConversationKey, content: string): Promise<void> {
  const state = useImStore.getState();
  const conversation = state.conversations[key];
  if (!conversation || !state.queueId) throw new ApiError(409, 'im_not_ready', '消息还没有准备好。');
  const localId = `${Date.now()}.${++localCounter}`;
  await requireTransport().send({ target: conversationTarget(conversation), content, queueId: state.queueId, localId });
}

export function readRawContent(messageId: number): Promise<string> {
  return requireTransport().rawContent(messageId);
}

export async function editMessage(messageId: number, content: string): Promise<void> {
  await requireTransport().editMessage(messageId, content);
}

export async function deleteMessage(messageId: number): Promise<void> {
  await requireTransport().deleteMessage(messageId);
}

export async function toggleReaction(message: ZulipMessage, emoji: ZulipEmojiRef): Promise<void> {
  const { selfId } = useImStore.getState();
  const mine = message.reactions.some((item) => item.user_id === selfId && item.emoji_code === emoji.code);
  await requireTransport().react(message.id, emoji, !mine);
}

export async function toggleStar(message: ZulipMessage): Promise<void> {
  await requireTransport().setFlag([message.id], 'starred', !message.flags.includes('starred'));
}

/** 标为未读：同时登记保护，离开这个会话之前自动标读不处理它（FE_PLAN §5.2）。 */
export async function markUnread(key: ConversationKey, message: ZulipMessage): Promise<void> {
  const { session } = useImStore.getState();
  await requireTransport().setFlag([message.id], 'read', false);
  if (!isCurrent(session)) return;
  useImStore.setState((state) => ({
    unread: { ...state.unread, [key]: [...new Set([...(state.unread[key] ?? []), message.id])].sort((a, b) => a - b) },
    manualUnread: { ...state.manualUnread, [message.id]: true },
  }));
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

/** 新建群：由本人确认后才提交（FE_PLAN §3「＋」一行）。成功后先在本地登记，随后的 subscription 事件校正。 */
export async function createGroup(name: string, userIds: number[]): Promise<void> {
  const { session, selfId } = useImStore.getState();
  // 创建者不会被上游自动订阅：请求和本地登记用同一份含本人的成员集合。
  const members = [...new Set([selfId, ...userIds])];
  const { id } = await requireTransport().createGroup(name, members);
  if (!isCurrent(session)) return;
  const conversation = streamConversation(id);
  useImStore.setState((state) => ({
    subscriptions: {
      ...state.subscriptions,
      [id]: {
        stream_id: id, name, description: '', invite_only: true, is_muted: false, pin_to_top: false,
        color: '#76ce90', subscribers: members,
      },
    },
    conversations: { ...state.conversations, [conversation.key]: conversation },
    // 排在当前最新会话之列，下一条消息到达后按实际 id 排序。
    order: { ...state.order, [conversation.key]: Math.max(0, ...Object.values(state.order).map((value) => value ?? 0)) },
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
