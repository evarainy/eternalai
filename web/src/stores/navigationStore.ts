import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export const NAVIGATION_COLLAPSE_STORAGE_KEY = 'eternalai-navigation-collapsed';

interface NavigationState {
  collapsed: boolean;
  /** 消息页单独记一份：2026-11-11 雨爷定消息页左导航默认收起，其他页面不受影响。 */
  messagesCollapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
  toggleCollapsed: () => void;
  toggleMessagesCollapsed: () => void;
}

export const useNavigationStore = create<NavigationState>()(
  persist(
    (set) => ({
      collapsed: false,
      messagesCollapsed: true,
      setCollapsed: (collapsed) => set({ collapsed }),
      toggleCollapsed: () => set((state) => ({ collapsed: !state.collapsed })),
      toggleMessagesCollapsed: () =>
        set((state) => ({ messagesCollapsed: !state.messagesCollapsed })),
    }),
    { name: NAVIGATION_COLLAPSE_STORAGE_KEY },
  ),
);
