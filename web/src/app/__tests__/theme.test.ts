import { describe, expect, it } from 'vitest';
import { workbenchTheme, workbenchTokens } from '../theme';

function luminance(hex: string): number {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)
    ?.map((channel) => Number.parseInt(channel, 16) / 255);
  if (!channels || channels.length !== 3) {
    throw new Error(`invalid_hex_color:${hex}`);
  }
  const linear = channels.map((channel) =>
    channel <= 0.03928
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4,
  );
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

describe('low-digital-literacy design tokens', () => {
  it('fixes body, auxiliary, line-height, and target-size constraints in one token source', () => {
    expect(workbenchTokens.bodyFontSize).toBe(19);
    expect(workbenchTokens.auxiliaryFontSize).toBeGreaterThanOrEqual(16);
    expect(workbenchTokens.lineHeight).toBeGreaterThanOrEqual(1.3);
    expect(workbenchTokens.lineHeight).toBeLessThanOrEqual(1.5);
    expect(workbenchTokens.minimumTargetSize).toBeGreaterThanOrEqual(44);
    expect(workbenchTheme.token?.fontSize).toBe(workbenchTokens.bodyFontSize);
    expect(workbenchTheme.token?.controlHeightSM).toBeGreaterThanOrEqual(44);
  });

  it.each([
    ['正文', workbenchTokens.colors.text],
    ['辅助文字', workbenchTokens.colors.textSecondary],
    ['输入提示', workbenchTokens.colors.placeholder],
    ['一般操作', workbenchTokens.colors.primary],
  ])('%s color has at least 4.5:1 contrast on the reading surface', (_name, color) => {
    expect(contrastRatio(color, workbenchTokens.colors.surface)).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps primary text readable on the explicit primary-soft background', () => {
    expect(contrastRatio(workbenchTokens.colors.primary, '#e7f0fb')).toBeGreaterThanOrEqual(
      4.5,
    );
  });
});
