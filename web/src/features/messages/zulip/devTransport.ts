import { ApiError } from '../../../api/mutator';
import type { ImTransport } from './transport';
import type {
  ZulipDirectMessage, ZulipDraft, ZulipEvent, ZulipMessage, ZulipNarrowTerm, ZulipRegisterResponse, ZulipStreamMessage,
  ZulipSubscription, ZulipUser,
} from './types';

/**
 * 开发期合成数据（属于 Mock，FE_PLAN §4.2）。只由 `import.meta.env.DEV` 下的动态 import 加载，
 * 不进生产包；不模拟事件队列语义，只给当前界面开发用，不算真实接通，也不算业务验收。
 * 真实接口出错时不能切到这里。全部人名、单位、内容均为合成数据。
 */

const SELF = 1;
const DEPT_FIELD = 1;
const TITLE_FIELD = 2;

function person(id: number, name: string, dept: string, title = ''): Omit<ZulipUser, 'is_active'> {
  const profile_data: ZulipUser['profile_data'] = { [DEPT_FIELD]: { value: dept } };
  if (title) profile_data[TITLE_FIELD] = { value: title };
  return {
    user_id: id, full_name: name, email: `user${id}@example.invalid`, avatar_url: null,
    is_bot: false, profile_data,
  };
}

// 与 12.3 的 realm_users 一致：不带 is_active。
const USERS: Omit<ZulipUser, 'is_active'>[] = [
  person(SELF, '王××', '办公室', '科员'),
  person(2, '李明', '规划处', '副处长'),
  person(3, '王芳', '财务处'),
  person(4, '张伟', '规划处'),
  person(5, '陈静', '办公室'),
  person(6, '刘洋', '办公室'),
  person(7, '赵磊', '信息中心'),
  person(8, '周涛', '安全生产科'),
  { ...person(9, '我的 AI 助手', ''), is_bot: true, profile_data: {} },
];

function stream(id: number, name: string, subscribers: number[], extra: Partial<ZulipSubscription> = {}): ZulipSubscription {
  return {
    stream_id: id, name, description: '', invite_only: true, is_muted: false, pin_to_top: false,
    color: '#76ce90', subscribers, topics_policy: 'empty_topic_only', ...extra,
  };
}

const SEED_SUBSCRIPTIONS: ZulipSubscription[] = [
  stream(10, '城市更新专项工作组', [1, 2, 3, 4, 5, 6, 7, 8], { description: '专项资金使用方案与会签材料' }),
  stream(11, '安全生产检查小组', [1, 4, 8]),
  stream(12, '办公室全体', [1, 2, 3, 4, 5, 6, 7, 8], { is_muted: true }),
  stream(13, '财务审核群', [1, 3, 5], { is_muted: true }),
  // 迁移来的旧群：沿用组织默认规则，按话题讨论（F41）。
  stream(14, '规划业务交流', [1, 2, 4, 6], { topics_policy: 'inherit' }),
];

const DAY = 86400;
const todayStart = Math.floor(new Date(new Date().setHours(0, 0, 0, 0)).getTime() / 1000);
const at = (dayOffset: number, hour: number, minute: number) => todayStart + dayOffset * DAY + hour * 3600 + minute * 60;

const IMAGE_SVG = 'data:image/svg+xml,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="520" height="328" viewBox="0 0 520 328">'
  + '<rect width="520" height="328" fill="#dfe8f4"/><rect y="150" width="520" height="178" fill="#e9eef5"/>'
  + '<rect x="48" y="56" width="192" height="120" rx="8" fill="#f7f9fc" stroke="#c9d3e2"/>'
  + '<rect x="284" y="44" width="184" height="168" rx="8" fill="#f7f9fc" stroke="#c9d3e2"/></svg>',
);

function user(id: number): Omit<ZulipUser, 'is_active'> {
  const found = USERS.find((item) => item.user_id === id);
  if (!found) throw new Error(`devTransport: unknown user ${id}`);
  return found;
}

const p = (text: string) => `<p>${text}</p>`;
const mention = (id: number) => `<span class="user-mention" data-user-id="${id}">@${user(id).full_name}</span>`;
const upload = (path: string, name: string) => p(`<a href="/user_uploads/2/${path}/${encodeURIComponent(name)}">${name}</a>`);

function others(message: ZulipDirectMessage): number[] {
  const ids = message.display_recipient.map((item) => item.id).filter((id) => id !== SELF);
  return ids.length === 0 ? [] : ids;
}

function abortError(): DOMException {
  return new DOMException('The operation was aborted.', 'AbortError');
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);
}

/**
 * 每次创建都是一份独立的合成数据（每次挂载、每个身份代次各一份）：消息、草稿、上传、新建群都在这个
 * 实例里，旧实例的迟到回调写不进新实例。写操作产生最小的合成事件，界面按真实环境的方式由事件更新。
 */
export function createDevTransport(): ImTransport {
  let nextId = 2000;
  const messages: ZulipMessage[] = [];
  /** 合成消息的原文（编辑时取回）；初始数据的原文取渲染后的纯文本。 */
  const rawById = new Map<number, string>();
  const drafts: (ZulipDraft & { id: number })[] = [];
  let nextDraftId = 1;
  /** 开发期上传的图片只存在本页内存里，用 data: 地址显示。 */
  const uploadedImages = new Map<string, string>();
  let nextUpload = 1;
  let subscriptions: ZulipSubscription[] = [];
  /** 合成事件队列：写操作产生的事件排在这里，由 events() 取走。 */
  const queue: ZulipEvent[] = [];
  let nextEventId = 0;
  let wake: (() => void) | null = null;

  /** 写操作后排一个事件，让界面按真实环境的方式由事件更新。 */
  function emit(event: Omit<ZulipEvent, 'id'>): void {
    queue.push({ ...event, id: nextEventId++ } as ZulipEvent);
    wake?.();
  }

  function streamMessage(
    streamId: number, sender: number, timestamp: number, content: string, flags: string[] = ['read'], subject = '',
  ): void {
    const sub = subscriptions.find((item) => item.stream_id === streamId);
    const message: ZulipStreamMessage = {
      id: nextId++, type: 'stream', stream_id: streamId, display_recipient: sub?.name ?? '', subject,
      sender_id: sender, sender_full_name: user(sender).full_name, content, timestamp,
      flags: sender === SELF ? ['read'] : flags, reactions: [],
    };
    messages.push(message);
  }

  function directMessage(sender: number, to: number[], timestamp: number, content: string, flags: string[] = ['read']): void {
    const ids = [...new Set([sender, ...to])].sort((a, b) => a - b);
    const message: ZulipDirectMessage = {
      id: nextId++, type: 'private', subject: '',
      display_recipient: ids.map((id) => ({ id, full_name: user(id).full_name, email: user(id).email })),
      sender_id: sender, sender_full_name: user(sender).full_name, content, timestamp,
      flags: sender === SELF ? ['read'] : flags, reactions: [],
    };
    messages.push(message);
  }

  /** 合成数据的初始内容。 */
  function seed(): void {
    subscriptions = structuredClone(SEED_SUBSCRIPTIONS);
    streamMessage(10, 6, at(-1, 16, 5), p('专项资金使用方案的初稿已经放到群文件了，各位先看一下，明天上午碰一次。'));
    streamMessage(10, 5, at(-1, 16, 12), p('好的，我负责的第二部分今晚整理完。'));
    streamMessage(10, SELF, at(-1, 16, 20), p('配套资金那一块我来核，明早给意见。'));
    streamMessage(10, 2, at(-1, 16, 30), p('辛苦大家，明天 9 点前把意见发群里。'));
    streamMessage(10, 2, at(0, 9, 12), p('各位，城市更新专项资金使用方案下周三上会，今天请把各自负责部分的意见反馈一下。'));
    messages[messages.length - 1]!.reactions = [
      { emoji_name: '+1', emoji_code: '1f44d', reaction_type: 'unicode_emoji', user_id: 3 },
      { emoji_name: '+1', emoji_code: '1f44d', reaction_type: 'unicode_emoji', user_id: 4 },
      { emoji_name: '+1', emoji_code: '1f44d', reaction_type: 'unicode_emoji', user_id: SELF },
    ];
    streamMessage(10, 3, at(0, 9, 20), upload('ab/c1', '城市更新项目资金明细（第三版）.xlsx'));
    streamMessage(10, SELF, at(0, 9, 26),
      '<blockquote><p>王芳：城市更新项目资金明细（第三版）.xlsx</p></blockquote>'
      + p('第 12 行的配套资金和上次会议口径不一致，我标出来了，请再核一下。'));
    streamMessage(10, 4, at(0, 10, 2),
      `<div class="message_inline_image"><a href="${IMAGE_SVG}" title="现场照片.png"><img src="${IMAGE_SVG}"></a></div>`);
    streamMessage(10, 5, at(0, 10, 36),
      p(`${mention(SELF)} 第三版材料已按你的意见改好上传了，麻烦今天下班前确认一下，我这边好提交会签。`),
      ['mentioned']);

    // 更早的群聊记录，用来看滚动、新消息提示与搜索定位。
    const EARLIER = [
      '第一次碰头会定在周二上午。', '我先把去年同类项目的资金明细找出来。', '配套资金口径以财政局的批复为准。',
      '附件里第 3 页的数字和汇总表对不上，麻烦再核一下。', '收到，我下午改。', '会签单需要各处室负责人签字。',
      '预算部分我这边已经初审过了。', '城市更新专项的进度表周四前更新。', '请大家按模板填，别改表头。',
      '规划处的意见已经汇总进来了。', '财务处这边没有新的意见。', '下周三上会的议程我来准备。',
    ];
    EARLIER.forEach((text, index) => {
      const sender = [2, 3, 4, 5, 6, SELF][index % 6]!;
      streamMessage(10, sender, at(-5 + Math.floor(index / 4), 9 + (index % 4) * 2, 10 + index), p(text));
    });

    directMessage(2, [SELF], at(-1, 17, 40), p('明天上会的材料我先过一遍，有问题再找你。'));
    directMessage(SELF, [2], at(-1, 17, 42), p('好的，辛苦。'));
    directMessage(2, [SELF], at(0, 10, 18), upload('de/f2', '会签意见汇总表（初稿）.docx'), []);
    directMessage(2, [SELF], at(0, 10, 24), p('好的，我下午把会签意见汇总一下，晚点发你确认。'), []);

    streamMessage(11, 8, at(0, 9, 58), p(`${mention(SELF)} 明天上午的检查路线已经定了，八点半在一楼集合。`), ['mentioned']);
    directMessage(4, [SELF], at(0, 9, 41), p('关于第三季度信息报送的口径，你那边有最新的通知吗？'));
    streamMessage(12, 6, at(0, 9, 30), p('本周五下午例会改到三楼会议室。'), []);
    directMessage(3, [SELF], at(-1, 15, 2), upload('gh/i3', '2026年第四季度经费预算表.xlsx'));
    directMessage(9, [SELF], at(-3, 11, 0), p('已为你整理好会议纪要初稿，等你确认。'));
    streamMessage(13, 3, at(-3, 14, 10), p('报销单已退回，请补充发票原件。'), []);
    streamMessage(14, 2, at(-2, 10, 5), p('控规调整的公示稿下周一挂网，请各自核对负责的地块。'), ['read'], '控规调整');
    streamMessage(14, 4, at(-2, 10, 20), p('第 3、7 号地块的容积率我已经核过了。'), ['read'], '控规调整');
    streamMessage(14, 6, at(-1, 14, 0), p('下周二的业务培训改在二楼小会议室。'), ['read'], '业务培训');
    streamMessage(14, 2, at(-1, 15, 30), p('培训材料周一前发群里。'), ['read'], '业务培训');
    directMessage(7, [SELF], at(-3, 16, 45), p('收到，谢谢！'));
    directMessage(SELF, [5, 6], at(-4, 15, 30), p('周五的材料分工我整理好了，你们看下有没有遗漏。'));

    // 真实 Zulip 的消息 id 随时间单调递增；合成数据按时间排好后重新编号。
    messages.sort((a, b) => a.timestamp - b.timestamp);
    messages.forEach((message, index) => { message.id = 1000 + index; });
    nextId = 1000 + messages.length;
  }

  function snapshot(): ZulipRegisterResponse {
    const unread = messages.filter((message) => !message.flags.includes('read'));
    const recent = new Map<string, { max_message_id: number; user_ids: number[] }>();
    for (const message of messages) {
      if (message.type !== 'private') continue;
      const ids = others(message);
      const key = ids.join(',');
      recent.set(key, { max_message_id: Math.max(recent.get(key)?.max_message_id ?? 0, message.id), user_ids: ids });
    }
    const pms = new Map<number, number[]>();
    const huddles = new Map<string, number[]>();
    const streams = new Map<number, number[]>();
    for (const message of unread) {
      if (message.type === 'stream') {
        streams.set(message.stream_id, [...(streams.get(message.stream_id) ?? []), message.id]);
      } else if (others(message).length === 1) {
        pms.set(message.sender_id, [...(pms.get(message.sender_id) ?? []), message.id]);
      } else {
        const key = message.display_recipient.map((item) => item.id).sort((a, b) => a - b).join(',');
        huddles.set(key, [...(huddles.get(key) ?? []), message.id]);
      }
    }
    const now = Math.floor(Date.now() / 1000);
    return {
      queue_id: 'dev-queue', last_event_id: -1, user_id: SELF, event_queue_longpoll_timeout_seconds: 90,
      max_file_upload_size_mib: 25, max_message_length: 10000, realm_users: USERS,
      custom_profile_fields: [
        { id: DEPT_FIELD, name: '部门', type: 1, order: 1 },
        { id: TITLE_FIELD, name: '职务', type: 1, order: 2 },
      ],
      subscriptions: structuredClone(subscriptions),
      recent_private_conversations: [...recent.values()].sort((a, b) => b.max_message_id - a.max_message_id),
      unread_msgs: {
        pms: [...pms].map(([other_user_id, unread_message_ids]) => ({ other_user_id, unread_message_ids })),
        streams: [...streams].map(([stream_id, unread_message_ids]) => ({ stream_id, topic: '', unread_message_ids })),
        huddles: [...huddles].map(([user_ids_string, unread_message_ids]) => ({ user_ids_string, unread_message_ids })),
        mentions: unread.filter((message) => message.flags.includes('mentioned')).map((message) => message.id),
      },
      presences: {
        2: { active_timestamp: now }, 5: { active_timestamp: now - 30 }, 7: { active_timestamp: now },
        4: { idle_timestamp: now - 60, active_timestamp: now - 900 }, 8: { idle_timestamp: now - 120 },
      },
      server_presence_offline_threshold_seconds: 140,
      server_timestamp: now,
      enter_sends: true,
      realm_emoji: {},
      drafts: drafts.map((draft) => ({ ...draft })),
    };
  }

  function find(messageId: number): ZulipMessage {
    const message = messages.find((item) => item.id === messageId);
    if (!message) throw new ApiError(400, 'im_upstream:BAD_REQUEST', '消息不存在。');
    return message;
  }

  /**
   * 极简的 Markdown 渲染，只覆盖输入框会产生的写法：段落与换行、`> ` 引用、`@**姓名|id**` 提及、
   * `[名称](/user_uploads/...)` 附件链接（图片显示为内联图）、`**粗体**` 与行内代码。
   */
  function render(markdown: string): string {
    const inline = (text: string) => escapeHtml(text)
      .replace(/@\*\*([^|*]+)\|(\d+)\*\*/g, (_match, name: string, id: string) => `<span class="user-mention" data-user-id="${id}">@${name}</span>`)
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
    const blocks: string[] = [];
    for (const block of markdown.trim().split(/\n{2,}/)) {
      // 连续的 `>` 行合成一个引用块，其余行按段落处理。
      let quote: string[] = [];
      let text: string[] = [];
      const flushText = () => { if (text.length > 0) blocks.push(p(text.map(inline).join('<br>'))); text = []; };
      const flushQuote = () => {
        if (quote.length > 0) blocks.push(`<blockquote>${p(quote.map(inline).join('<br>'))}</blockquote>`);
        quote = [];
      };
      for (const line of block.split('\n')) {
        if (line.startsWith('>')) {
          flushText();
          quote.push(line.replace(/^>\s?/, ''));
          continue;
        }
        flushQuote();
        const upload = /^\[([^\]]+)\]\((\/user_uploads\/[^)]+)\)$/.exec(line.trim());
        if (!upload) {
          text.push(line);
          continue;
        }
        flushText();
        const name = upload[1]!;
        const url = upload[2]!;
        const image = uploadedImages.get(url);
        blocks.push(image
          ? `<div class="message_inline_image"><a href="${image}" title="${escapeHtml(name)}"><img src="${image}"></a></div>`
          : p(`<a href="${url}">${escapeHtml(name)}</a>`));
      }
      flushQuote();
      flushText();
    }
    return blocks.join('');
  }

  seed();
  return {
    async identity() {
      return { provider: 'zulip', im_instance_id: 'dev', realm_id: 'dev', im_user_id: String(SELF) };
    },
    async register() {
      return structuredClone(snapshot());
    },
    events(_queueId, lastEventId, signal) {
      return new Promise((resolve, reject) => {
        const deliver = () => {
          const ready = queue.filter((event) => event.id > lastEventId);
          if (ready.length === 0) return false;
          wake = null;
          resolve({ events: structuredClone(ready) });
          return true;
        };
        if (signal.aborted) {
          reject(abortError());
          return;
        }
        if (deliver()) return;
        wake = () => {
          deliver();
        };
        signal.addEventListener('abort', () => {
          wake = null;
          reject(abortError());
        }, { once: true });
      });
    },
    async deleteQueue() {},
    async reportPresence() {},
    async streamMembers(streamId) {
      return subscriptions.find((item) => item.stream_id === streamId)?.subscribers ?? [];
    },
    async setSubscriptionProperty(streamId, property, value) {
      const sub = subscriptions.find((item) => item.stream_id === streamId);
      if (sub) sub[property] = value;
      emit({ type: 'subscription', op: 'update', stream_id: streamId, property, value });
    },
    async addMembers(streamName, userIds) {
      const sub = subscriptions.find((item) => item.name === streamName);
      if (!sub) return;
      sub.subscribers = [...new Set([...(sub.subscribers ?? []), ...userIds])];
      emit({ type: 'subscription', op: 'peer_add', stream_ids: [sub.stream_id], user_ids: userIds });
    },
    async leaveStream(streamName) {
      const sub = subscriptions.find((item) => item.name === streamName);
      if (!sub) return;
      subscriptions = subscriptions.filter((item) => item !== sub);
      emit({ type: 'subscription', op: 'remove', subscriptions: [{ stream_id: sub.stream_id, name: sub.name }] });
    },
    async userChannels(userId) {
      return subscriptions.filter((item) => item.subscribers?.includes(userId)).map((item) => item.stream_id);
    },
    async streamName(streamId) {
      return subscriptions.find((item) => item.stream_id === streamId)?.name ?? `群 ${streamId}`;
    },
    async userGroups() {
      return [{ id: 1, name: '规划处', description: '合成用户组', members: [2, 3], is_system_group: false }];
    },
    async streamTopics(streamId) {
      const topics = new Map<string, number>();
      for (const message of messages) {
        if (message.type === 'stream' && message.stream_id === streamId) {
          topics.set(message.subject, Math.max(topics.get(message.subject) ?? 0, message.id));
        }
      }
      return [...topics].map(([name, max_id]) => ({ name, max_id })).sort((a, b) => b.max_id - a.max_id);
    },
    async messages(query) {
      const matchTerm = (message: ZulipMessage, term: ZulipNarrowTerm): boolean => {
        if (term.operator === 'channel') return message.type === 'stream' && message.stream_id === term.operand;
        if (term.operator === 'dm') {
          if (message.type !== 'private') return false;
          const ids = others(message);
          const want = term.operand.filter((id) => id !== SELF);
          return ids.length === want.length && want.every((id) => ids.includes(id));
        }
        if (term.operator === 'search') {
          const text = new DOMParser().parseFromString(message.content, 'text/html').body.textContent ?? '';
          return text.toLowerCase().includes(term.operand.toLowerCase());
        }
        if (term.operator === 'has') return term.operand === 'attachment' && message.content.includes('/user_uploads/');
        if (term.operator === 'topic') return message.type === 'stream' && message.subject === term.operand;
        return false;
      };
      const matched = messages.filter((message) => query.narrow.every((term) => matchTerm(message, term)));
      let index = matched.length - 1;
      if (typeof query.anchor === 'number') {
        const anchor = query.anchor;
        index = matched.findIndex((message) => message.id >= anchor);
        if (index < 0) index = matched.length - 1;
      }
      const start = Math.max(0, index - query.numBefore);
      const end = Math.min(matched.length, index + query.numAfter + 1);
      return {
        messages: structuredClone(matched.slice(start, end)),
        found_newest: end >= matched.length, found_oldest: start === 0,
        found_anchor: typeof query.anchor === 'number' && matched[index]?.id === query.anchor,
      };
    },
    async send({ target, content }) {
      const timestamp = Math.floor(Date.now() / 1000);
      if (target.type === 'stream') streamMessage(target.streamId, SELF, timestamp, render(content), ['read'], target.topic);
      else directMessage(SELF, target.userIds, timestamp, render(content));
      const message = messages[messages.length - 1]!;
      rawById.set(message.id, content);
      emit({ type: 'message', message: structuredClone(message), flags: ['read'] });
      return { id: message.id };
    },
    async rawContents(messageIds) {
      return messageIds.map((id) => {
        const message = find(id);
        return {
          ...structuredClone(message),
          content: rawById.get(id) ?? new DOMParser().parseFromString(message.content, 'text/html').body.textContent ?? '',
        };
      });
    },
    async rawContent(messageId) {
      const message = find(messageId);
      return rawById.get(messageId) ?? new DOMParser().parseFromString(message.content, 'text/html').body.textContent ?? '';
    },
    async editMessage(messageId, content) {
      const message = find(messageId);
      if (message.sender_id !== SELF) throw new ApiError(400, 'im_upstream:BAD_REQUEST', '只能编辑自己的消息。');
      message.content = render(content);
      message.last_edit_timestamp = Math.floor(Date.now() / 1000);
      rawById.set(messageId, content);
      emit({
        type: 'update_message', message_id: messageId, message_ids: [messageId], rendered_content: message.content,
        edit_timestamp: message.last_edit_timestamp, user_id: SELF,
      });
    },
    async deleteMessage(messageId) {
      const index = messages.findIndex((message) => message.id === messageId);
      if (index < 0) throw new ApiError(400, 'im_upstream:BAD_REQUEST', '消息不存在。');
      const [removed] = messages.splice(index, 1);
      emit({ type: 'delete_message', message_id: messageId, message_type: removed!.type });
    },
    async react(messageId, emoji, add) {
      const message = find(messageId);
      const mine = (item: ZulipMessage['reactions'][number]) => item.user_id === SELF && item.emoji_code === emoji.code;
      emit({
        type: 'reaction', op: add ? 'add' : 'remove', message_id: messageId, emoji_name: emoji.name,
        emoji_code: emoji.code, reaction_type: emoji.type, user_id: SELF,
      });
      if (add && !message.reactions.some(mine)) {
        message.reactions.push({ emoji_name: emoji.name, emoji_code: emoji.code, reaction_type: emoji.type, user_id: SELF });
      } else if (!add) {
        message.reactions = message.reactions.filter((item) => !mine(item));
      }
    },
    async setFlag(messageIds, flag, add) {
      for (const id of messageIds) {
        const message = messages.find((item) => item.id === id);
        if (!message) continue;
        message.flags = add ? [...new Set([...message.flags, flag])] : message.flags.filter((item) => item !== flag);
      }
      const details = Object.fromEntries(messageIds.map((id) => {
        const message = messages.find((item) => item.id === id);
        if (!message) return [id, {}];
        return [id, message.type === 'stream'
          ? { type: 'stream', stream_id: message.stream_id }
          : { type: 'private', user_ids: others(message) }];
      }));
      emit({
        type: 'update_message_flags', op: add ? 'add' : 'remove', flag, messages: messageIds, all: false,
        message_details: details,
      });
    },
    async createDraft(draft) {
      const id = nextDraftId++;
      drafts.push({ ...draft, id });
      emit({ type: 'drafts', op: 'add', drafts: [{ ...draft, id }] });
      return id;
    },
    async editDraft(draftId, draft) {
      const index = drafts.findIndex((item) => item.id === draftId);
      if (index < 0) throw new ApiError(404, 'im_upstream:BAD_REQUEST', '草稿不存在。');
      drafts[index] = { ...draft, id: draftId };
      emit({ type: 'drafts', op: 'update', draft: { ...draft, id: draftId } });
    },
    async deleteDraft(draftId) {
      const index = drafts.findIndex((item) => item.id === draftId);
      if (index >= 0) drafts.splice(index, 1);
      emit({ type: 'drafts', op: 'remove', draft_id: draftId });
    },
    async createGroup(name, userIds) {
      const id = Math.max(...subscriptions.map((item) => item.stream_id)) + 1;
      const sub = stream(id, name, [...new Set([SELF, ...userIds])]);
      subscriptions.push(sub);
      emit({ type: 'subscription', op: 'add', subscriptions: [structuredClone(sub)] });
      return { id };
    },
    upload(file, onProgress, signal) {
      return new Promise((resolve, reject) => {
        if (signal.aborted) {
          reject(abortError());
          return;
        }
        let reader: FileReader | null = null;
        let ratio = 0;
        const timer = window.setInterval(() => {
          ratio = Math.min(1, ratio + 0.25);
          onProgress(ratio);
          if (ratio < 1) return;
          window.clearInterval(timer);
          const url = `/user_uploads/2/dev/${nextUpload++}/${encodeURIComponent(file.name)}`;
          if (!file.type.startsWith('image/')) {
            resolve({ url, filename: file.name });
            return;
          }
          const current = new FileReader();
          reader = current;
          current.onload = () => {
            if (signal.aborted) return;
            uploadedImages.set(url, String(current.result));
            resolve({ url, filename: file.name });
          };
          current.onerror = () => reject(new ApiError(400, 'im_upstream:BAD_REQUEST', '读取文件失败。'));
          current.readAsDataURL(file);
        }, 150);
        signal.addEventListener('abort', () => {
          window.clearInterval(timer);
          reader?.abort();
          reject(abortError());
        }, { once: true });
      });
    },
  };
}
