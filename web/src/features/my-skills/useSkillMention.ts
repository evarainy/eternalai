/** `$` 指定调用：在输入框里用 `$`（或全角 `＄`）选本人可用的功能。
 *
 * 选中时记下功能 id（结构化绑定），发送时只把 id 交给服务端，由服务端按本人当前版本直接执行；
 * 不写 `$` 时照旧由模型判断。手输完整的 `$功能名` 也能绑定，最长的完整名称优先；
 * `$100` 这类金额按普通文字发送。中文输入法组字期间不弹、不选、不发送。 */

import {
  useCallback, useMemo, useRef, useState, type CompositionEvent, type KeyboardEvent,
  type RefObject,
} from 'react';
import type { SkillListItem } from '../../api/userSkills';
import { useMySkills } from './useMySkills';

export interface SkillBinding {
  skillId: string;
  name: string;
}

export type MentionResolution =
  | { kind: 'none' }
  | { kind: 'ref'; skillId: string }
  | { kind: 'blocked'; reason: string };

interface Token {
  start: number;
  end: number;
  text: string;
}

const MAX_CANDIDATES = 8;
const TRIGGERS = new Set(['$', '＄']);
const AMOUNT = /^[\d.,]/;

function norm(text: string): string {
  return text.normalize('NFKC').replace(/\s+/g, '').toLowerCase();
}

/** The `$word` the caret is in (start of line or after a space), if any. */
function tokenAt(value: string, caret: number): Token | null {
  let start = caret;
  while (start > 0 && !/\s/.test(value[start - 1]!)) start -= 1;
  const word = value.slice(start, caret);
  if (!word || !TRIGGERS.has(word[0]!)) return null;
  const text = word.slice(1);
  return AMOUNT.test(text) ? null : { start, end: caret, text };
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

function mention(name: string): string {
  return `$${name}`;
}

/** The draft without the `$name` the user un-chose (so it is never bound back). */
function withoutMention(draft: string, name: string): string {
  const index = draft.normalize('NFKC').indexOf(mention(name).normalize('NFKC'));
  if (index < 0) return draft;
  const end = index + mention(name).length;
  return (draft.slice(0, index) + draft.slice(draft[end] === ' ' ? end + 1 : end)).trimStart();
}

/** A typed `$name` at the start of the message: the longest complete usable name wins. */
function resolveTyped(message: string, items: readonly SkillListItem[] | undefined):
  MentionResolution {
  const text = message.normalize('NFKC').trimStart();
  if (!text.startsWith('$')) return { kind: 'none' };
  const rest = text.slice(1);
  if (!rest.trim()) return { kind: 'none' };
  // A full name wins over the amount rule ("2026 年报"); a bare amount ("$100") stays text.
  const amount = AMOUNT.test(rest);
  if (items === undefined) {
    return amount ? { kind: 'none' }
      : { kind: 'blocked', reason: '功能列表还没读到，请稍后再发送。' };
  }
  const boundary = (char: string | undefined) => char === undefined || /[\s\p{P}]/u.test(char);
  const usable = items.filter((item) => item.state === 'usable');
  const matches = usable.filter((item) => {
    const name = item.name.normalize('NFKC');
    return rest.startsWith(name) && boundary(rest[name.length]);
  });
  if (matches.length === 0) {
    if (amount) return { kind: 'none' };
    const word = rest.split(/\s/)[0] ?? rest;
    return { kind: 'blocked', reason: `没有找到名为「${word}」的可用功能，请从列表中选择。` };
  }
  const longest = Math.max(...matches.map((item) => item.name.normalize('NFKC').length));
  const top = matches.filter((item) => item.name.normalize('NFKC').length === longest);
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
  // Read the list only once the user starts a `$` mention (or the panel opened it).
  const [wanted, setWanted] = useState(false);
  const skills = useMySkills(enabled && wanted);
  const items = skills.data?.items;
  const usable = useMemo(() => (items ?? []).filter((item) => item.state === 'usable'), [items]);
  const [token, setToken] = useState<Token | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [binding, setBinding] = useState<SkillBinding | null>(null);
  const composing = useRef(false);

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

  /** After every edit or caret move: drop a binding whose text is gone, re-read the token. */
  const sync = useCallback((value: string, caret: number | null) => {
    setBinding((current) => (current !== null
      && !value.normalize('NFKC').includes(mention(current.name).normalize('NFKC'))
      ? null : current));
    if (composing.current) return;
    const next = caret === null ? null : tokenAt(value, caret);
    if (next !== null) setWanted(true);
    setToken(next);
  }, []);

  const bind = useCallback((item: SkillListItem) => {
    setBinding({ skillId: item.skill_id, name: item.name });
    setToken(null);
    onBind?.();
  }, [onBind]);

  const select = useCallback((item: SkillListItem) => {
    if (token === null) return;
    const inserted = `${mention(item.name)} `;
    setDraft(draft.slice(0, token.start) + inserted + draft.slice(token.end));
    bind(item);
    focusAt(token.start + inserted.length);
  }, [token, draft, setDraft, bind, focusAt]);

  /** From the panel: one flow per message, so a new choice replaces the old one. */
  const insert = useCallback((item: SkillListItem) => {
    const rest = binding === null ? draft : withoutMention(draft, binding.name);
    const next = `${mention(item.name)} ${rest.trimStart()}`;
    setDraft(next);
    bind(item);
    focusAt(next.length);
  }, [binding, draft, setDraft, bind, focusAt]);

  const clear = useCallback(() => {
    if (binding !== null) setDraft(withoutMention(draft, binding.name));
    setBinding(null);
    setToken(null);
  }, [binding, draft, setDraft]);

  const reset = useCallback(() => {
    setBinding(null);
    setToken(null);
  }, []);

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
    if (/^\s*[$＄]/.test(message)) setWanted(true);
    if (binding !== null
        && message.normalize('NFKC').includes(mention(binding.name).normalize('NFKC'))) {
      return { kind: 'ref', skillId: binding.skillId };
    }
    return resolveTyped(message, skills.isSuccess ? items : undefined);
  };

  return {
    open, candidates, active, select, insert, clear, reset, sync, onKeyDown, composition,
    binding, resolveForSend,
    loading: skills.isLoading, failed: skills.isError,
    setActiveId,
  };
}

export type SkillMention = ReturnType<typeof useSkillMention>;
