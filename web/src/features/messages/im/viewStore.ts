import { create } from 'zustand';
import { onImReset } from '../zulip/imStore';

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
