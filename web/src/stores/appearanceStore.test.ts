import { beforeEach, describe, expect, it } from 'vitest';
import { SKINS, DEFAULT_SKIN } from '../app/theme';
import { APPEARANCE_STORAGE_KEY, useAppearanceStore } from './appearanceStore';

describe('appearance skin store', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAppearanceStore.setState({ skin: DEFAULT_SKIN, size: 'm', clock: 'digit', fx: null });
  });

  it('starts on the default skin', () => {
    expect(useAppearanceStore.getState().skin).toBe(
      DEFAULT_SKIN,
    );
    expect(SKINS).toContain(DEFAULT_SKIN);
  });

  it('persists every offered skin so the choice survives a reload', () => {
    for (const skin of SKINS) {
      useAppearanceStore.getState().setSkin(skin);

      expect(useAppearanceStore.getState().skin).toBe(skin);
      const stored = window.localStorage.getItem(APPEARANCE_STORAGE_KEY);
      expect(stored).not.toBeNull();
      expect(JSON.parse(stored ?? '{}')).toMatchObject({
        state: { skin },
      });
    }
  });

  it('falls back to the default when storage holds an unknown skin', () => {
    window.localStorage.setItem(
      APPEARANCE_STORAGE_KEY,
      JSON.stringify({ state: { skin: 'unknown' }, version: 0 }),
    );

    useAppearanceStore.persist.rehydrate();

    expect(useAppearanceStore.getState().skin).toBe(
      DEFAULT_SKIN,
    );
  });
});
