import type { ImTransport } from './transport';
import type {
  ZulipDirectMessage, ZulipMessage, ZulipRegisterResponse, ZulipStreamMessage, ZulipSubscription, ZulipUser,
} from './types';

/**
 * 开发期合成数据（属于 Mock，FE_PLAN §4.2）。只由 `import.meta.env.DEV` 下的动态 import 加载，
 * 不进生产包；不模拟事件队列语义，只给当前界面开发用，不算真实接通，也不算业务验收。
 * 真实接口出错时不能切到这里。全部人名、单位、内容均为合成数据。
 */

const SELF = 1;
const DEPT_FIELD = 1;
const TITLE_FIELD = 2;

function person(id: number, name: string, dept: string, title = ''): ZulipUser {
  const profile_data: ZulipUser['profile_data'] = { [DEPT_FIELD]: { value: dept } };
  if (title) profile_data[TITLE_FIELD] = { value: title };
  return {
    user_id: id, full_name: name, email: `user${id}@example.invalid`, avatar_url: null, is_active: true,
    is_bot: false, profile_data,
  };
}

const USERS: ZulipUser[] = [
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
    color: '#76ce90', subscribers, ...extra,
  };
}

const SUBSCRIPTIONS: ZulipSubscription[] = [
  stream(10, '城市更新专项工作组', [1, 2, 3, 4, 5, 6, 7, 8], { description: '专项资金使用方案与会签材料' }),
  stream(11, '安全生产检查小组', [1, 4, 8]),
  stream(12, '办公室全体', [1, 2, 3, 4, 5, 6, 7, 8], { is_muted: true }),
  stream(13, '财务审核群', [1, 3, 5], { is_muted: true }),
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

let nextId = 2000;
const messages: ZulipMessage[] = [];

function user(id: number): ZulipUser {
  const found = USERS.find((item) => item.user_id === id);
  if (!found) throw new Error(`devTransport: unknown user ${id}`);
  return found;
}

function streamMessage(streamId: number, sender: number, timestamp: number, content: string, flags: string[] = ['read']): void {
  const sub = SUBSCRIPTIONS.find((item) => item.stream_id === streamId);
  const message: ZulipStreamMessage = {
    id: nextId++, type: 'stream', stream_id: streamId, display_recipient: sub?.name ?? '', subject: '',
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

const p = (text: string) => `<p>${text}</p>`;
const mention = (id: number) => `<span class="user-mention" data-user-id="${id}">@${user(id).full_name}</span>`;
const upload = (path: string, name: string) => p(`<a href="/user_uploads/2/${path}/${encodeURIComponent(name)}">${name}</a>`);

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
directMessage(7, [SELF], at(-3, 16, 45), p('收到，谢谢！'));
directMessage(SELF, [5, 6], at(-4, 15, 30), p('周五的材料分工我整理好了，你们看下有没有遗漏。'));

// 真实 Zulip 的消息 id 随时间单调递增；合成数据按时间排好后重新编号。
messages.sort((a, b) => a.timestamp - b.timestamp);
messages.forEach((message, index) => { message.id = 1000 + index; });
nextId = 1000 + messages.length;

function others(message: ZulipDirectMessage): number[] {
  const ids = message.display_recipient.map((item) => item.id).filter((id) => id !== SELF);
  return ids.length === 0 ? [] : ids;
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
    max_file_upload_size_mib: 25, realm_users: USERS,
    custom_profile_fields: [
      { id: DEPT_FIELD, name: '部门', type: 1, order: 1 },
      { id: TITLE_FIELD, name: '职务', type: 1, order: 2 },
    ],
    subscriptions: SUBSCRIPTIONS,
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
    presence_offline_threshold_seconds: 140,
    server_timestamp: now,
    enter_sends: true,
  };
}

function abortError(): DOMException {
  return new DOMException('The operation was aborted.', 'AbortError');
}

export function createDevTransport(): ImTransport {
  return {
    async identity() {
      return { provider: 'zulip', im_instance_id: 'dev', realm_id: 'dev', im_user_id: String(SELF) };
    },
    async register() {
      return structuredClone(snapshot());
    },
    events(_queueId, _lastEventId, signal) {
      return new Promise((_resolve, reject) => {
        if (signal.aborted) reject(abortError());
        signal.addEventListener('abort', () => reject(abortError()), { once: true });
      });
    },
    async deleteQueue() {},
    async messages(query) {
      const [term] = query.narrow;
      const matched = messages.filter((message) => {
        if (!term) return true;
        if (term.operator === 'channel') return message.type === 'stream' && message.stream_id === term.operand;
        if (term.operator === 'dm') {
          if (message.type !== 'private') return false;
          const ids = others(message);
          const want = term.operand.filter((id) => id !== SELF);
          return ids.length === want.length && want.every((id) => ids.includes(id));
        }
        return false;
      });
      return {
        messages: structuredClone(matched.slice(-query.numBefore - 1)),
        found_newest: true, found_oldest: matched.length <= query.numBefore + 1, found_anchor: false,
      };
    },
    async send({ target, content }) {
      const html = content.split(/\n{2,}/).map((part) => p(escapeHtml(part).replace(/\n/g, '<br>'))).join('');
      const timestamp = Math.floor(Date.now() / 1000);
      if (target.type === 'stream') streamMessage(target.streamId, SELF, timestamp, html);
      else directMessage(SELF, target.userIds, timestamp, html);
      return { id: messages[messages.length - 1]!.id };
    },
  };
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => `&#${char.charCodeAt(0)};`);
}
