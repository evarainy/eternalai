import type { ThemeConfig } from 'antd';

/**
 * 工作台视觉基座：墨、纸、印（2026-11-13 雨爷确认 v7 样板）。
 *
 * - 墨：左侧窄栏与外框，实色，只放导航；皮肤只换它的颜色。
 * - 纸：白色内容面，所有内容写在纸上。
 * - 印：朱红，只表示「超期 / 未读」和「用户亲手授权」，别处不用。
 *
 * 蓝色是唯一的操作色，蓝与朱红都不随皮肤变。
 */
export const SKINS = ['ink', 'rose', 'sage', 'graphite', 'light'] as const;

export type Skin = (typeof SKINS)[number];

export const DEFAULT_SKIN: Skin = 'ink';

/** 面向用户的皮肤名称。 */
export const SKIN_LABELS: Record<Skin, string> = {
  ink: '墨',
  rose: '樱',
  sage: '竹',
  graphite: '玄',
  light: '素',
};

/** 设置菜单里皮肤圆点的颜色，取各皮肤外框的代表色。 */
export const SKIN_SWATCHES: Record<Skin, string> = {
  ink: '#101a30',
  rose: '#f3cbd7',
  sage: '#c6dfd1',
  graphite: '#1e2026',
  light: '#dde3ee',
};

export const FONT_SIZES = ['m', 'l'] as const;

export type FontSize = (typeof FONT_SIZES)[number];

export const DEFAULT_FONT_SIZE: FontSize = 'm';

export const FONT_SIZE_LABELS: Record<FontSize, string> = { m: '标准', l: '大' };

/** 根字号。界面尺寸一律用 rem，跟着这两档整体缩放；即时消息区内部用 px，不跟随。 */
export const FONT_SIZE_PX: Record<FontSize, number> = { m: 15, l: 17 };

export const CLOCK_STYLES = ['digit', 'dial'] as const;

export type ClockStyle = (typeof CLOCK_STYLES)[number];

export const CLOCK_STYLE_LABELS: Record<ClockStyle, string> = { digit: '数字', dial: '表盘' };

export const FX_MODES = ['full', 'lite'] as const;

export type FxMode = (typeof FX_MODES)[number];

export const FX_MODE_LABELS: Record<FxMode, string> = { full: '标准', lite: '精简' };

/**
 * 单屏允许携带 `backdrop-filter` 的元素数量上限（2026-09-02 实测边界，沿用）。
 *
 * 新外壳只有四处毛玻璃，而且都是浮在内容之上的那一层：问 AI 浮钮、问 AI 面板、设置菜单、搜索面板。
 * 内容面与卡片一律实色。`web/src/app/__tests__/blurBudget.test.tsx` 在真实渲染结果上执行该上限。
 */
export const BLUR_LAYER_BUDGET = 6;

/**
 * antd 默认会给「恰好两个汉字」的按钮文字中间插一个真空格（`发布` → `发 布`），这会改掉读屏软件念出来的
 * 按钮名。一律关掉。
 */
export const WORKBENCH_BUTTON_CONFIG = { autoInsertSpace: false } as const;

export const workbenchTokens = {
  bodyFontSize: FONT_SIZE_PX.m,
  lineHeight: 1.6,
  /** 标准字号下控件高度（2.4rem）。 */
  controlHeight: 36,
  fontFamily:
    "'Noto Sans SC', 'Noto Sans CJK SC', 'Source Han Sans SC', 'HarmonyOS Sans SC', 'Microsoft YaHei UI', 'Microsoft YaHei', sans-serif",
  colors: {
    paper: '#ffffff',
    paperSoft: '#f5f6f8',
    paperMuted: '#eceef2',
    line: 'rgba(16, 26, 46, 0.08)',
    lineStrong: 'rgba(16, 26, 46, 0.15)',
    fieldBorder: 'rgba(16, 26, 46, 0.22)',
    text: '#121a2b',
    textSecondary: '#465066',
    textTertiary: '#657086',
    /** 唯一的操作色。 */
    primary: '#2450c8',
    primarySoft: '#eaf0ff',
    /** 焦点环与操作色同值。 */
    focus: '#2450c8',
    /** 印：超期、未读、亲手授权。 */
    seal: '#c93a26',
    sealSoft: '#fdefeb',
    success: '#1a7f45',
    warning: '#9a5b06',
  },
} as const;

/**
 * antd 主题。字号两档与动效开关由外观设置决定，所以是函数而不是常量。
 *
 * `motion: false` 用于「动效：精简」——老电脑、远程桌面和没有显卡加速的机器。
 */
export function buildWorkbenchTheme(size: FontSize, fx: FxMode): ThemeConfig {
  const fontSize = FONT_SIZE_PX[size];
  const scale = fontSize / FONT_SIZE_PX.m;
  const px = (value: number) => Math.round(value * scale);
  const { colors } = workbenchTokens;
  return {
    token: {
      borderRadius: 10,
      borderRadiusLG: 16,
      borderRadiusSM: 6,
      colorBgBase: colors.paper,
      colorBgContainer: colors.paper,
      colorBgElevated: colors.paper,
      colorBgLayout: 'transparent',
      colorBorder: colors.fieldBorder,
      colorBorderSecondary: colors.line,
      colorError: colors.seal,
      colorLink: colors.primary,
      colorPrimary: colors.primary,
      colorSuccess: colors.success,
      colorText: colors.text,
      colorTextPlaceholder: colors.textTertiary,
      colorTextSecondary: colors.textSecondary,
      colorTextTertiary: colors.textTertiary,
      colorWarning: colors.warning,
      controlHeight: px(workbenchTokens.controlHeight),
      controlHeightLG: px(40),
      controlHeightSM: px(32),
      controlOutline: 'rgba(36, 80, 200, 0.16)',
      controlOutlineWidth: 3,
      fontFamily: workbenchTokens.fontFamily,
      fontSize,
      fontSizeLG: px(16),
      fontSizeSM: px(13),
      lineHeight: workbenchTokens.lineHeight,
      motion: fx === 'full',
    },
    components: {
      Button: {
        fontWeight: 600,
        primaryShadow: 'inset 0 1px 0 rgba(255, 255, 255, 0.22), 0 8px 16px -8px rgba(36, 80, 200, 0.7)',
        defaultShadow: 'none',
        dangerShadow: 'none',
      },
      Modal: {
        borderRadiusLG: 20,
      },
      Table: {
        cellFontSize: fontSize,
        cellFontSizeMD: fontSize,
        cellFontSizeSM: px(14),
        cellPaddingBlock: px(11),
        cellPaddingInline: px(14),
        headerBg: colors.paperSoft,
        headerColor: colors.textTertiary,
        headerSplitColor: 'transparent',
        rowHoverBg: colors.paperSoft,
      },
    },
  };
}

/** 默认外观（标准字号、标准动效）下的主题，供不关心外观设置的调用方与测试使用。 */
export const workbenchTheme: ThemeConfig = buildWorkbenchTheme(DEFAULT_FONT_SIZE, 'full');
