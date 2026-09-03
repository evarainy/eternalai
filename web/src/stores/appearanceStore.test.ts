import { beforeEach, describe, expect, it } from 'vitest';
import { BACKGROUND_PRESETS, DEFAULT_BACKGROUND_PRESET } from '../app/theme';
import { APPEARANCE_STORAGE_KEY, useAppearanceStore } from './appearanceStore';

describe('appearance background store', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAppearanceStore.setState({ background: DEFAULT_BACKGROUND_PRESET });
  });

  it('starts on the default preset', () => {
    expect(useAppearanceStore.getState().background).toBe(
      DEFAULT_BACKGROUND_PRESET,
    );
    expect(BACKGROUND_PRESETS).toContain(DEFAULT_BACKGROUND_PRESET);
  });

  it('persists every offered preset so the choice survives a reload', () => {
    for (const preset of BACKGROUND_PRESETS) {
      useAppearanceStore.getState().setBackground(preset);

      expect(useAppearanceStore.getState().background).toBe(preset);
      const stored = window.localStorage.getItem(APPEARANCE_STORAGE_KEY);
      expect(stored).not.toBeNull();
      expect(JSON.parse(stored ?? '{}')).toMatchObject({
        state: { background: preset },
      });
    }
  });

  it('falls back to the default when storage holds an unknown preset', () => {
    window.localStorage.setItem(
      APPEARANCE_STORAGE_KEY,
      JSON.stringify({ state: { background: 'bgZ' }, version: 0 }),
    );

    useAppearanceStore.persist.rehydrate();

    expect(useAppearanceStore.getState().background).toBe(
      DEFAULT_BACKGROUND_PRESET,
    );
  });
});
