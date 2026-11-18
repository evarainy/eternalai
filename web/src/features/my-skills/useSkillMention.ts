/** `$` 指定调用：在输入框里用 `$`（或全角 `＄`）选本人可用的功能。
 *
 * 选中时记下功能 id 和它在草稿里的那一段文字（结构化绑定，随编辑移动；那段文字被改掉即解除），
 * 发送时只把 id 交给服务端，由服务端按本人当前版本直接执行；不写 `$` 时照旧由模型判断。
 * 手输完整的 `$功能名` 也能绑定：最长的完整名称优先；单独的 `$100` 这类金额按普通文字发送。
 * 中文输入法组字期间不弹、不选、不发送。换账号即清空。 */

import {
  useCallback, useEffect, useMemo, useRef, useState, type CompositionEvent, type KeyboardEvent,
  type RefObject,
} from 'react';
import type { SkillListItem } from '../../api/userSkills';
import { useAuthStore } from '../../stores/authStore';
import { useMySkills } from './useMySkills';

interface Binding {
  skillId: string;
  name: string;
  /** Where `$name` starts in the draft. */
  start: number;
  generation: number;
}

export interface SkillBinding {
  skillId: string;
  name: string;
}

export type MentionResolution =
  | { kind: 'none' }
  | { kind: 'ref'; skillId: string }
  | { kind: 'blocked'; reason: string };

interface Token {
  /** The whole `$word` under the caret. */
  start: number;
  end: number;
  /** What was typed after `$` up to the caret: the filter. */
  text: string;
}

const MAX_CANDIDATES = 8;
const TRIGGERS = new Set(['$', '＄']);
const DIGIT = /^[\d.,]/;
const STANDALONE_AMOUNT = /^[\d.,]+(?=\s|$)/;

function norm(text: string): string {
  return text.normalize('NFKC').replace(/\s+/g, '').toLowerCase();
}

function fold(text: string): string {
  return text.normalize('NFKC').toLowerCase();
}

const isSpace = (char: string | undefined) => char !== undefined && /\s/.test(char);
const endsWord = (char: string | undefined) => char === undefined || /[\s\p{P}]/u.test(char);

function tokenAt(value: string, caret: number): Token | null {
  let start = caret;
  while (start > 0 && !isSpace(value[start - 1])) start -= 1;
  let end = caret;
  while (end < value.length && !isSpace(value[end])) end += 1;
  if (!TRIGGERS.has(value[start] ?? '')) return null;
  if (DIGIT.test(value.slice(start + 1, end))) return null;
  return { start, end, text: value.slice(start + 1, caret) };
}

function rank(items: readonly SkillListItem[], text: string): SkillListItem[] {
  const query = norm(text);
  const scored = items.flatMap((item) => {
    const name = norm(item.name);
    const score = name === query ? 0 : name.startsWith(query) ? 1 : name.includes(query) ? 2
      : norm(item.purpose).includes(query) ? 3 : -1;
    return score < 0 ? [] : [{ item, score }];
  });
  scored.sort((a, b) => a.score - b.score || a.item.name.localeCompare(b.item.name, 'zh'));
  return scored.slice(0, MAX_CANDIDATES).map(({ item }) => item);
}

const mentionText = (name: string) => `$${name}`;

/** Where the binding's `$name` is now: its old place, else its only standalone place. */
function locate(value: string, binding: Binding): number | null {
  const text = mentionText(binding.name);
  const standalone = (at: number) => value.startsWith(text, at)
    && (at === 0 || isSpace(value[at - 1])) && endsWord(value[at + text.length]);
  if (standalone(binding.start)) return binding.start;
  const found: number[] = [];
  for (let at = value.indexOf(text); at >= 0; at = value.indexOf(text, at + 1)) {
    if (standalone(at)) found.push(at);
  }
  return found.length === 1 ? found[0]! : null;
}

/** ``value`` without ``length`` chars at ``start`` (and one space after them). */
function cut(value: string, start: number, length: number): string {
  const end = start + length;
  return value.slice(0, start) + value.slice(value[end] === ' ' ? end + 1 : end);
}

/** A typed `$name` at the start of the message: the longest complete usable name wins. */
function resolveTyped(message: string, items: readonly SkillListItem[] | undefined):
  MentionResolution {
  const text = message.trimStart();
  if (!TRIGGERS.has(text[0] ?? '')) return { kind: 'none' };
  const rest = text.slice(1);
  if (!rest.trim()) return { kind: 'none' };
  const amount = STANDALONE_AMOUNT.test(rest);
  if (items === undefined) {
    // Only a bare amount may go without the list; a name must be checked first.
    return amount ? { kind: 'none' }
      : { kind: 'blocked', reason: '正在读取我的功能，请稍后再发送。' };
  }
  const folded = fold(rest);
  const usable = items.filter((item) => item.state === 'usable');
  const matches = usable.filter((item) => {
    const name = fold(item.name);
    return folded.startsWith(name) && endsWord(folded[name.length]);
  });
  if (matches.length === 0) {
    if (amount) return { kind: 'none' };
    const word = rest.split(/\s/)[0] ?? rest;
    return { kind: 'blocked', reason: `没有找到名为「${word}」的可用功能，请从列表中选择。` };
  }
  const longest = Math.max(...matches.map((item) => fold(item.name).length));
  const top = matches.filter((item) => fold(item.name).length === longest);
  const same = usable.filter((item) => norm(item.name) === norm(top[0]!.name));
  if (top.length > 1 || same.length > 1) {
    return { kind: 'blocked', reason: '有多个同名的功能，请从列表中选择。' };
  }
  return { kind: 'ref', skillId: top[0]!.skill_id };
}

export function useSkillMention({ draft, setDraft, inputRef, enabled, onBind }: {
  draft: string;
  setDraft: (value: string) => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  enabled: boolean;
  /** Called when a flow is bound (to clear a conflicting browser-skill choice). */
  onBind?: () => void;
}) {
  const generation = useAuthStore((state) => state.generation);
  // Read the list only once the user starts a `$` mention (or the panel opened it).
  const [wanted, setWanted] = useState(false);
  const skills = useMySkills(enabled && wanted);
  const items = skills.data?.items;
  const usable = useMemo(() => (items ?? []).filter((item) => item.state === 'usable'), [items]);
  const [token, setToken] = useState<Token | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [stored, setBinding] = useState<Binding | null>(null);
  const composing = useRef(false);
  // Another account's choice is never shown or sent.
  const binding = stored !== null && stored.generation === generation ? stored : null;
  useEffect(() => {
    setBinding(null);
    setToken(null);
  }, [generation]);

  const candidates = useMemo(() => (token === null ? [] : rank(usable, token.text)),
    [token, usable]);
  const active = candidates.find((item) => item.skill_id === activeId) ?? candidates[0] ?? null;
  const open = enabled && token !== null;

  const focusAt = useCallback((position: number) => {
    requestAnimationFrame(() => {
      const element = inputRef.current;
      if (element === null) return;
      element.focus();
      element.setSelectionRange(position, position);
    });
  }, [inputRef]);

  /** After every edit or caret move: follow or drop the binding, re-read the token. */
  const sync = useCallback((value: string, caret: number | null) => {
    setBinding((current) => {
      if (current === null) return null;
      const at = locate(value, current);
      return at === null ? null : at === current.start ? current : { ...current, start: at };
    });
    if (composing.current) return;
    const next = caret === null ? null : tokenAt(value, caret);
    if (next !== null) setWanted(true);
    setToken(next);
  }, []);

  const bindAt = useCallback((item: SkillListItem, start: number) => {
    setBinding({ skillId: item.skill_id, name: item.name, start, generation });
    setToken(null);
    onBind?.();
  }, [generation, onBind]);

  /** The draft without the current binding's text, and where ``position`` moves to. */
  const unbound = useCallback((value: string, position: number): [string, number] => {
    if (binding === null) return [value, position];
    const at = locate(value, binding);
    if (at === null) return [value, position];
    const next = cut(value, at, mentionText(binding.name).length);
    return [next, at < position ? position - (value.length - next.length) : position];
  }, [binding]);

  const select = useCallback((item: SkillListItem) => {
    if (token === null) return;
    const inserted = `${mentionText(item.name)} `;
    const replaced = draft.slice(0, token.start) + inserted + draft.slice(token.end).trimStart();
    // One flow per message: an earlier choice elsewhere is removed.
    const [next, start] = binding !== null && locate(draft, binding) !== token.start
      ? unbound(replaced, token.start) : [replaced, token.start];
    setDraft(next);
    bindAt(item, start);
    focusAt(start + inserted.length);
  }, [token, draft, binding, unbound, setDraft, bindAt, focusAt]);

  /** From the panel: the chosen flow goes to the start of the message. */
  const insert = useCallback((item: SkillListItem) => {
    const [rest] = unbound(draft, 0);
    const next = `${mentionText(item.name)} ${rest.trimStart()}`;
    setDraft(next);
    bindAt(item, 0);
    focusAt(next.length);
  }, [draft, unbound, setDraft, bindAt, focusAt]);

  const clear = useCallback(() => {
    setDraft(unbound(draft, 0)[0]);
    setBinding(null);
    setToken(null);
  }, [draft, unbound, setDraft]);

  const reset = useCallback(() => {
    setBinding(null);
    setToken(null);
  }, []);

  /** Put back the choice of a message that was refused before any work. */
  const restore = useCallback((value: string, skillId: string | null) => {
    const item = skillId === null ? undefined : items?.find((each) => each.skill_id === skillId);
    if (item === undefined) return;
    const candidate: Binding = { skillId: item.skill_id, name: item.name, start: 0, generation };
    const at = locate(value, candidate);
    if (at !== null) setBinding({ ...candidate, start: at });
  }, [items, generation]);

  /** True when the key belonged to the candidate list (the caller must not send). */
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return false;
    if (!open) return false;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (candidates.length === 0 || active === null) return true;
      const index = candidates.indexOf(active);
      const step = event.key === 'ArrowDown' ? 1 : candidates.length - 1;
      setActiveId(candidates[(index + step) % candidates.length]!.skill_id);
      return true;
    }
    const modified = event.shiftKey || event.ctrlKey || event.altKey || event.metaKey;
    if ((event.key === 'Enter' && !modified) || (event.key === 'Tab' && active !== null)) {
      event.preventDefault();
      if (active !== null) select(active);
      else setToken(null);
      return true;
    }
    if (event.key === 'Escape') {
      // defaultPrevented: the page's Esc handler leaves the chat sheet open.
      event.preventDefault();
      setToken(null);
      return true;
    }
    return false;
  };

  const composition = {
    onCompositionStart: () => {
      composing.current = true;
    },
    onCompositionEnd: (event: CompositionEvent<HTMLTextAreaElement>) => {
      composing.current = false;
      sync(event.currentTarget.value, event.currentTarget.selectionStart);
    },
  };

  /** What this message names: the chosen id, a uniquely typed full name, or nothing. */
  const resolveForSend = (message: string): MentionResolution => {
    if (binding !== null && locate(message, binding) !== null) {
      return { kind: 'ref', skillId: binding.skillId };
    }
    if (TRIGGERS.has(message.trimStart()[0] ?? '')) setWanted(true);
    return resolveTyped(message, skills.isSuccess ? items : undefined);
  };

  const shown: SkillBinding | null = binding === null ? null
    : { skillId: binding.skillId, name: binding.name };
  return {
    open, candidates, active, select, insert, clear, reset, restore, sync, onKeyDown,
    composition, binding: shown, resolveForSend,
    loading: skills.isLoading, failed: skills.isError,
    setActiveId,
  };
}

export type SkillMention = ReturnType<typeof useSkillMention>;
