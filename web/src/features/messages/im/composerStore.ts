import { create } from 'zustand';
import { useImStore } from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';

/**
 * 消息动作与输入框之间的通道：右键菜单的「回复」「引用」往输入框插入文字，「编辑」「↑ 编辑上一条」
 * 把输入框切到编辑模式。编辑请求带发起时的会话 key 与 IM session：读原文的迟到回调只在仍是同一会话、
 * 同一身份时生效，输入框也只消费 key 与自己一致的编辑请求。切换会话时由 ChatPane 清空。
 */
interface ComposerBridge {
  insert: { text: string; nonce: number } | null;
  editing: { key: ConversationKey; messageId: number; raw: string } | null;
  requestInsert: (text: string) => void;
  startEditing: (key: ConversationKey, session: number, messageId: number, raw: string) => void;
  stopEditing: () => void;
  reset: () => void;
}

let nonce = 0;

export const useComposerBridge = create<ComposerBridge>()((set) => ({
  insert: null,
  editing: null,
  requestInsert: (text) => set({ insert: { text, nonce: ++nonce } }),
  startEditing: (key, session, messageId, raw) => {
    const im = useImStore.getState();
    if (im.session === session && im.selected === key) set({ editing: { key, messageId, raw } });
  },
  stopEditing: () => set({ editing: null }),
  reset: () => set({ insert: null, editing: null }),
}));
