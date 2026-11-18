/** `$` 指定调用：在输入框里用 `$`（或全角 `＄`）选本人可用的功能。
 *
 * 选中时记下功能 id 和它在草稿里的那一段文字的起止位置（结构化绑定）：前面的编辑只平移位置，
 * 改到这一段即解除。发送时只把 id 交给服务端，由服务端按本人当前版本直接执行；不写 `$` 时照旧
 * 由模型判断。手输完整的 `$功能名` 也能绑定（最长的完整名称优先）；单独的 `$100` 这类金额按普通
 * 文字发送。中文输入法组字期间不弹、不选、不发送。换账号即清空。 */

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
  /** `$name` occupies [start, end) of the draft. */
  start: number;
  end: number;
  generation: number;
}

export interface SkillBinding {
  skillId: string;
  name: string;
}

/** The chosen flow of one sent message, with its `$name` span in that message (if known). */
export interface SkillSelection {
  skillId: string;
  span: { name: string; start: number; end: number } | null;
}

export type MentionResolution =
  | { kind: 'none' }
  | { kind: 'ref'; selection: SkillSelection }
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
const mentionText = (name: string) => `$${name}`;

/** `$name` stands alone at [start, end) of ``value``. */
function standaloneAt(value: string, start: number, end: number, name: string): boolean {
  return start >= 0 && value.slice(start, end) === mentionText(name)
    && (start === 0 || isSpace(value[start - 1])) && endsWord(value[end]);
}

/** The binding after ``prev`` became ``next``: shifted, kept, or gone when its span changed. */
function follow(prev: string, next: string, binding: Binding): Binding | null {
  let common = 0;
  const shortest = Math.min(prev.length, next.length);
  while (common < shortest && prev[common] === next[common]) common += 1;
  let tail = 0;
  while (tail < shortest - common
      && prev[prev.length - 1 - tail] === next[next.length - 1 - tail]) tail += 1;
  const editedUntil = prev.length - tail;
  let { start, end } = binding;
  if (editedUntil <= start) {
    const delta = next.length - prev.length;
    start += delta;
    end += delta;
  } else if (common < end) {
    return null;
  }
  return standaloneAt(next, start, end, binding.name) ? { ...binding, start, end } : null;
}

function tokenAt(value: string, caret: number): Token | null {
  let start = caret;
  while (start > 0 && !isSpace(value[start - 1])) start -= 1;
  let end = caret;
  while (end < value.length && !isSpace(value[end])) end += 1;
  if (!TRIGGERS.has(value[start] ?? '')) return null;
  if (DIGIT.test(value.slice(start + 1, end).normalize('NFKC'))) return null;
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

/** ``value`` without [start, end) and one space right after it. */
function cut(value: string, start: number, end: number): string {
  return value.slice(0, start) + value.slice(value[end] === ' ' ? end + 1 : end);
}

type ListState = 'ready' | 'loading' | 'failed';

/** A typed `$name` at the start of the message: the longest complete usable name wins. */
function resolveTyped(message: string, items: readonly SkillListItem[] | undefined,
  state: ListState): MentionResolution {
  if (!TRIGGERS.has(message[0] ?? '')) return { kind: 'none' };
  const rest = message.slice(1);
  if (!rest.trim()) return { kind: 'none' };
  const amount = STANDALONE_AMOUNT.test(rest.normalize('NFKC'));
  if (state !== 'ready' || items === undefined) {
    // Only a bare amount may go without the list; a name must be checked first.
    if (amount) return { kind: 'none' };
    return { kind: 'blocked', reason: state === 'failed'
      ? '我的功能暂时读不到，请稍后再发送。' : '正在读取我的功能，请稍后再发送。' };
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
  const item = top[0]!;
  const end = 1 + item.name.length;
  return {
    kind: 'ref',
    selection: {
      skillId: item.skill_id,
      span: standaloneAt(message, 0, end, item.name) ? { name: item.name, start: 0, end } : null,
    },
  };
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
  // The draft the last edit started from (the prop lags one render behind an edit).
  const previous = useRef(draft);
  previous.current = draft;
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
    const before = previous.current;
    previous.current = value;
    if (before !== value) {
      setBinding((current) => (current === null ? null : follow(before, value, current)));
    }
    if (composing.current) return;
    const next = caret === null ? null : tokenAt(value, caret);
    if (next !== null) setWanted(true);
    setToken(next);
  }, []);

  const bindAt = useCallback((item: SkillListItem, start: number) => {
    setBinding({
      skillId: item.skill_id, name: item.name, start,
      end: start + mentionText(item.name).length, generation,
    });
    setToken(null);
    onBind?.();
  }, [generation, onBind]);

  /** The draft with [start, end) replaced by the chosen `$name `; one flow per message. */
  const place = useCallback((value: string, from: number, to: number, item: SkillListItem):
    [string, number] => {
    let start = from;
    let end = to;
    const current = binding !== null && standaloneAt(value, binding.start, binding.end,
      binding.name) ? binding : null;
    if (current !== null && current.start < end && start < current.end) {
      // The choice is being changed in place: replace the whole bound span.
      start = Math.min(start, current.start);
      end = Math.max(end, current.end);
    }
    const inserted = `${mentionText(item.name)} `;
    const after = value.slice(end);
    const next = value.slice(0, start) + inserted + (after.startsWith(' ') ? after.slice(1) : after);
    if (current === null || (current.start < end && start < current.end)
        || (current.start <= start && end <= current.end)) {
      return [next, start];
    }
    // An earlier choice elsewhere is removed (one flow per message).
    if (current.end <= start) {
      const without = cut(next, current.start, current.end);
      return [without, start - (next.length - without.length)];
    }
    const shift = next.length - value.length;
    return [cut(next, current.start + shift, current.end + shift), start];
  }, [binding]);

  const select = useCallback((item: SkillListItem) => {
    if (token === null || composing.current) return;
    const [next, start] = place(draft, token.start, token.end, item);
    setDraft(next);
    previous.current = next;
    bindAt(item, start);
    focusAt(start + mentionText(item.name).length + 1);
  }, [token, draft, place, setDraft, bindAt, focusAt]);

  /** From the panel: the chosen flow goes to the start of the message. */
  const insert = useCallback((item: SkillListItem) => {
    const base = binding !== null && standaloneAt(draft, binding.start, binding.end, binding.name)
      ? cut(draft, binding.start, binding.end) : draft;
    const next = `${mentionText(item.name)} ${base.trimStart()}`;
    setDraft(next);
    previous.current = next;
    bindAt(item, 0);
    focusAt(next.length);
  }, [binding, draft, setDraft, bindAt, focusAt]);

  const clear = useCallback(() => {
    if (binding !== null && standaloneAt(draft, binding.start, binding.end, binding.name)) {
      const next = cut(draft, binding.start, binding.end);
      setDraft(next);
      previous.current = next;
    }
    setBinding(null);
    setToken(null);
  }, [binding, draft, setDraft]);

  const reset = useCallback(() => {
    setBinding(null);
    setToken(null);
  }, []);

  /** Put back the frozen choice of a message that was refused before any work. */
  const restore = useCallback((value: string, selection: SkillSelection | null) => {
    const span = selection?.span;
    if (selection == null || span == null) return;
    if (standaloneAt(value, span.start, span.end, span.name)) {
      setBinding({ skillId: selection.skillId, name: span.name, start: span.start,
        end: span.end, generation });
    }
  }, [generation]);

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
      setToken(null);
    },
    onCompositionEnd: (event: CompositionEvent<HTMLTextAreaElement>) => {
      composing.current = false;
      sync(event.currentTarget.value, event.currentTarget.selectionStart);
    },
  };

  /** The IME is composing: neither the list nor the page may act on keys. */
  const isComposing = () => composing.current;

  /** What the trimmed ``message`` (from the current draft) names. */
  const resolveForSend = (message: string): MentionResolution => {
    const lead = draft.length - draft.trimStart().length;
    if (binding !== null) {
      const start = binding.start - lead;
      const end = binding.end - lead;
      if (standaloneAt(message, start, end, binding.name)) {
        return { kind: 'ref',
          selection: { skillId: binding.skillId, span: { name: binding.name, start, end } } };
      }
    }
    if (TRIGGERS.has(message[0] ?? '')) {
      setWanted(true);
      if (skills.isError) void skills.refetch();
    }
    const state: ListState = skills.isSuccess ? 'ready' : skills.isError ? 'failed' : 'loading';
    return resolveTyped(message, items, state);
  };

  const shown: SkillBinding | null = binding === null ? null
    : { skillId: binding.skillId, name: binding.name };
  return {
    open, candidates, active, select, insert, clear, reset, restore, sync, onKeyDown,
    composition, isComposing, binding: shown, resolveForSend,
    loading: skills.isLoading, failed: skills.isError,
    setActiveId,
  };
}

export type SkillMention = ReturnType<typeof useSkillMention>;
