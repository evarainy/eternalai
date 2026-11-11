import type { ImState, StoredDraft } from './imStore';
import { dmConversation, messageConversation, streamConversation } from './narrow';
import type { ConversationKey } from './narrow';
import type {
  ZulipDraft, ZulipEvent, ZulipMessage, ZulipPresence, ZulipProfileField, ZulipRealmEmoji, ZulipSubscription,
  ZulipUser,
} from './types';

/**
 * 事件归并（FE_PLAN §5.2）：按 12.3 的事件形状把一批事件折叠进状态，纯函数、不发请求。
 * 归并按服务端消息 id 去重；同一 id 再次到达不重复插入。`heartbeat` 和用不到的类型（realm、
 * user_group 等）原样跳过。调用方只在整批归并成功后才推进 `last_event_id`。
 */

const MENTION_FLAGS = ['mentioned', 'stream_wildcard_mentioned', 'topic_wildcard_mentioned', 'wildcard_mentioned'];

type Windows = ImState['windows'];
type Latest = ImState['latest'];

function entries<T>(record: Partial<Record<ConversationKey, T>>): [ConversationKey, T][] {
  return Object.entries(record).filter((entry): entry is [ConversationKey, T] => entry[1] !== undefined);
}

function ids(event: ZulipEvent): Set<number> {
  const many = event.message_ids;
  if (Array.isArray(many)) return new Set(many as number[]);
  return new Set(typeof event.message_id === 'number' ? [event.message_id] : []);
}

/** 对窗口和摘要里命中 `targets` 的消息逐条变换；返回 null 表示移除。 */
function changeMessages(
  state: ImState,
  targets: ReadonlySet<number>,
  change: (message: ZulipMessage) => ZulipMessage | null,
): ImState {
  if (targets.size === 0) return state;
  const windows: Windows = { ...state.windows };
  for (const [key, window] of entries(state.windows)) {
    if (!window.messages.some((message) => targets.has(message.id))) continue;
    windows[key] = {
      ...window,
      messages: window.messages.flatMap((message) => {
        if (!targets.has(message.id)) return [message];
        const next = change(message);
        return next ? [next] : [];
      }),
    };
  }
  const latest: Latest = { ...state.latest };
  for (const [key, message] of entries(state.latest)) {
    if (!targets.has(message.id)) continue;
    const next = change(message);
    if (next) {
      latest[key] = next;
    } else {
      // 摘要被删时退到已加载窗口里的上一条；窗口没加载就不显示摘要。
      const rest = windows[key]?.messages;
      const tail = rest?.[rest.length - 1];
      if (tail) latest[key] = tail;
      else delete latest[key];
    }
  }
  return { ...state, windows, latest };
}

function withoutUnread(state: ImState, targets: ReadonlySet<number> | 'all'): ImState {
  const unread: ImState['unread'] = {};
  if (targets !== 'all') {
    for (const [key, list] of entries(state.unread)) {
      const kept = list.filter((id) => !targets.has(id));
      if (kept.length > 0) unread[key] = kept;
    }
  }
  const mentions = targets === 'all' ? {} : { ...state.mentions };
  const unreadTopics = targets === 'all' ? {} : { ...state.unreadTopics };
  if (targets !== 'all') {
    for (const id of targets) {
      delete mentions[id];
      delete unreadTopics[id];
    }
  }
  // 「标为未读」的保护（manualUnread）不在这里清：它只随换会话结束，迟到的已读事件不能把它撤掉。
  return { ...state, unread, mentions, unreadTopics };
}

function addUnread(state: ImState, key: ConversationKey, id: number, mentioned: boolean, topic: string | null): ImState {
  const list = state.unread[key] ?? [];
  return {
    ...state,
    unread: list.includes(id) ? state.unread : { ...state.unread, [key]: [...list, id].sort((a, b) => a - b) },
    mentions: mentioned ? { ...state.mentions, [id]: true } : state.mentions,
    unreadTopics: topic === null ? state.unreadTopics : { ...state.unreadTopics, [id]: topic },
  };
}

function insertSorted(messages: ZulipMessage[], message: ZulipMessage): ZulipMessage[] {
  if (messages.some((item) => item.id === message.id)) return messages;
  const next = [...messages, message];
  return next.sort((a, b) => a.id - b.id);
}

function onMessage(state: ImState, event: ZulipEvent): ImState {
  const flags = Array.isArray(event.flags) ? (event.flags as string[]) : [];
  const message = { ...(event.message as ZulipMessage), flags };
  const conversation = messageConversation(message, state.selfId);
  const { key } = conversation;
  const window = state.windows[key];
  const latest = state.latest[key];
  let next: ImState = {
    ...state,
    conversations: state.conversations[key] ? state.conversations : { ...state.conversations, [key]: conversation },
    latest: !latest || latest.id < message.id ? { ...state.latest, [key]: message } : state.latest,
    order: { ...state.order, [key]: Math.max(state.order[key] ?? 0, message.id) },
    // 窗口停在较早的位置时不追加：用户点「回到最新」时会重读。
    windows: window && window.foundNewest
      ? { ...state.windows, [key]: { ...window, messages: insertSorted(window.messages, message) } }
      : state.windows,
  };
  if (!flags.includes('read')) {
    next = addUnread(next, key, message.id, flags.some((flag) => MENTION_FLAGS.includes(flag)),
      message.type === 'stream' ? message.subject : null);
  }
  return next;
}

function onUpdateMessage(state: ImState, event: ZulipEvent): ImState {
  const targets = ids(event);
  // 跨群移动：12.3 带 new_stream_id。旧窗口里移除，新群窗口作废、下次打开时重读。
  if (typeof event.new_stream_id === 'number') {
    const moved = changeMessages(state, targets, () => null);
    const target = streamConversation(event.new_stream_id).key;
    const windows = { ...moved.windows };
    delete windows[target];
    const unread: ImState['unread'] = {};
    const carried: number[] = [];
    for (const [key, list] of entries(moved.unread)) {
      const kept = list.filter((id) => !targets.has(id));
      carried.push(...list.filter((id) => targets.has(id)));
      if (kept.length > 0) unread[key] = kept;
    }
    if (carried.length > 0) unread[target] = [...(unread[target] ?? []), ...carried].sort((a, b) => a - b);
    return { ...moved, windows, unread };
  }
  let next = state;
  // 话题改名（12.3 带 subject）：同步消息的话题和未读所在话题，静音按新话题判断。
  if (typeof event.subject === 'string') {
    const topic = event.subject;
    next = changeMessages(next, targets, (message) => ({ ...message, subject: topic }));
    const unreadTopics = { ...next.unreadTopics };
    for (const id of targets) if (id in unreadTopics) unreadTopics[id] = topic;
    next = { ...next, unreadTopics };
  }
  const messageId = event.message_id;
  if (typeof messageId !== 'number') return next;
  // 编辑后本人的标记（例如新加或去掉了 @本人）随事件给出，提醒状态跟着正文一起变。
  if (Array.isArray(event.flags)) {
    const flags = event.flags as string[];
    next = changeMessages(next, new Set([messageId]), (message) => ({ ...message, flags }));
    const mentions = { ...next.mentions };
    if (!flags.includes('read') && flags.some((flag) => MENTION_FLAGS.includes(flag))) mentions[messageId] = true;
    else delete mentions[messageId];
    next = { ...next, mentions };
  }
  const content = event.rendered_content;
  if (typeof content !== 'string') return next;
  const renderingOnly = event.rendering_only === true;
  const editedAt = typeof event.edit_timestamp === 'number' ? event.edit_timestamp : undefined;
  return changeMessages(next, new Set([messageId]), (message) => ({
    ...message,
    content,
    last_edit_timestamp: renderingOnly ? message.last_edit_timestamp : editedAt ?? message.last_edit_timestamp,
  }));
}

function onDeleteMessage(state: ImState, event: ZulipEvent): ImState {
  const targets = ids(event);
  return withoutUnread(changeMessages(state, targets, () => null), targets);
}

function messageDetailsKey(state: ImState, detail: unknown): ConversationKey | null {
  if (typeof detail !== 'object' || detail === null) return null;
  const value = detail as { type?: string; stream_id?: number; user_ids?: number[] };
  if (value.type === 'stream' && typeof value.stream_id === 'number') return streamConversation(value.stream_id).key;
  if (value.type === 'private') return dmConversation(value.user_ids ?? [], state.selfId).key;
  return null;
}

function onFlags(state: ImState, event: ZulipEvent): ImState {
  const flag = event.flag;
  const add = event.op === 'add';
  if (typeof flag !== 'string') return state;
  const all = event.all === true;
  const targets = new Set(Array.isArray(event.messages) ? (event.messages as number[]) : []);
  const setFlag = (message: ZulipMessage): ZulipMessage => {
    const has = message.flags.includes(flag);
    if (has === add) return message;
    return { ...message, flags: add ? [...message.flags, flag] : message.flags.filter((item) => item !== flag) };
  };
  let next = all
    ? changeMessages(state, new Set(entries(state.windows).flatMap(([, window]) => window.messages.map((m) => m.id))
      .concat(entries(state.latest).map(([, message]) => message.id))), setFlag)
    : changeMessages(state, targets, setFlag);
  if (flag !== 'read') return next;
  if (add) return withoutUnread(next, all ? 'all' : targets);
  // 标为未读（本人在任何端操作）：按 message_details 放回对应会话的未读。
  const details = (typeof event.message_details === 'object' && event.message_details !== null
    ? event.message_details : {}) as Record<string, unknown>;
  for (const id of targets) {
    const detail = details[String(id)];
    const key = messageDetailsKey(next, detail);
    if (!key) continue;
    const value = (typeof detail === 'object' && detail !== null ? detail : {}) as { mentioned?: boolean; topic?: string };
    next = addUnread(next, key, id, value.mentioned === true, typeof value.topic === 'string' ? value.topic : null);
  }
  return next;
}

function onReaction(state: ImState, event: ZulipEvent): ImState {
  const messageId = event.message_id;
  if (typeof messageId !== 'number') return state;
  const reaction = {
    emoji_name: String(event.emoji_name), emoji_code: String(event.emoji_code),
    reaction_type: event.reaction_type as ZulipMessage['reactions'][number]['reaction_type'],
    user_id: Number(event.user_id),
  };
  const same = (item: ZulipMessage['reactions'][number]) => item.user_id === reaction.user_id
    && item.emoji_code === reaction.emoji_code && item.reaction_type === reaction.reaction_type;
  return changeMessages(state, new Set([messageId]), (message) => ({
    ...message,
    reactions: event.op === 'add'
      ? (message.reactions.some(same) ? message.reactions : [...message.reactions, reaction])
      : message.reactions.filter((item) => !same(item)),
  }));
}

/** 失去一个群的访问（退群、被移出、群被删）：该群的全部本地状态立即丢掉。 */
function dropStream(state: ImState, streamId: number): ImState {
  const key = streamConversation(streamId).key;
  const subscriptions = { ...state.subscriptions };
  delete subscriptions[streamId];
  const conversations = { ...state.conversations };
  const windows = { ...state.windows };
  const latest = { ...state.latest };
  const unread = { ...state.unread };
  delete conversations[key];
  delete windows[key];
  delete latest[key];
  delete unread[key];
  return {
    ...state, subscriptions, conversations, windows, latest, unread,
    selected: state.selected === key ? null : state.selected,
    anchor: state.anchor?.key === key ? null : state.anchor,
  };
}

function updateSubscription(state: ImState, streamId: number, patch: Partial<ZulipSubscription>): ImState {
  const sub = state.subscriptions[streamId];
  return sub ? { ...state, subscriptions: { ...state.subscriptions, [streamId]: { ...sub, ...patch } } } : state;
}

function onSubscription(state: ImState, event: ZulipEvent): ImState {
  switch (event.op) {
    case 'add': {
      let next = state;
      for (const sub of (event.subscriptions as ZulipSubscription[] | undefined) ?? []) {
        const conversation = streamConversation(sub.stream_id);
        next = {
          ...next,
          subscriptions: { ...next.subscriptions, [sub.stream_id]: sub },
          conversations: { ...next.conversations, [conversation.key]: next.conversations[conversation.key] ?? conversation },
        };
      }
      return next;
    }
    case 'remove': {
      let next = state;
      for (const sub of (event.subscriptions as { stream_id: number }[] | undefined) ?? []) next = dropStream(next, sub.stream_id);
      return next;
    }
    case 'update': {
      const streamId = Number(event.stream_id);
      const { property, value } = event;
      if (property === 'is_muted') return updateSubscription(state, streamId, { is_muted: value === true });
      if (property === 'in_home_view') return updateSubscription(state, streamId, { is_muted: value !== true });
      if (property === 'pin_to_top') return updateSubscription(state, streamId, { pin_to_top: value === true });
      if (property === 'color' && typeof value === 'string') return updateSubscription(state, streamId, { color: value });
      return state;
    }
    case 'peer_add':
    case 'peer_remove': {
      const streamIds = (event.stream_ids as number[] | undefined) ?? [];
      const userIds = new Set((event.user_ids as number[] | undefined) ?? []);
      let next = state;
      for (const streamId of streamIds) {
        const sub = next.subscriptions[streamId];
        if (!sub?.subscribers) continue;
        const subscribers = event.op === 'peer_add'
          ? [...new Set([...sub.subscribers, ...userIds])]
          : sub.subscribers.filter((id) => !userIds.has(id));
        next = updateSubscription(next, streamId, { subscribers });
      }
      return next;
    }
    default:
      return state;
  }
}

function onStream(state: ImState, event: ZulipEvent): ImState {
  if (event.op === 'delete') {
    const removed = Array.isArray(event.stream_ids)
      ? (event.stream_ids as number[])
      : ((event.streams as { stream_id: number }[] | undefined) ?? []).map((item) => item.stream_id);
    return removed.reduce(dropStream, state);
  }
  if (event.op !== 'update') return state;
  const streamId = Number(event.stream_id);
  const { property, value } = event;
  if (property === 'name' && typeof value === 'string') return updateSubscription(state, streamId, { name: value });
  if (property === 'description' && typeof value === 'string') return updateSubscription(state, streamId, { description: value });
  if (property === 'invite_only') return updateSubscription(state, streamId, { invite_only: value === true });
  return state;
}

function onRealmUser(state: ImState, event: ZulipEvent): ImState {
  const person = event.person as (Partial<ZulipUser> & { user_id: number; custom_profile_field?: { id: number; value: string | null; rendered_value?: string } }) | undefined;
  if (!person) return state;
  const current = state.users[person.user_id];
  if (event.op === 'add') return { ...state, users: { ...state.users, [person.user_id]: person as ZulipUser } };
  if (!current) return state;
  if (event.op === 'remove') return { ...state, users: { ...state.users, [person.user_id]: { ...current, is_active: false } } };
  if (event.op !== 'update') return state;
  const { custom_profile_field: field, ...rest } = person;
  let profile = current.profile_data;
  if (field) {
    profile = { ...profile };
    if (field.value === null) delete profile[String(field.id)];
    else profile[String(field.id)] = { value: field.value, rendered_value: field.rendered_value };
  }
  return { ...state, users: { ...state.users, [person.user_id]: { ...current, ...rest, profile_data: profile } } };
}

function onPresence(state: ImState, event: ZulipEvent): ImState {
  const presences = event.presences as Record<string, ZulipPresence> | undefined;
  if (!presences) return state;
  const next = { ...state.presences };
  for (const [id, value] of Object.entries(presences)) next[Number(id)] = { ...next[Number(id)], ...value };
  return { ...state, presences: next };
}

function onDrafts(state: ImState, event: ZulipEvent): ImState {
  const drafts = { ...state.drafts };
  if (event.op === 'add') {
    for (const draft of (event.drafts as ZulipDraft[] | undefined) ?? []) {
      if (draft.id !== undefined) drafts[draft.id] = draft as StoredDraft;
    }
  } else if (event.op === 'update') {
    const draft = event.draft as ZulipDraft | undefined;
    if (draft?.id !== undefined) drafts[draft.id] = draft as StoredDraft;
  } else if (event.op === 'remove' && typeof event.draft_id === 'number') {
    delete drafts[event.draft_id];
  } else {
    return state;
  }
  return { ...state, drafts };
}

function onUserTopic(state: ImState, event: ZulipEvent): ImState {
  const { stream_id: streamId, topic_name: topic, visibility_policy: policy } = event;
  if (typeof streamId !== 'number' || typeof topic !== 'string' || typeof policy !== 'number') return state;
  return { ...state, topicPolicies: { ...state.topicPolicies, [`${streamId}:${topic}`]: policy } };
}

export function profileFieldIds(fields: ZulipProfileField[]): Pick<ImState, 'deptFieldId' | 'titleFieldId'> {
  const find = (name: string) => {
    const field = fields.find((item) => item.name === name);
    return field ? String(field.id) : null;
  };
  return { deptFieldId: find('部门'), titleFieldId: find('职务') };
}

function applyEvent(state: ImState, event: ZulipEvent): ImState {
  switch (event.type) {
    case 'message': return onMessage(state, event);
    case 'update_message': return onUpdateMessage(state, event);
    case 'delete_message': return onDeleteMessage(state, event);
    case 'update_message_flags': return onFlags(state, event);
    case 'reaction': return onReaction(state, event);
    case 'subscription': return onSubscription(state, event);
    case 'stream': return onStream(state, event);
    case 'realm_user': return onRealmUser(state, event);
    case 'presence': return onPresence(state, event);
    case 'drafts': return onDrafts(state, event);
    case 'user_topic': return onUserTopic(state, event);
    case 'user_settings':
      return event.property === 'enter_sends' && typeof event.value === 'boolean' ? { ...state, enterSends: event.value } : state;
    case 'realm_emoji':
      return event.op === 'update' && typeof event.realm_emoji === 'object' && event.realm_emoji !== null
        ? { ...state, realmEmoji: Object.values(event.realm_emoji as Record<string, ZulipRealmEmoji>).filter((emoji) => !emoji.deactivated) }
        : state;
    case 'custom_profile_fields':
      return Array.isArray(event.fields) ? { ...state, ...profileFieldIds(event.fields as ZulipProfileField[]) } : state;
    default:
      return state;
  }
}

/** 整批归并。抛错时调用方不推进游标，按队列失效重建（见 imStore 接收循环）。 */
export function applyEvents(state: ImState, events: ZulipEvent[]): ImState {
  return events.reduce(applyEvent, state);
}
