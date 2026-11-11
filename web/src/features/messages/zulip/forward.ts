/**
 * 转发正文的拼法（FE_PLAN §11）。Zulip 没有独立的「转发」对象，惯例是把原文引用进一条普通新消息
 * （12.3 `web/src/compose_reply.ts::quote_messages`）。这里沿用：
 *
 * - 原文用 ```quote 围栏引用，围栏长度比原文里最长的反引号串多一位，原文里的代码块不会破坏边界。
 * - 原文里的 @提及改成静默提及（`@_**…**`、`@_*…*`），转发不再重复通知被提到的人。
 * - 附件保留原始 `/user_uploads/...` 链接：发出后由 Zulip 关联到新消息，收件人按新消息的权限读取。
 */

export interface ForwardSource {
  senderName: string;
  /** 已格式化好的时间文字。 */
  time: string;
  /** 编辑时取回的原始 Markdown。 */
  raw: string;
}

export const MERGED_TITLE = '聊天记录';

function escapeMarkdown(text: string): string {
  return text.replace(/([\\*_`[\]])/g, '\\$1');
}

function silenceMentions(raw: string): string {
  return raw.replace(/@(?!_)(\*\*?)/g, '@_$1');
}

function quoted(raw: string): string {
  const longest = Math.max(2, ...(raw.match(/`+/g) ?? []).map((run) => run.length));
  const fence = '`'.repeat(longest + 1);
  return `${fence}quote\n${silenceMentions(raw)}\n${fence}`;
}

function heading(source: ForwardSource): string {
  return `**${escapeMarkdown(source.senderName)}** ${source.time}`;
}

/** 逐条转发：每条一条消息，首行注明原发送人与时间。 */
export function singleForward(source: ForwardSource): string {
  return `转发自 ${heading(source)}\n${quoted(source.raw)}`;
}

/** 合并转发：一条消息，固定标题行开头（前端据此折叠成记录卡），之后按原顺序逐条引用。 */
export function mergedForward(conversationTitle: string, sources: ForwardSource[]): string {
  const title = `**${MERGED_TITLE}**（${sources.length} 条，来自「${escapeMarkdown(conversationTitle)}」）`;
  return [title, ...sources.map((source) => `${heading(source)}\n${quoted(source.raw)}`)].join('\n\n');
}

/**
 * 发送前的长度：按服务端规则去掉首尾空白后，以 Unicode 码点计数（Python 字符数，不是 UTF-16 单元）。
 * 超过 register 返回的 `max_message_length` 时服务端会截断，所以必须在发送前拦下。
 */
export function messageLength(content: string): number {
  return Array.from(content.trim()).length;
}

/** 服务端渲染后的合并转发正文以标题行开头。 */
export function isMergedRecord(html: string): boolean {
  return html.startsWith(`<p><strong>${MERGED_TITLE}</strong>（`);
}
