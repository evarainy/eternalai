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

/*
 * 定稿画板 `_scratch/design/glass/*.dc.html` **共用样式表**里实测在用的全部字号。画板不进仓库
 * （`_scratch/` 是忽略目录），所以这里抄成常量，改动时以画板为准。
 *
 * **适用面（2026-09-04 雨爷裁定后收窄）**：这个闭集只管**正文、辅助、说明**三档字号令牌，也就是
 * 下面那条断言点名的那三枚。它**不管展示级标题**——雨爷原话「`/chat` 欢迎语 28px 不要被限制死，
 * 要美观好看」。收窄的理由不是为了让谁过测：这个闭集是从画板的共用样式表抽的，**漏掉了画板里的
 * 内联覆盖**（`Chat.dc.html` 的欢迎标题内联值就是 28px，本就不在集内），拿它去管展示级标题从一开始
 * 就是越界。`/chat` 欢迎语现值由 `ChatPage.test.tsx` 单独钉住下限，不是没人管。
 */
const CANVAS_FONT_SIZES: readonly number[] = [14, 15, 16, 17, 18, 19, 21, 34];

describe('low-digital-literacy design tokens', () => {
  /*
   * 2026-09-04 雨爷推翻了「正文 19px / 辅助 ≥16px」的事实前提：2026-08-27 那条裁决的依据是「用户
   * 年龄层偏大」，实际用户都是年轻人。**尺寸下限口径就此作废**，现役口径是「照搬设计稿实测值」。
   *
   * 因此这里把原来的 `>= 16` / `>= 14` / `<= 1.45` 三条下限换成**等值 + 画板闭集成员**：
   * - 把任一字号改成画板上没有的值（例如为了「更好读」提到 20px），闭集断言变红；
   * - 把它改成画板上另一个合法值（例如 16→17），等值断言变红，逼着改动人回来对画板。
   * 下限写法两种都放过，正是它让上一轮把画板的 14px 改成 16px 而没有任何门禁拦住。
   *
   * 行高同理：画板 `.root` 就是 1.45，不是一个区间。
   */
  it('fixes body, auxiliary, line-height, and target-size constraints in one token source', () => {
    expect(workbenchTokens.bodyFontSize).toBe(19);
    expect(workbenchTokens.auxiliaryFontSize).toBe(16);
    expect(workbenchTokens.captionFontSize).toBe(14);
    expect(CANVAS_FONT_SIZES).toContain(workbenchTokens.bodyFontSize);
    expect(CANVAS_FONT_SIZES).toContain(workbenchTokens.auxiliaryFontSize);
    expect(CANVAS_FONT_SIZES).toContain(workbenchTokens.captionFontSize);
    expect(workbenchTokens.lineHeight).toBe(1.45);
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

  /*
   * 2026-09-04：焦点环曾经是暖纸色系遗留的橙色 `#b44b00`——它自己是过 3:1 的，所以上面那条对比度守卫
   * 一路放行，直到雨爷实机走查才看见「点一下输入框弹一圈橙」。对比度达标不等于配色属于现役色板，所以
   * 这里再钉一条：焦点环必须就是主题色本身。橙色若被改回来，这条会打红。
   */
  it('draws the focus ring in the theme colour, not the retired warm-paper orange', () => {
    expect(workbenchTokens.colors.focus).toBe(workbenchTokens.colors.primary);
    expect(workbenchTokens.colors.focus).not.toBe('#b44b00');
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
