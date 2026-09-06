import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import {
  BACKGROUND_PRESETS,
  DEFAULT_BACKGROUND_PRESET,
  type BackgroundPreset,
} from '../app/theme';

export const APPEARANCE_STORAGE_KEY = 'eternalai-appearance-background';

interface AppearanceState {
  background: BackgroundPreset;
  setBackground: (background: BackgroundPreset) => void;
}

function isBackgroundPreset(value: unknown): value is BackgroundPreset {
  return BACKGROUND_PRESETS.includes(value as BackgroundPreset);
}

/**
 * 底图选择。2026-09-02 裁决要求「换底图不动任何组件」，因此这里只存一个预设标识，由 AppShell 挂到
 * 外壳根节点的 class 上；持久化写法与 `navigationStore` 一致。存储里出现未知取值时退回默认预设，
 * 不让坏值把界面变成没有底图的白板。
 */
export const useAppearanceStore = create<AppearanceState>()(
  persist(
    (set) => ({
      background: DEFAULT_BACKGROUND_PRESET,
      setBackground: (background) => set({ background }),
    }),
    {
      name: APPEARANCE_STORAGE_KEY,
      merge: (persisted, current) => {
        const candidate = (persisted as Partial<AppearanceState> | undefined)
          ?.background;
        return {
          ...current,
          background: isBackgroundPreset(candidate)
            ? candidate
            : DEFAULT_BACKGROUND_PRESET,
        };
      },
    },
  ),
);
