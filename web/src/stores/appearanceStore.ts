import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import {
  CLOCK_STYLES,
  DEFAULT_FONT_SIZE,
  DEFAULT_SKIN,
  FONT_SIZES,
  FX_MODES,
  SKINS,
  type ClockStyle,
  type FontSize,
  type FxMode,
  type Skin,
} from '../app/theme';

export const APPEARANCE_STORAGE_KEY = 'eternalai-appearance';

interface AppearanceChoices {
  skin: Skin;
  size: FontSize;
  clock: ClockStyle;
  /** `null` 表示用户没有选过：按本机有没有显卡加速自动决定（见 `resolveFxMode`）。 */
  fx: FxMode | null;
}

interface AppearanceState extends AppearanceChoices {
  setSkin: (skin: Skin) => void;
  setSize: (size: FontSize) => void;
  setClock: (clock: ClockStyle) => void;
  toggleClock: () => void;
  setFx: (fx: FxMode) => void;
}

const DEFAULTS: AppearanceChoices = {
  skin: DEFAULT_SKIN,
  size: DEFAULT_FONT_SIZE,
  clock: 'digit',
  fx: null,
};

function pick<T extends string>(allowed: readonly T[], value: unknown, fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}

/**
 * 外观选择：皮肤、字号、时钟样式、动效。现在记在浏览器本地；按用户保存到后端是第二期的事。
 * 存储里出现未知取值时逐项退回默认，不让坏值把界面弄成没有样式的状态。
 */
export const useAppearanceStore = create<AppearanceState>()(
  persist(
    (set) => ({
      ...DEFAULTS,
      setSkin: (skin) => set({ skin }),
      setSize: (size) => set({ size }),
      setClock: (clock) => set({ clock }),
      toggleClock: () =>
        set((state) => ({ clock: state.clock === 'digit' ? 'dial' : 'digit' })),
      setFx: (fx) => set({ fx }),
    }),
    {
      name: APPEARANCE_STORAGE_KEY,
      partialize: ({ skin, size, clock, fx }) => ({ skin, size, clock, fx }),
      merge: (persisted, current) => {
        const stored = (persisted ?? {}) as Partial<Record<keyof AppearanceChoices, unknown>>;
        return {
          ...current,
          skin: pick(SKINS, stored.skin, DEFAULTS.skin),
          size: pick(FONT_SIZES, stored.size, DEFAULTS.size),
          clock: pick(CLOCK_STYLES, stored.clock, DEFAULTS.clock),
          fx: FX_MODES.includes(stored.fx as FxMode) ? (stored.fx as FxMode) : null,
        };
      },
    },
  ),
);

let softwareRendering: boolean | undefined;

/**
 * 本机是否在用软件渲染（没有显卡加速，常见于远程桌面和老电脑）。只探测一次。
 *
 * 做法是建一个 WebGL 上下文并要求「没有重大性能缺陷」：软件光栅下浏览器会拒绝创建。探测不出来
 * （例如 WebGL 被策略关闭）时按「有加速」处理，用户仍可在设置里手动切到精简。
 */
export function isSoftwareRendering(): boolean {
  if (softwareRendering !== undefined) return softwareRendering;
  softwareRendering = false;
  if (typeof WebGLRenderingContext === 'undefined') return softwareRendering;
  try {
    // 两次探测各用一张新画布：同一张画布拿到过上下文后，参数不同的第二次请求不会重新创建。
    const strict = document
      .createElement('canvas')
      .getContext('webgl', { failIfMajorPerformanceCaveat: true });
    const lenient = strict ?? document.createElement('canvas').getContext('webgl');
    if (lenient === null) {
      // 两种都建不了说明 WebGL 不可用，不下结论。
      return softwareRendering;
    }
    const info = lenient.getExtension('WEBGL_debug_renderer_info');
    const renderer = info === null ? '' : String(lenient.getParameter(info.UNMASKED_RENDERER_WEBGL));
    // 宽松模式能建、严格模式不能建，或渲染器名字就是软件实现，都按软件渲染处理。
    softwareRendering = strict === null || /swiftshader|llvmpipe|software/i.test(renderer);
  } catch {
    softwareRendering = false;
  }
  return softwareRendering;
}

/** 实际生效的动效档：用户选过就用用户的，没选过按显卡加速情况决定。 */
export function resolveFxMode(fx: FxMode | null): FxMode {
  return fx ?? (isSoftwareRendering() ? 'lite' : 'full');
}

/** 把外观选择写到 `<html>` 上，样式表按这四个属性取值。 */
export function applyAppearance(choices: AppearanceChoices): void {
  const root = document.documentElement;
  root.dataset.skin = choices.skin;
  root.dataset.size = choices.size;
  root.dataset.clock = choices.clock;
  root.dataset.fx = resolveFxMode(choices.fx);
}
