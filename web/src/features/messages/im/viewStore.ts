import { create } from 'zustand';
import { onImReset, openConversation, startDirect } from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';

/**
 * 消息区当前看的是「消息」还是「联系人」页签（FE_PLAN §3「消息 / 联系人」切换），以及联系人页里选中的人。
 * 资料卡的「查看资料」通过这里切到联系人页并选中此人。换身份时清空。
 */
interface ImView {
  tab: 'messages' | 'contacts';
  contact: number | null;
  showMessages: () => void;
  showContact: (userId: number | null) => void;
}

export const useImView = create<ImView>()((set) => ({
  tab: 'messages',
  contact: null,
  showMessages: () => set({ tab: 'messages' }),
  showContact: (contact) => set({ tab: 'contacts', contact }),
}));

onImReset(() => useImView.setState({ tab: 'messages', contact: null }));

/** 从联系人页、资料卡发起聊天：切回「消息」页签再打开会话。 */
export function chatWith(userIds: number[]): void {
  useImView.getState().showMessages();
  startDirect(userIds);
}

export function showConversation(key: ConversationKey): void {
  useImView.getState().showMessages();
  openConversation(key);
}
