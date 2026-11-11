import { create } from 'zustand';
import { onImReset } from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';

/** 一次多选最多的条数（FE_PLAN §11；与 V1 多条来源上限一致）。 */
export const SELECT_LIMIT = 50;

/**
 * 聊天记录多选（FE_PLAN §11）：只在一个会话内有效，换会话或换身份时清空。按住 Shift 点击时，从上一次点击的
 * 消息到这一条整段选中；超过上限的部分不加入。
 */
interface Selection {
  key: ConversationKey | null;
  ids: number[];
  last: number | null;
  start: (key: ConversationKey, messageId: number) => void;
  /** `ordered` 是当前窗口里按顺序排列的消息 id，用于 Shift 连选。 */
  toggle: (messageId: number, range: boolean, ordered: number[]) => void;
  clear: () => void;
}

export const useSelection = create<Selection>()((set, get) => ({
  key: null,
  ids: [],
  last: null,
  start: (key, messageId) => set({ key, ids: [messageId], last: messageId }),
  toggle: (messageId, range, ordered) => {
    const { ids, last } = get();
    if (range && last !== null) {
      const from = ordered.indexOf(last);
      const to = ordered.indexOf(messageId);
      if (from >= 0 && to >= 0) {
        const span = ordered.slice(Math.min(from, to), Math.max(from, to) + 1);
        const merged = [...new Set([...ids, ...span])].slice(0, SELECT_LIMIT);
        set({ ids: merged, last: messageId });
        return;
      }
    }
    if (ids.includes(messageId)) set({ ids: ids.filter((id) => id !== messageId), last: messageId });
    else if (ids.length < SELECT_LIMIT) set({ ids: [...ids, messageId], last: messageId });
  },
  clear: () => set({ key: null, ids: [], last: null }),
}));

onImReset(() => useSelection.getState().clear());
