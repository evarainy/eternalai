import type { ThemeConfig } from 'antd';

/**
 * 底图预设。2026-09-02「视觉方向改为 iOS 26 玻璃拟态」要求换底图不动任何组件，因此底图只是外壳
 * 根节点上的一个 class，组件一律靠玻璃材质与底图透出取色，不写死填充色。
 */
export const BACKGROUND_PRESETS = ['bgA', 'bgB', 'bgC'] as const;

export type BackgroundPreset = (typeof BACKGROUND_PRESETS)[number];

export const DEFAULT_BACKGROUND_PRESET: BackgroundPreset = 'bgA';

/** 面向用户的底图名称；不用「预设 A」这类系统术语。 */
export const BACKGROUND_PRESET_LABELS: Record<BackgroundPreset, string> = {
  bgA: '蓝紫',
  bgB: '暖橙',
  bgC: '青绿',
};

/**
 * 单屏允许携带 `backdrop-filter` 的元素数量上限。
 *
 * 2026-09-02 裁决把这条定为硬约束，数字来自出稿时的实测边界（每个按钮图标各加一层 → 单屏 40 余层，
 * 整块画布渲染不出来；KPI 卡各带一层 → 单屏 9 层，该屏单独崩），且实测发生在 x86 + 新版 Chrome 上，
 * ARM 麒麟终端只会更紧，不得放宽。`web/src/app/__tests__/blurBudget.test.tsx` 在真实渲染结果上执行
 * 该上限。
 */
export const BLUR_LAYER_BUDGET = 6;

/**
 * 玻璃面在最深底图上的**合成底色**，作为对比度基准。
 *
 * 这两个值不是「画出来的颜色」，而是把 CSS 里的半透明面与三套底图中最深的一套（`bgA`：四层彩色
 * radial 全部叠满后的 `rgb(114, 165, 252)`）做 source-over 合成后的最坏结果：
 *
 * - `surface`   = `rgba(255,255,255,.86)`（内容面板）叠在该底色上 → `rgb(235, 242, 255)`
 * - `glassSurface` = `rgba(255,255,255,.58)`（左导航 / 顶栏这类大面玻璃）叠在该底色上 → `rgb(196, 217, 254)`
 *
 * 玻璃底的对比度不可预测，所以正文与辅助文字的对比度一律按这两个**最坏底色**核，而不是按看起来
 * 差不多的浅色核。`backdrop-filter` 只做模糊与饱和度调整，不改变合成后的平均亮度，因此不参与该核算。
 */
export const workbenchTokens = {
  bodyFontSize: 19,
  auxiliaryFontSize: 16,
  captionFontSize: 14,
  lineHeight: 1.45,
  minimumTargetSize: 44,
  /** 承载正文的面板必须垫足够不透明的底，画稿用的就是这一档。 */
  panelFill: 'rgba(255, 255, 255, 0.86)',
  /** 左导航 / 顶栏这类大面玻璃。 */
  glassFill: 'rgba(255, 255, 255, 0.58)',
  /** 浮动窗与顶栏这类需要更实一点的大面玻璃。 */
  glassFillRaised: 'rgba(255, 255, 255, 0.66)',
  /**
   * 三套底图各自「彩色 radial 全部叠满」时的最深底色，按 source-over 逐层合成算出。这是玻璃面下方
   * 可能出现的最暗背景；`bgA` 是三者中最暗的，因此对比度一律按它核。
   */
  backgroundFloors: {
    bgA: '#72a5fc',
    bgB: '#f7b5a1',
    bgC: '#8ad6cc',
  },
  colors: {
    surface: '#ebf2ff',
    glassSurface: '#c4d9fe',
    text: '#161d2e',
    textSecondary: '#454e66',
    placeholder: '#4f5872',
    primary: '#2450c8',
    focus: '#b44b00',
    success: '#0b5c2b',
    warning: '#8c4a00',
    error: '#a8231c',
    neutral: '#4f5872',
  },
} as const;

export const workbenchTheme: ThemeConfig = {
  token: {
    borderRadius: 14,
    colorBgBase: '#eef2fc',
    colorBgContainer: workbenchTokens.panelFill,
    colorBgElevated: 'rgba(255, 255, 255, 0.94)',
    colorBgLayout: 'transparent',
    colorBorderSecondary: 'rgba(22, 29, 46, 0.10)',
    colorError: workbenchTokens.colors.error,
    colorPrimary: workbenchTokens.colors.primary,
    colorPrimaryBg: 'rgba(47, 107, 255, 0.14)',
    colorPrimaryBgHover: 'rgba(47, 107, 255, 0.20)',
    colorPrimaryBorder: 'rgba(47, 107, 255, 0.42)',
    colorSuccess: workbenchTokens.colors.success,
    colorText: workbenchTokens.colors.text,
    colorTextPlaceholder: workbenchTokens.colors.placeholder,
    colorTextSecondary: workbenchTokens.colors.textSecondary,
    colorWarning: workbenchTokens.colors.warning,
    controlHeight: 48,
    controlHeightLG: 52,
    controlHeightSM: workbenchTokens.minimumTargetSize,
    fontFamily:
      "'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', 'Source Han Sans SC', sans-serif",
    fontSize: workbenchTokens.bodyFontSize,
    fontSizeSM: workbenchTokens.auxiliaryFontSize,
    lineHeight: workbenchTokens.lineHeight,
    motion: false,
  },
  components: {
    Button: {
      controlHeight: 48,
      controlHeightLG: 52,
      controlHeightSM: workbenchTokens.minimumTargetSize,
      fontWeight: 650,
    },
    Menu: {
      collapsedIconSize: 20,
      itemHeight: 48,
    },
    Pagination: {
      itemSize: workbenchTokens.minimumTargetSize,
      itemSizeSM: workbenchTokens.minimumTargetSize,
    },
    Table: {
      cellFontSize: workbenchTokens.bodyFontSize,
      cellFontSizeMD: workbenchTokens.bodyFontSize,
      cellFontSizeSM: workbenchTokens.auxiliaryFontSize,
      cellPaddingBlock: 16,
      cellPaddingInline: 16,
      headerBg: 'rgba(22, 29, 46, 0.045)',
      headerColor: workbenchTokens.colors.text,
    },
  },
};
