import { create } from 'zustand';

/**
 * 消息动作与输入框之间的通道：右键菜单的「回复」「引用」往输入框插入文字，「编辑」「↑ 编辑上一条」
 * 把输入框切到编辑模式。只在当前会话内有效，切换会话时由 ChatPane 清空。
 */
interface ComposerBridge {
  insert: { text: string; nonce: number } | null;
  editing: { messageId: number; raw: string } | null;
  requestInsert: (text: string) => void;
  startEditing: (messageId: number, raw: string) => void;
  stopEditing: () => void;
  reset: () => void;
}

let nonce = 0;

export const useComposerBridge = create<ComposerBridge>()((set) => ({
  insert: null,
  editing: null,
  requestInsert: (text) => set({ insert: { text, nonce: ++nonce } }),
  startEditing: (messageId, raw) => set({ editing: { messageId, raw } }),
  stopEditing: () => set({ editing: null }),
  reset: () => set({ insert: null, editing: null }),
}));
