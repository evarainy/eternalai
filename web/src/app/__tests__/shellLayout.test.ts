import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import {
  IDENTITY_FALLBACK_LINES,
  MINIMUM_TARGET_SIZE,
  USER_MENU_WIDTH,
  collapsedNavigationTargetWidth,
  singleLineTextWidth,
} from '../shellLayout';

function readSource(relativePath: string): string {
  return readFileSync(
    fileURLToPath(new URL(relativePath, import.meta.url)),
    'utf-8',
  );
}

describe('AppShell sizing after the 2026-11-11 topbar removal', () => {
  it('keeps every fail-closed identity line on a single line of the user menu', () => {
    expect(IDENTITY_FALLBACK_LINES.length).toBeGreaterThan(0);
    for (const line of IDENTITY_FALLBACK_LINES) {
      expect(singleLineTextWidth(line)).toBeLessThanOrEqual(USER_MENU_WIDTH);
    }
  });

  it('keeps collapsed navigation targets at or above the decided minimum size', () => {
    expect(collapsedNavigationTargetWidth()).toBeGreaterThanOrEqual(
      MINIMUM_TARGET_SIZE,
    );
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
