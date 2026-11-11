import { conversationDrafts } from '../zulip/imStore';
import { imFileUrl } from '../zulip/links';
import type { ImState } from '../zulip/imStore';
import type { Conversation, ConversationKey } from '../zulip/narrow';
import type { ZulipEmojiRef, ZulipMessage } from '../zulip/types';

/** 界面用的纯函数：名称、头像、在线状态、时间与摘要。只读 store 快照，不发请求。 */

export type AvatarTone = 'c1' | 'c2' | 'c3' | 'c4' | 'c5' | 'c6';
const TONES: AvatarTone[] = ['c1', 'c2', 'c3', 'c4', 'c5', 'c6'];

export function avatarTone(seed: number): AvatarTone {
  return TONES[Math.abs(seed) % TONES.length]!;
}

/** 头像上的字：取姓名最后两个字；含「×」的脱敏名只取第一个字。 */
export function avatarText(name: string): string {
  const chars = [...name.trim()];
  if (chars.length === 0) return '?';
  return name.includes('×') ? chars[0]! : chars.slice(-2).join('');
}

export function userName(state: ImState, userId: number): string {
  return state.users[userId]?.full_name ?? `用户 ${userId}`;
}

function profileValue(state: ImState, userId: number, fieldId: string | null): string | null {
  if (!fieldId) return null;
  const value = state.users[userId]?.profile_data?.[fieldId]?.value?.trim();
  return value ? value : null;
}

export function userDept(state: ImState, userId: number): string | null {
  return profileValue(state, userId, state.deptFieldId);
}

export function userTitle(state: ImState, userId: number): string | null {
  return profileValue(state, userId, state.titleFieldId);
}

export type PresenceState = 'online' | 'away' | 'off';

/** 12.3 simplified presence：active 在阈值内为在线，idle 在阈值内为离开，其余离线。 */
export function userPresence(state: ImState, userId: number): PresenceState {
  const presence = state.presences[userId];
  if (!presence) return 'off';
  const now = Date.now() / 1000 + state.serverSkew;
  const fresh = (value: number | undefined) => value !== undefined && now - value <= state.presenceThreshold;
  if (fresh(presence.active_timestamp)) return 'online';
  if (fresh(presence.idle_timestamp)) return 'away';
  return 'off';
}

export const PRESENCE_LABEL: Record<PresenceState, string> = { online: '在线', away: '离开', off: '离线' };

export type ConversationAvatar =
  | { kind: 'person'; userId: number; text: string; tone: AvatarTone; url: string | null; bot: boolean }
  | { kind: 'group'; cells: { text: string; tone: AvatarTone }[] };

export function conversationTitle(state: ImState, conversation: Conversation): string {
  if (conversation.kind === 'stream') return state.subscriptions[conversation.streamId]?.name ?? '未知群聊';
  if (conversation.userIds.length === 1 && conversation.userIds[0] === state.selfId) {
    return `${userName(state, state.selfId)}（我）`;
  }
  return conversation.userIds.map((id) => userName(state, id)).join('、');
}

export function personAvatar(state: ImState, userId: number): ConversationAvatar {
  const user = state.users[userId];
  return {
    kind: 'person', userId, text: avatarText(user?.full_name ?? ''), tone: avatarTone(userId),
    url: user?.avatar_url ?? null, bot: user?.is_bot ?? false,
  };
}

export function conversationAvatar(state: ImState, conversation: Conversation): ConversationAvatar {
  if (conversation.kind === 'dm' && conversation.userIds.length === 1) return personAvatar(state, conversation.userIds[0]!);
  // 宫格头像优先显示其他成员，本人不占位。
  const members = (conversation.kind === 'dm'
    ? conversation.userIds
    : state.subscriptions[conversation.streamId]?.subscribers ?? []).filter((id) => id !== state.selfId);
  const title = conversationTitle(state, conversation);
  const cells = members.slice(0, 4).map((id) => ({ text: [...userName(state, id)][0] ?? '', tone: avatarTone(id) }));
  return { kind: 'group', cells: cells.length > 0 ? cells : [{ text: [...title][0] ?? '群', tone: avatarTone(title.length) }] };
}

export function conversationMemberCount(state: ImState, conversation: Conversation): number | null {
  if (conversation.kind === 'dm') return conversation.userIds.length + (conversation.userIds[0] === state.selfId ? 0 : 1);
  return state.subscriptions[conversation.streamId]?.subscribers?.length ?? null;
}

/* ---------- 时间 ---------- */

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function dayDiff(timestamp: number, now = new Date()): number {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const then = new Date(timestamp * 1000);
  const day = new Date(then.getFullYear(), then.getMonth(), then.getDate()).getTime();
  return Math.round((start - day) / 86400000);
}

export function clockTime(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function dayLabel(timestamp: number): string {
  const diff = dayDiff(timestamp);
  const date = new Date(timestamp * 1000);
  if (diff === 0) return '今天';
  if (diff === 1) return '昨天';
  if (diff > 1 && diff < 7) return WEEKDAYS[date.getDay()]!;
  if (date.getFullYear() === new Date().getFullYear()) return `${date.getMonth() + 1}月${date.getDate()}日`;
  return `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
}

/** 会话列表右上角：今天显示时分，其余显示日期。 */
export function listTime(timestamp: number): string {
  return dayDiff(timestamp) === 0 ? clockTime(timestamp) : dayLabel(timestamp);
}

/** 消息记录里的时间分隔：「今天 09:12」「昨天 16:05」。 */
export function separatorTime(timestamp: number): string {
  return `${dayLabel(timestamp)} ${clockTime(timestamp)}`;
}

/** 与上一条相隔 1 小时以上或跨天时显示时间分隔；每条消息的时分已在发送人旁显示。 */
export function needsSeparator(previous: ZulipMessage | undefined, current: ZulipMessage): boolean {
  if (!previous) return true;
  return current.timestamp - previous.timestamp >= 3600 || dayDiff(previous.timestamp) !== dayDiff(current.timestamp);
}

/* ---------- 消息正文的形状 ---------- */

export type FileKind = 'xls' | 'doc' | 'pdf' | 'ppt' | 'zip' | 'other';

export type MessageShape =
  | { kind: 'html' }
  | { kind: 'file'; name: string; href: string; ext: string; fileKind: FileKind }
  | { kind: 'image'; src: string; href: string; title: string };

const FILE_KINDS: [RegExp, FileKind][] = [
  [/^(xls|xlsx|csv|et)$/, 'xls'],
  [/^(doc|docx|wps|txt|rtf)$/, 'doc'],
  [/^pdf$/, 'pdf'],
  [/^(ppt|pptx|dps)$/, 'ppt'],
  [/^(zip|rar|7z|tar|gz)$/, 'zip'],
];

function inertDocument(html: string): Document {
  // DOMParser 生成的文档不执行脚本、不加载资源，只用来读结构和纯文本。
  return new DOMParser().parseFromString(html, 'text/html');
}

/**
 * 只含一个上传链接的消息显示为文件卡；只含一张内联图片的显示为图片；其余走净化后的 HTML 气泡。
 * 返回的 href/src 仍是原始值，由 MessageBody 统一改写到代理入口。
 */
export function messageShape(html: string): MessageShape {
  const body = inertDocument(html).body;
  const blocks = [...body.children];
  if (blocks.length === 1 && blocks[0]!.matches('div.message_inline_image')) {
    const link = blocks[0]!.querySelector('a');
    const image = blocks[0]!.querySelector('img');
    if (link && image) {
      return {
        kind: 'image', src: image.getAttribute('src') ?? '', href: link.getAttribute('href') ?? '',
        title: link.getAttribute('title') ?? '图片',
      };
    }
  }
  if (blocks.length === 1 && blocks[0]!.tagName === 'P' && blocks[0]!.childNodes.length === 1) {
    const link = blocks[0]!.firstElementChild;
    const href = link?.getAttribute('href') ?? '';
    if (link?.tagName === 'A' && imFileUrl(href)?.includes('/files/user_uploads/')) {
      const name = link.textContent?.trim() || decodeURIComponent(href.split('/').pop() ?? '文件');
      const ext = (name.split('.').pop() ?? '').toLowerCase();
      const fileKind = FILE_KINDS.find(([pattern]) => pattern.test(ext))?.[1] ?? 'other';
      return { kind: 'file', name, href, ext, fileKind };
    }
  }
  return { kind: 'html' };
}

export function messageText(html: string): string {
  // 段落、引用、换行之间补一个空格，避免摘要里前后两段文字粘在一起。
  const spaced = html.replace(/<\/(p|blockquote|div|li|h[1-6]|pre)>|<br\s*\/?>/gi, '$& ');
  const doc = inertDocument(spaced);
  // Unicode emoji 在服务端 HTML 里是 `:name:` 文字加 `emoji-<码点>` class，摘要里换回字符本身（同 MessageBody）。
  for (const span of doc.body.querySelectorAll('span.emoji')) {
    const code = [...span.classList].find((name) => /^emoji-[0-9a-f-]+$/.test(name));
    if (code) span.textContent = String.fromCodePoint(...code.slice(6).split('-').map((part) => parseInt(part, 16)));
  }
  return (doc.body.textContent ?? '').replace(/\s+/g, ' ').trim();
}

/** 会话列表的摘要：群里带发送人，文件和图片用方括号标出。 */
export function messageSummary(state: ImState, message: ZulipMessage): string {
  const shape = messageShape(message.content);
  const text = shape.kind === 'file' ? `[文件] ${shape.name}` : shape.kind === 'image' ? '[图片]' : messageText(message.content);
  if (message.type === 'stream' && message.sender_id !== state.selfId) return `${userName(state, message.sender_id)}：${text}`;
  return text;
}

/** 表态按 emoji 归并计数；`mine` 表示本人也点过。 */
export function groupReactions(message: ZulipMessage, selfId: number) {
  const groups = new Map<string, { key: string; label: string; count: number; mine: boolean; emoji: ZulipEmojiRef }>();
  for (const reaction of message.reactions) {
    const key = `${reaction.reaction_type}:${reaction.emoji_code}`;
    const label = reaction.reaction_type === 'unicode_emoji'
      ? String.fromCodePoint(...reaction.emoji_code.split('-').map((part) => parseInt(part, 16)))
      : `:${reaction.emoji_name}:`;
    const group = groups.get(key) ?? {
      key, label, count: 0, mine: false,
      emoji: { name: reaction.emoji_name, code: reaction.emoji_code, type: reaction.reaction_type },
    };
    group.count += 1;
    group.mine ||= reaction.user_id === selfId;
    groups.set(key, group);
  }
  return [...groups.values()];
}

/* ---------- 会话列表 ---------- */

export interface ConversationRow {
  key: ConversationKey;
  title: string;
  time: string;
  summary: string;
  unread: number;
  mentioned: boolean;
  /** 本会话有服务端已保存的草稿；正在看的会话不显示。 */
  draft: boolean;
  muted: boolean;
  isGroup: boolean;
}

/**
 * 群里某个话题是否静音（12.3 原生规则）：话题偏好「静音」直接静音；群静音时，话题偏好「取消静音」
 * 「关注」例外。新群只用空话题；旧的多话题群逐话题判断。
 */
export function topicMuted(state: ImState, streamId: number, topic: string): boolean {
  const policy = state.topicPolicies[`${streamId}:${topic}`] ?? 0;
  if (policy === 1) return true;
  return (state.subscriptions[streamId]?.is_muted ?? false) && policy !== 2 && policy !== 3;
}

/** 会话行是否显示为静音：看空话题（新群唯一的话题）。单聊不静音。 */
export function conversationMuted(state: ImState, conversation: Conversation): boolean {
  return conversation.kind === 'stream' && topicMuted(state, conversation.streamId, '');
}

/** 一个会话里计入提醒的未读数：群按每条未读所在话题判断静音。 */
export function countedUnread(state: ImState, conversation: Conversation): number {
  const ids = state.unread[conversation.key] ?? [];
  if (conversation.kind !== 'stream') return ids.length;
  return ids.filter((id) => !topicMuted(state, conversation.streamId, state.unreadTopics[id] ?? '')).length;
}

/** 未读总数（左导航角标、列表页签）：静音的群和话题不计入。 */
export function unreadTotal(state: ImState): number {
  let total = 0;
  for (const conversation of Object.values(state.conversations)) {
    if (conversation) total += countedUnread(state, conversation);
  }
  return total;
}

/** 读不到消息时给使用者看的说明（按合同错误码）。 */
export function imFailureText(code: string | null): string {
  if (code === 'im_mapping_required') return '你的消息账号还没有绑定，请联系管理员处理。';
  if (code === 'im_identity_mismatch') return '消息账号和绑定的身份对不上，请联系管理员核对。';
  if (code === 'im_disabled') return '单位消息尚未启用。';
  return '消息服务暂时连不上。';
}

/** 会话列表的行：置顶在前，其余按最新消息 id 倒序。 */
export function conversationRows(state: ImState): ConversationRow[] {
  const rows: (ConversationRow & { order: number; pinned: boolean })[] = [];
  for (const conversation of Object.values(state.conversations)) {
    if (!conversation) continue;
    const sub = conversation.kind === 'stream' ? state.subscriptions[conversation.streamId] : undefined;
    const latest = state.latest[conversation.key];
    const unreadIds = state.unread[conversation.key] ?? [];
    const draft = state.selected === conversation.key ? undefined : conversationDrafts(state, conversation.key)[0];
    rows.push({
      key: conversation.key,
      title: conversationTitle(state, conversation),
      time: latest ? listTime(latest.timestamp) : '',
      summary: draft ? draft.content.replace(/\s+/g, ' ') : latest ? messageSummary(state, latest) : '',
      draft: draft !== undefined,
      unread: unreadIds.length,
      mentioned: unreadIds.some((id) => state.mentions[id]),
      muted: conversationMuted(state, conversation),
      isGroup: conversation.kind === 'stream' || conversation.userIds.length > 1,
      order: state.order[conversation.key] ?? 0,
      pinned: sub?.pin_to_top ?? false,
    });
  }
  return rows.sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.order - a.order);
}
