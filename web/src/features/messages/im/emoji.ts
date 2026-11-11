import type { ZulipEmojiRef } from '../zulip/types';

/**
 * 常用 Unicode 表情。`name` 必须是 Zulip 12.3 表情表里的名字，表态时与 `code` 一起提交
 * （名字与码点不匹配会被拒绝）；输入框里直接插入字符本身，由服务端渲染。
 */
export interface UnicodeEmoji {
  name: string;
  code: string;
}

export const COMMON_EMOJI: UnicodeEmoji[] = [
  { name: '+1', code: '1f44d' }, { name: 'heart', code: '2764' }, { name: 'smile', code: '1f604' },
  { name: 'tada', code: '1f389' }, { name: 'clap', code: '1f44f' }, { name: 'pray', code: '1f64f' },
  { name: 'joy', code: '1f602' }, { name: 'grinning', code: '1f600' }, { name: 'slight_smile', code: '1f642' },
  { name: 'thinking', code: '1f914' }, { name: 'sweat_smile', code: '1f605' }, { name: 'muscle', code: '1f4aa' },
  { name: 'fire', code: '1f525' }, { name: 'rocket', code: '1f680' }, { name: 'star', code: '2b50' },
  { name: 'coffee', code: '2615' }, { name: 'rose', code: '1f339' }, { name: '-1', code: '1f44e' },
];

/** 右键菜单顶部的快捷表态。 */
export const QUICK_REACTIONS = COMMON_EMOJI.slice(0, 6);

export function emojiChar(code: string): string {
  return String.fromCodePoint(...code.split('-').map((part) => parseInt(part, 16)));
}

export function unicodeEmojiRef(emoji: UnicodeEmoji): ZulipEmojiRef {
  return { name: emoji.name, code: emoji.code, type: 'unicode_emoji' };
}
