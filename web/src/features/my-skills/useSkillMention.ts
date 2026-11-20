/** `$` 指定调用：在输入框里用 `$`（或全角 `＄`）选本人可用的功能。
 *
 * 选中时记下功能 id 和它在草稿里的那一段文字的起止位置（结构化绑定）：前面的编辑只平移位置，
 * 改到这一段即解除。发送只认正在显示的标签，且先按发送时新读的名单核对（名称已变、不可用或手输
 * 名称现指向别的功能即改标或撤标并暂不发送）：把 id 交给服务端，由服务端按本人当前版本直接执行；
 * 没有标签的 `$名称`（含用户取消的）一律按普通文字发送，不写 `$` 时照旧由模型判断。手输句首的
 * 完整 `$功能名` 时当场显示标签（最长的完整名称优先）；单独的 `$100` 这类金额按普通文字。中文
 * 输入法组字期间不弹、不选、不发送，上屏后再判断。换账号即清空。 */

import {
  useCallback, useEffect, useMemo, useRef, useState, type CompositionEvent, type KeyboardEvent,
  type RefObject,
} from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { listSkills, type SkillListItem } from '../../api/userSkills';
import { useAuthStore } from '../../stores/authStore';
import { useMySkills } from './useMySkills';

interface Binding {
  skillId: string;
  name: string;
  /** The mention exactly as it stands in the draft at [start, end) (typed variants included). */
  text: string;
  start: number;
  end: number;
  generation: number;
  /** Bound by typing the full name (a longer name typed on may replace it). */
  typed: boolean;
}

interface MentionSpan {
  text: string;
  start: number;
  end: number;
}

export interface SkillBinding {
  skillId: string;
  name: string;
}

/** The chosen flow of one sent message, with its `$name` span in that message (if known). */
export interface SkillSelection {
  skillId: string;
  span: { name: string; text: string; start: number; end: number } | null;
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
const STANDALONE_AMOUNT = /^[\d.,]+(?=[\s\p{P}]|$)/u;

function norm(text: string): string {
  return text.normalize('NFKC').replace(/\s+/g, '').toLowerCase();
}

function fold(text: string): string {
  return text.normalize('NFKC').toLowerCase();
}

const isSpace = (char: string | undefined) => char !== undefined && /\s/.test(char);
const endsWord = (char: string | undefined) => char === undefined || /[\s\p{P}]/u.test(char);
const mentionText = (name: string) => `$${name.trim()}`;

/** ``text`` stands alone at [start, end) of ``value``. */
function standaloneAt(value: string, start: number, end: number, text: string): boolean {
  return start >= 0 && value.slice(start, end) === text
    && (start === 0 || isSpace(value[start - 1])) && endsWord(value[end]);
}

/** The span after ``prev`` became ``next`` under one alignment of the edit. */
function followAligned<T extends MentionSpan>(prev: string, next: string, binding: T,
  prefixFirst: boolean): T | null {
  const shortest = Math.min(prev.length, next.length);
  let common = 0;
  let tail = 0;
  const growCommon = (limit: number) => {
    while (common < limit && prev[common] === next[common]) common += 1;
  };
  const growTail = (limit: number) => {
    while (tail < limit && prev[prev.length - 1 - tail] === next[next.length - 1 - tail]) {
      tail += 1;
    }
  };
  if (prefixFirst) {
    growCommon(shortest);
    growTail(shortest - common);
  } else {
    growTail(shortest);
    growCommon(shortest - tail);
  }
  const editedUntil = prev.length - tail;
  let { start, end } = binding;
  if (editedUntil <= start) {
    const delta = next.length - prev.length;
    start += delta;
    end += delta;
  } else if (common < end) {
    return null;
  }
  return standaloneAt(next, start, end, binding.text) ? { ...binding, start, end } : null;
}

/** [from, to) of ``next`` that an edit from ``prev`` wrote (common prefix first). */
function editedRange(prev: string, next: string): [number, number] {
  const shortest = Math.min(prev.length, next.length);
  let common = 0;
  while (common < shortest && prev[common] === next[common]) common += 1;
  let tail = 0;
  while (tail < shortest - common
    && prev[prev.length - 1 - tail] === next[next.length - 1 - tail]) tail += 1;
  return [common, next.length - tail];
}

/** The span after an edit; when repeated text makes the edited place ambiguous, it is gone. */
function follow<T extends MentionSpan>(prev: string, next: string, binding: T): T | null {
  const a = followAligned(prev, next, binding, true);
  const b = followAligned(prev, next, binding, false);
  return a !== null && b !== null && a.start === b.start ? a : null;
}

/** The `$word` under the caret, digits included (they only never open the list). */
function triggerAt(value: string, caret: number): Token | null {
  let start = caret;
  while (start > 0 && !isSpace(value[start - 1])) start -= 1;
  let end = caret;
  while (end < value.length && !isSpace(value[end])) end += 1;
  if (!TRIGGERS.has(value[start] ?? '')) return null;
  return { start, end, text: value.slice(start + 1, caret) };
}

function tokenAt(value: string, caret: number): Token | null {
  const token = triggerAt(value, caret);
  if (token === null) return null;
  return DIGIT.test(value.slice(token.start + 1, token.end).normalize('NFKC')) ? null : token;
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

const UNREADABLE = '我的功能暂时读不到，请稍后再发送。';
const CHANGED = '按最新的功能名单，开头的名称对应的功能有变化，请确认后再发送。';

/** A typed `$name` at the start of the message: the longest complete usable name wins. */
function resolveTyped(message: string, items: readonly SkillListItem[]): MentionResolution {
  if (!TRIGGERS.has(message[0] ?? '')) return { kind: 'none' };
  const rest = message.slice(1);
  if (!rest.trim()) return { kind: 'none' };
  const amount = STANDALONE_AMOUNT.test(rest.normalize('NFKC'));
  const folded = fold(rest);
  const usable = items.filter((item) => item.state === 'usable');
  const matches = usable.filter((item) => {
    const name = fold(item.name.trim());
    return folded.startsWith(name) && endsWord(folded[name.length]);
  });
  if (matches.length === 0) {
    if (amount) return { kind: 'none' };
    const word = rest.split(/\s/)[0] ?? rest;
    return { kind: 'blocked', reason: `没有找到名为「${word}」的可用功能，请从列表中选择。` };
  }
  const longest = Math.max(...matches.map((item) => fold(item.name.trim()).length));
  const top = matches.filter((item) => fold(item.name.trim()).length === longest);
  const same = usable.filter((item) => norm(item.name) === norm(top[0]!.name));
  if (top.length > 1 || same.length > 1) {
    return { kind: 'blocked', reason: '有多个同名的功能，请从列表中选择。' };
  }
  const item = top[0]!;
  // The raw text that folds to the name (a full-width or other-case variant as typed).
  const name = fold(item.name.trim());
  let length = 1;
  while (length <= rest.length && fold(rest.slice(0, length)) !== name) length += 1;
  const end = 1 + length;
  const text = message.slice(0, end);
  return {
    kind: 'ref',
    selection: {
      skillId: item.skill_id,
      span: length <= rest.length && standaloneAt(message, 0, end, text)
        ? { name: item.name, text, start: 0, end } : null,
    },
  };
}

/** The edit [from, to) wrote into [start, end] (a pure deletion: inside the name or at its end). */
function touches(from: number, to: number, start: number, end: number): boolean {
  return to > from ? from <= end && to > start : from > start && from <= end;
}

type Typed =
  | { kind: 'bind'; found: MentionSpan & { skillId: string; name: string } }
  /** The leading name now needs a choice from the list (several flows share it). */
  | { kind: 'unbind' };

/** What the edit [from, to) of ``value`` makes of a `$name` typed at its start. */
function typedAt(value: string, items: readonly SkillListItem[], from: number, to: number):
  Typed | null {
  const lead = value.length - value.trimStart().length;
  const named = resolveTyped(value.slice(lead), items);
  if (named.kind === 'blocked') return { kind: 'unbind' };
  if (named.kind !== 'ref' || named.selection.span === null) return null;
  const { span, skillId } = named.selection;
  const start = span.start + lead;
  const end = span.end + lead;
  return touches(from, to, start, end)
    ? { kind: 'bind', found: { ...span, start, end, skillId } } : null;
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
  const queryClient = useQueryClient();
  // Read the list only once the user starts a `$` mention (or the panel opened it).
  const [wanted, setWanted] = useState(false);
  const skills = useMySkills(enabled && wanted);
  const items = skills.data?.items;
  const usable = useMemo(() => (items ?? []).filter((item) => item.state === 'usable'), [items]);
  const [token, setToken] = useState<Token | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [stored, setBinding] = useState<Binding | null>(null);
  const composing = useRef(false);
  // The draft before the IME started: the edit is judged once the text is final.
  const composeBase = useRef<string | null>(null);
  // Edits made at a leading `$word` before the list was read: judged once it is.
  const awaiting = useRef<{ from: number; to: number; length: number } | null>(null);
  // Only a list read successfully now binds a typed name (never a stale copy).
  const ready = skills.isSuccess;
  const itemsRef = useRef(ready ? items : undefined);
  itemsRef.current = ready ? items : undefined;
  // The draft the last edit started from (the prop lags one render behind an edit).
  const previous = useRef(draft);
  previous.current = draft;
  // Another account's choice is never shown or sent.
  const binding = stored !== null && stored.generation === generation ? stored : null;
  const bindingRef = useRef(binding);
  bindingRef.current = binding;
  // The send check under way; any edit, change of choice, account or unmount voids it.
  const sendCheck = useRef(0);
  useEffect(() => () => { sendCheck.current += 1; }, [draft, stored, generation]);
  useEffect(() => {
    setBinding(null);
    setToken(null);
    awaiting.current = null;
  }, [generation]);
  // A draft replaced from elsewhere (a quick prompt) takes the choice with it.
  useEffect(() => {
    if (composing.current) return;
    if (binding !== null && !standaloneAt(draft, binding.start, binding.end, binding.text)) {
      setBinding(null);
      setToken(null);
    }
  }, [draft, binding]);

  /** Show (or drop) the tag of a full name the edit [from, to) typed at the start. */
  const bindTyped = useCallback((value: string, from: number, to: number,
    current: Binding | null) => {
    const list = itemsRef.current;
    if (list === undefined) {
      if (!TRIGGERS.has(value.trimStart()[0] ?? '')) return;
      const last = awaiting.current;
      awaiting.current = last === null ? { from, to, length: value.length } : {
        from: Math.min(last.from, from),
        to: Math.max(last.to + value.length - last.length, to),
        length: value.length,
      };
      setWanted(true);
      return;
    }
    awaiting.current = null;
    if (current !== null && !current.typed) return;
    const typed = typedAt(value, list, from, to);
    if (typed === null) return;
    if (typed.kind === 'unbind') {
      if (current !== null) setBinding(null);
      return;
    }
    if (current !== null && current.start !== typed.found.start) return;
    setBinding({ ...typed.found, generation, typed: true });
  }, [generation]);
  useEffect(() => {
    const range = awaiting.current;
    if (!ready || range === null || composing.current) return;
    bindTyped(draft, range.from, range.to, bindingRef.current);
  }, [ready, items, draft, bindTyped]);

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
    // While the IME composes, nothing is judged: the choice stays as it was.
    if (composing.current) {
      previous.current = value;
      return;
    }
    const before = composeBase.current ?? previous.current;
    composeBase.current = null;
    previous.current = value;
    let current = bindingRef.current;
    if (before !== value) {
      if (current !== null) {
        current = follow(before, value, current);
        setBinding(current);
      }
      bindTyped(value, ...editedRange(before, value), current);
    }
    // Any `$word` (a digit-led name too) starts reading the list, ready for sending.
    if (caret !== null && triggerAt(value, caret) !== null) setWanted(true);
    setToken(caret === null ? null : tokenAt(value, caret));
  }, [bindTyped]);

  const bindAt = useCallback((item: SkillListItem, start: number) => {
    const text = mentionText(item.name);
    awaiting.current = null;
    setBinding({
      skillId: item.skill_id, name: item.name, text, start, end: start + text.length, generation,
      typed: false,
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
      binding.text) ? binding : null;
    if (current !== null && current.start < end && start < current.end) {
      // The choice is being changed in place: replace the whole bound span.
      start = Math.min(start, current.start);
      end = Math.max(end, current.end);
    }
    const inserted = `${mentionText(item.name)} `;
    const after = value.slice(end);
    const next = value.slice(0, start) + inserted + (after.startsWith(' ') ? after.slice(1) : after);
    if (current === null || (current.start < end && start < current.end)) return [next, start];
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
    const base = binding !== null && standaloneAt(draft, binding.start, binding.end, binding.text)
      ? cut(draft, binding.start, binding.end) : draft;
    const next = `${mentionText(item.name)} ${base.trimStart()}`;
    setDraft(next);
    previous.current = next;
    bindAt(item, 0);
    focusAt(next.length);
  }, [binding, draft, setDraft, bindAt, focusAt]);

  const clear = useCallback(() => {
    if (binding !== null && standaloneAt(draft, binding.start, binding.end, binding.text)) {
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
    awaiting.current = null;
  }, []);

  /** Put back the frozen choice of a message that was refused before any work. */
  const restore = useCallback((value: string, selection: SkillSelection | null) => {
    const span = selection?.span;
    if (selection == null || span == null) return;
    if (standaloneAt(value, span.start, span.end, span.text)) {
      setBinding({ skillId: selection.skillId, name: span.name, text: span.text,
        start: span.start, end: span.end, generation, typed: false });
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
      sendCheck.current += 1;
      composeBase.current ??= previous.current;
      setToken(null);
    },
    onCompositionEnd: (event: CompositionEvent<HTMLTextAreaElement>) => {
      composing.current = false;
      sync(event.currentTarget.value, event.currentTarget.selectionStart);
    },
  };

  /** The IME is composing: neither the list nor the page may act on keys. */
  const isComposing = () => composing.current;

  /** The trimmed ``message`` (from the current draft) may name a flow: check it before sending.
   * Otherwise it is plain text and goes as it is. */
  const needsCheck = (message: string) => binding !== null || TRIGGERS.has(message[0] ?? '');

  /** What such a ``message`` names, judged on the list read now: a tag that no longer matches it
   * is changed or dropped and the send waits for a look. Null when the check was voided during
   * the read (an edit, a change of choice, the IME, another account or leaving the page): the
   * send is dropped. */
  const resolveForSend = async (message: string): Promise<MentionResolution | null> => {
    setWanted(true);
    const check = ++sendCheck.current;
    // Read directly (not through the query, whose cancel or offline pause would hand back the
    // cached copy or hold the send); the result refreshes the shared cache.
    const list = await listSkills().then((data) => {
      queryClient.setQueryData(['my-skills', generation], data);
      return data.items;
    }, () => null);
    if (sendCheck.current !== check) return null;
    if (list === null) return { kind: 'blocked', reason: UNREADABLE };
    const pending = awaiting.current;
    awaiting.current = null;
    if (binding === null) {
      // Edits made before any list was read are judged on this one (as the list arriving would).
      const typed = pending === null ? null : typedAt(draft, list, pending.from, pending.to);
      if (typed?.kind === 'bind') {
        setBinding({ ...typed.found, generation, typed: true });
        return { kind: 'blocked', reason: CHANGED };
      }
      const named = resolveTyped(message, list);
      // Only the shown tag is sent: a full name without one (undone, or never bound) is text.
      return named.kind === 'ref' ? { kind: 'none' } : named;
    }
    const item = list.find((entry) => entry.skill_id === binding.skillId);
    if (item === undefined || item.state !== 'usable' || item.name !== binding.name) {
      setBinding(null);
      return { kind: 'blocked', reason: `「${binding.name}」已改名或不可用，请重新选择。` };
    }
    const lead = draft.length - draft.trimStart().length;
    const start = binding.start - lead;
    const end = binding.end - lead;
    if (binding.typed) {
      // A typed name is matched again where it stands (a longer or shared name may now win).
      const named = resolveTyped(message.slice(start), list);
      const found = named.kind === 'ref' ? named.selection : null;
      const span = found?.span ?? null;
      if (found?.skillId !== binding.skillId || span?.end !== end - start) {
        setBinding(found === null || span === null ? null : {
          skillId: found.skillId, name: span.name, text: span.text, start: binding.start,
          end: binding.start + span.end, generation, typed: true,
        });
        return { kind: 'blocked', reason: CHANGED };
      }
    }
    return { kind: 'ref', selection: { skillId: binding.skillId,
      span: { name: binding.name, text: binding.text, start, end } } };
  };

  const shown: SkillBinding | null = binding === null ? null
    : { skillId: binding.skillId, name: binding.name };
  return {
    open, candidates, active, select, insert, clear, reset, restore, sync, onKeyDown,
    composition, isComposing, binding: shown, needsCheck, resolveForSend,
    loading: skills.isLoading, failed: skills.isError,
    setActiveId,
  };
}

export type SkillMention = ReturnType<typeof useSkillMention>;
