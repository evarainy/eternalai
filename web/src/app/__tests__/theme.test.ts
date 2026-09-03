import { describe, expect, it } from 'vitest';
import {
  BACKGROUND_PRESETS,
  BACKGROUND_PRESET_LABELS,
  BLUR_LAYER_BUDGET,
  DEFAULT_BACKGROUND_PRESET,
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

function alphaOf(fill: string): number {
  const matched = /rgba\(\s*255,\s*255,\s*255,\s*([\d.]+)\s*\)/.exec(fill);
  if (matched?.[1] === undefined) {
    throw new Error(`unsupported_fill:${fill}`);
  }
  return Number.parseFloat(matched[1]);
}

/** 把一层白色半透明面按 source-over 合成到底色上。 */
function compositeWhiteOver(fill: string, backdrop: string): string {
  const alpha = alphaOf(fill);
  const composed = channels(backdrop).map((channel) =>
    Math.round(alpha * 255 + (1 - alpha) * channel),
  );
  return `#${composed
    .map((channel) => channel.toString(16).padStart(2, '0'))
    .join('')}`;
}

const READING_SURFACES: readonly [string, string][] = [
  ['内容面板', workbenchTokens.colors.surface],
  ['大面玻璃', workbenchTokens.colors.glassSurface],
];

const TEXT_COLORS: readonly [string, string][] = [
  ['正文', workbenchTokens.colors.text],
  ['辅助文字', workbenchTokens.colors.textSecondary],
  ['说明与输入提示', workbenchTokens.colors.placeholder],
  ['语义强调（链接 / 提示）', workbenchTokens.colors.primary],
  ['语义 · 逾期', workbenchTokens.colors.error],
  ['语义 · 警示', workbenchTokens.colors.warning],
  ['语义 · 正常', workbenchTokens.colors.success],
];

describe('low-digital-literacy design tokens', () => {
  it('fixes body, auxiliary, line-height, and target-size constraints in one token source', () => {
    expect(workbenchTokens.bodyFontSize).toBe(19);
    expect(workbenchTokens.auxiliaryFontSize).toBeGreaterThanOrEqual(16);
    expect(workbenchTokens.captionFontSize).toBeGreaterThanOrEqual(14);
    expect(workbenchTokens.lineHeight).toBeGreaterThanOrEqual(1.4);
    expect(workbenchTokens.lineHeight).toBeLessThanOrEqual(1.45);
    expect(workbenchTokens.minimumTargetSize).toBeGreaterThanOrEqual(44);
    expect(workbenchTheme.token?.fontSize).toBe(workbenchTokens.bodyFontSize);
    expect(workbenchTheme.token?.fontSizeSM).toBe(
      workbenchTokens.auxiliaryFontSize,
    );
    expect(workbenchTheme.token?.controlHeightSM).toBeGreaterThanOrEqual(44);
    expect(workbenchTheme.token?.controlHeightLG).toBeGreaterThanOrEqual(52);
  });

  it('drops the warm-paper palette and the serif heading family', () => {
    const paletteValues = Object.values(workbenchTokens.colors).map((value) =>
      value.toLowerCase(),
    );
    for (const retired of ['#f4f1e8', '#fffdf8', '#e7e3d8', '#1f4f8a']) {
      expect(paletteValues).not.toContain(retired);
    }
    expect(workbenchTheme.token?.fontFamily).toMatch(/sans-serif$/);
    expect(workbenchTheme.token?.fontFamily).not.toMatch(/Zhongsong/i);
    expect(workbenchTheme.token?.fontFamily).not.toMatch(/Noto Serif|Han Serif/i);
  });
});

/*
 * 玻璃底的对比度不可预测，所以下面不按「看起来差不多的浅色」核，而是按 CSS 里真实的半透明面叠在最深
 * 底图上的合成结果核。合成关系本身也断言，令牌不能是拍脑袋写死的常数。
 */
describe('glass reading surfaces', () => {
  it('derives the reading surfaces from the real fills over the darkest background', () => {
    expect(
      compositeWhiteOver(
        workbenchTokens.panelFill,
        workbenchTokens.backgroundFloors.bgA,
      ),
    ).toBe(workbenchTokens.colors.surface);
    expect(
      compositeWhiteOver(
        workbenchTokens.glassFill,
        workbenchTokens.backgroundFloors.bgA,
      ),
    ).toBe(workbenchTokens.colors.glassSurface);
  });

  it('confirms the blue-violet preset really is the darkest of the three', () => {
    const floors = BACKGROUND_PRESETS.map((preset) =>
      luminance(
        compositeWhiteOver(
          workbenchTokens.glassFill,
          workbenchTokens.backgroundFloors[preset],
        ),
      ),
    );
    const darkest = Math.min(...floors);
    expect(floors[0]).toBe(darkest);
  });

  it.each(
    READING_SURFACES.flatMap(([surfaceName, surface]) =>
      TEXT_COLORS.map(
        ([colorName, color]) =>
          [`${colorName} on ${surfaceName}`, color, surface] as const,
      ),
    ),
  )('%s reaches at least 4.5:1', (_name, color, surface) => {
    expect(contrastRatio(color, surface)).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps the focus ring above the 3:1 non-text threshold on both surfaces', () => {
    for (const [, surface] of READING_SURFACES) {
      expect(
        contrastRatio(workbenchTokens.colors.focus, surface),
      ).toBeGreaterThanOrEqual(3);
    }
  });

  it('keeps body-bearing panels opaque enough and large glass translucent', () => {
    expect(alphaOf(workbenchTokens.panelFill)).toBeGreaterThanOrEqual(0.86);
    expect(alphaOf(workbenchTokens.glassFill)).toBeLessThan(
      alphaOf(workbenchTokens.panelFill),
    );
    expect(workbenchTheme.token?.colorBgContainer).toBe(
      workbenchTokens.panelFill,
    );
  });
});

describe('background presets', () => {
  it('offers exactly three switchable backgrounds with plain-language names', () => {
    expect([...BACKGROUND_PRESETS]).toEqual(['bgA', 'bgB', 'bgC']);
    expect(BACKGROUND_PRESETS).toContain(DEFAULT_BACKGROUND_PRESET);
    for (const preset of BACKGROUND_PRESETS) {
      expect(BACKGROUND_PRESET_LABELS[preset]).toMatch(/^[一-龥]+$/);
      expect(workbenchTokens.backgroundFloors[preset]).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it('holds the decided blur-layer budget', () => {
    expect(BLUR_LAYER_BUDGET).toBe(6);
  });
});
