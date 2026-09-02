import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  IDENTITY_UNAVAILABLE_LINES,
  LAYOUT_BASELINE_WIDTH,
  MINIMUM_TARGET_SIZE,
  SIDEBAR_COLLAPSED_WIDTH,
  SIDEBAR_EXPANDED_WIDTH,
  TOPBAR_ELEMENT_WIDTHS,
  TOPBAR_IDENTITY_WIDTH,
  collapsedNavigationTargetWidth,
  singleLineTextWidth,
  topbarAvailableWidth,
  topbarRequiredWidth,
} from '../shellLayout';

function readSource(relativePath: string): string {
  return readFileSync(
    fileURLToPath(new URL(relativePath, import.meta.url)),
    'utf-8',
  );
}

describe('AppShell 1280px layout budget', () => {
  it('fits the six fixed topbar elements next to the expanded sidebar', () => {
    expect(TOPBAR_ELEMENT_WIDTHS).toHaveLength(6);
    expect(topbarRequiredWidth()).toBeLessThanOrEqual(
      topbarAvailableWidth(SIDEBAR_EXPANDED_WIDTH, LAYOUT_BASELINE_WIDTH),
    );
  });

  it('keeps more room once the sidebar is collapsed to the icon rail', () => {
    expect(topbarAvailableWidth(SIDEBAR_COLLAPSED_WIDTH)).toBeGreaterThan(
      topbarAvailableWidth(SIDEBAR_EXPANDED_WIDTH),
    );
    expect(topbarRequiredWidth()).toBeLessThanOrEqual(
      topbarAvailableWidth(SIDEBAR_COLLAPSED_WIDTH),
    );
  });

  it('keeps every fail-closed identity line on a single line of its slot', () => {
    expect(IDENTITY_UNAVAILABLE_LINES.length).toBeGreaterThan(0);
    for (const line of IDENTITY_UNAVAILABLE_LINES) {
      expect(singleLineTextWidth(line)).toBeLessThanOrEqual(TOPBAR_IDENTITY_WIDTH);
    }
  });

  it('keeps collapsed navigation targets at or above the decided minimum size', () => {
    expect(collapsedNavigationTargetWidth()).toBeGreaterThanOrEqual(
      MINIMUM_TARGET_SIZE,
    );
    for (const width of TOPBAR_ELEMENT_WIDTHS) {
      expect(width).toBeGreaterThanOrEqual(MINIMUM_TARGET_SIZE);
    }
  });
});

describe('AppShell and Dock stylesheets follow the 2026-09-02 floating-panel decision', () => {
  it('drops the content-offset grid that the pinned mode used to trigger', () => {
    const shellCss = readSource('../AppShell.module.css');

    expect(shellCss).not.toContain('data-dock-offset');
  });

  it('positions both Dock modes as fixed overlays instead of a sticky column', () => {
    const dockCss = readSource('../AIDock.module.css');
    const dockBlock = /\.dock\s*\{([^}]*)\}/.exec(dockCss);
    const drawerBlock = /\.drawer\s*\{([^}]*)\}/.exec(dockCss);
    const pinnedBlock = /\.pinned\s*\{([^}]*)\}/.exec(dockCss);

    expect(dockBlock).not.toBeNull();
    expect(drawerBlock).not.toBeNull();
    expect(pinnedBlock).not.toBeNull();
    expect(dockBlock?.[1]).toContain('position: fixed');
    expect(drawerBlock?.[1]).not.toContain('position:');
    expect(pinnedBlock?.[1]).not.toContain('position:');
    expect(dockCss).not.toContain('position: sticky');
  });
});
