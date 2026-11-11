import { conversationDrafts, onImReset, saveDraft, useImStore } from './imStore';
import type { StoredDraft } from './imStore';
import type { ConversationKey } from './narrow';

/**
 * 输入框草稿与服务端 /drafts 的同步（FE_PLAN §5.2「草稿」）。
 *
 * - 一个「草稿位」对应一份服务端草稿（id 为 null 表示还没建），记着服务端已确认的内容。
 * - 同一草稿位的保存串行执行，要保存的内容记在草稿位自己身上：连续触发会合并成一次，切换到另一份
 *   草稿或进入编辑模式都不会把别处的文字写进来。
 * - 草稿位按「IM session × 会话」存在模块里，输入框重新挂载（切走再切回）时接着用同一个，在途保存
 *   确认的 id 不会丢；换身份时随 resetIm 清空，旧身份排着的保存在执行前核对 session，一律作废。
 */
export interface DraftSlot {
  readonly key: ConversationKey;
  readonly session: number;
  id: number | null;
  /** 服务端已确认的内容（id 为 null 时为空串）。 */
  saved: string;
  /** 已请求保存、还没执行的内容。 */
  pending: string | null;
  running: boolean;
  /** 最近一次保存失败，下次成功前保持「未保存」。 */
  failed: boolean;
  chain: Promise<void>;
  /** 当前挂着的输入框登记的回调：状态变化时刷新显示、删除失败时提示。 */
  onChange: (() => void) | null;
  onError: ((text: string) => void) | null;
}

const slots = new Map<ConversationKey, DraftSlot>();
onImReset(() => slots.clear());

function isCurrent(session: number): boolean {
  return useImStore.getState().session === session;
}

function newSlot(key: ConversationKey, session: number, draft: StoredDraft | undefined): DraftSlot {
  return {
    key, session, id: draft?.id ?? null, saved: draft?.content ?? '', pending: null, running: false, failed: false,
    chain: Promise.resolve(), onChange: null, onError: null,
  };
}

/** 本会话当前选中的草稿位；首次取用时指向最新的一份服务端草稿。 */
export function currentDraftSlot(key: ConversationKey): DraftSlot {
  const state = useImStore.getState();
  const existing = slots.get(key);
  if (existing && existing.session === state.session) return existing;
  const slot = newSlot(key, state.session, conversationDrafts(state, key)[0]);
  slots.set(key, slot);
  return slot;
}

/** 换到本会话的另一份草稿。旧草稿位保留自己的保存队列，之后的完成回调只改它自己。 */
export function switchDraftSlot(key: ConversationKey, draft: StoredDraft): DraftSlot {
  const slot = newSlot(key, useImStore.getState().session, draft);
  slots.set(key, slot);
  return slot;
}

/** 草稿位此刻是否有保存排着或在途。 */
export function draftBusy(slot: DraftSlot): boolean {
  return slot.running || slot.pending !== null;
}

async function runSave(slot: DraftSlot, failText: string | undefined): Promise<void> {
  if (slot.pending === null) return;
  const value = slot.pending;
  slot.pending = null;
  if (!isCurrent(slot.session) || value === slot.saved) {
    slot.onChange?.();
    return;
  }
  slot.running = true;
  slot.onChange?.();
  try {
    const id = await saveDraft(slot.key, slot.id, value);
    if (!isCurrent(slot.session)) return;
    slot.id = id;
    slot.saved = id === null ? '' : value;
    slot.failed = false;
  } catch {
    if (!isCurrent(slot.session)) return;
    slot.failed = true;
    if (failText) slot.onError?.(failText);
  } finally {
    slot.running = false;
    if (isCurrent(slot.session)) slot.onChange?.();
  }
}

/**
 * 请求把 `content` 存进这份草稿（空串表示删除）。排在本草稿位已有的保存之后；执行时取最近一次请求的
 * 内容。不重试：失败显示「未保存」，下次触发保存时再存。
 */
export function requestDraftSave(slot: DraftSlot, content: string, failText?: string): void {
  if (!isCurrent(slot.session)) return;
  slot.pending = content;
  slot.onChange?.();
  slot.chain = slot.chain.then(() => runSave(slot, failText));
}
