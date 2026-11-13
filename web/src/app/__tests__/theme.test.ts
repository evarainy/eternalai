import { describe, expect, it } from 'vitest';
import {
  BLUR_LAYER_BUDGET,
  DEFAULT_SKIN,
  FONT_SIZES,
  FONT_SIZE_PX,
  SKINS,
  SKIN_LABELS,
  buildWorkbenchTheme,
  workbenchTheme,
  workbenchTokens,
} from '../theme';

function channels(hex: string): [number, number, number] {
  const parsed = hex
    .slice(1)
    .match(/.{2}/g)
    ?.map((channel) => Number.parseInt(channel, 16));
  if (!parsed || parsed.length !== 3) {
    throw new Error(`invalid_hex_color:${hex}`);
  }
  const [red, green, blue] = parsed;
  if (red === undefined || green === undefined || blue === undefined) {
    throw new Error(`invalid_hex_color:${hex}`);
  }
  return [red, green, blue];
}

function luminance(hex: string): number {
  const linear = channels(hex).map((channel) => {
    const scaled = channel / 255;
    return scaled <= 0.03928
      ? scaled / 12.92
      : ((scaled + 0.055) / 1.055) ** 2.4;
  });
  const [red, green, blue] = linear;
  if (red === undefined || green === undefined || blue === undefined) {
    throw new Error(`invalid_hex_color:${hex}`);
  }
  return red * 0.2126 + green * 0.7152 + blue * 0.0722;
}

function contrastRatio(foreground: string, background: string): number {
  const first = luminance(foreground);
  const second = luminance(background);
  const lighter = Math.max(first, second);
  const darker = Math.min(first, second);
  return (lighter + 0.05) / (darker + 0.05);
}

const READING_SURFACES: readonly [string, string][] = [
  ['纸面', workbenchTokens.colors.paper],
  ['浅色纸面', workbenchTokens.colors.paperSoft],
];

const TEXT_COLORS: readonly [string, string][] = [
  ['正文', workbenchTokens.colors.text],
  ['辅助文字', workbenchTokens.colors.textSecondary],
  ['说明与输入提示', workbenchTokens.colors.textTertiary],
  ['语义强调（链接 / 提示）', workbenchTokens.colors.primary],
  ['语义 · 逾期', workbenchTokens.colors.seal],
  ['语义 · 警示', workbenchTokens.colors.warning],
  ['语义 · 正常', workbenchTokens.colors.success],
];

describe('墨、纸、印 design tokens', () => {
  it('keeps font sizes and control heights in sync with the selected root size', () => {
    expect([...FONT_SIZES]).toEqual(['m', 'l']);
    expect(FONT_SIZE_PX).toEqual({ m: 15, l: 17 });
    expect(workbenchTokens.bodyFontSize).toBe(15);
    expect(workbenchTokens.lineHeight).toBe(1.6);
    expect(workbenchTokens.controlHeight).toBe(36);
    for (const size of FONT_SIZES) {
      const theme = buildWorkbenchTheme(size, 'full');
      expect(theme.token?.fontSize).toBe(FONT_SIZE_PX[size]);
      expect(theme.token?.fontSizeSM).toBe(Math.round(13 * FONT_SIZE_PX[size] / 15));
      expect(theme.token?.lineHeight).toBe(workbenchTokens.lineHeight);
      expect(theme.token?.controlHeight).toBe(Math.round(36 * FONT_SIZE_PX[size] / 15));
      expect(theme.token?.controlHeightSM).toBe(Math.round(32 * FONT_SIZE_PX[size] / 15));
      expect(theme.token?.controlHeightLG).toBe(Math.round(40 * FONT_SIZE_PX[size] / 15));
    }
  });

  it('drops the warm-paper palette and the serif heading family', () => {
    const paletteValues = Object.values(workbenchTokens.colors).map((value) => value.toLowerCase());
    for (const retired of ['#f4f1e8', '#fffdf8', '#e7e3d8', '#1f4f8a']) {
      expect(paletteValues).not.toContain(retired);
    }
    expect(workbenchTheme.token?.fontFamily).toMatch(/sans-serif$/);
    expect(workbenchTheme.token?.fontFamily).not.toMatch(/Zhongsong/i);
    expect(workbenchTheme.token?.fontFamily).not.toMatch(/Noto Serif|Han Serif/i);
  });
});

describe('paper reading surfaces', () => {
  it.each(
    READING_SURFACES.flatMap(([surfaceName, surface]) =>
      TEXT_COLORS.map(([colorName, color]) => [`${colorName} on ${surfaceName}`, color, surface] as const),
    ),
  )('%s reaches at least 4.5:1', (_name, color, surface) => {
    expect(contrastRatio(color, surface)).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps the focus ring above the 3:1 non-text threshold on both surfaces', () => {
    for (const [, surface] of READING_SURFACES) {
      expect(contrastRatio(workbenchTokens.colors.focus, surface)).toBeGreaterThanOrEqual(3);
    }
  });

  it('draws the focus ring in the theme colour, not the retired warm-paper orange', () => {
    expect(workbenchTokens.colors.focus).toBe(workbenchTokens.colors.primary);
    expect(workbenchTokens.colors.focus).not.toBe('#b44b00');
  });

  it('keeps body-bearing panels opaque', () => {
    expect(workbenchTokens.colors.paper).toBe('#ffffff');
    expect(workbenchTheme.token?.colorBgContainer).toBe(workbenchTokens.colors.paper);
    expect(workbenchTheme.token?.colorBgElevated).toBe(workbenchTokens.colors.paper);
  });
});

describe('skins', () => {
  it('offers exactly five switchable skins with plain-language names', () => {
    expect([...SKINS]).toEqual(['ink', 'rose', 'sage', 'graphite', 'light']);
    expect(DEFAULT_SKIN).toBe('ink');
    expect(SKINS.map((skin) => SKIN_LABELS[skin])).toEqual(['墨', '樱', '竹', '玄', '素']);
  });

  it('holds the decided blur-layer budget', () => {
    expect(BLUR_LAYER_BUDGET).toBe(6);
  });
});
