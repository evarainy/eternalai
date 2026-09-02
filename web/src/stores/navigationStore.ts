import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export const NAVIGATION_COLLAPSE_STORAGE_KEY = 'eternalai-navigation-collapsed';

interface NavigationState {
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
  toggleCollapsed: () => void;
}

export const useNavigationStore = create<NavigationState>()(
  persist(
    (set) => ({
      collapsed: false,
      setCollapsed: (collapsed) => set({ collapsed }),
      toggleCollapsed: () => set((state) => ({ collapsed: !state.collapsed })),
    }),
    { name: NAVIGATION_COLLAPSE_STORAGE_KEY },
  ),
);
