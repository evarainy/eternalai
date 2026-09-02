import { beforeEach, describe, expect, it } from 'vitest';
import {
  NAVIGATION_COLLAPSE_STORAGE_KEY,
  useNavigationStore,
} from './navigationStore';

describe('navigation collapse store', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useNavigationStore.setState({ collapsed: false });
  });

  it('starts expanded', () => {
    expect(useNavigationStore.getState().collapsed).toBe(false);
  });

  it('persists the collapsed choice so it survives a reload', () => {
    useNavigationStore.getState().toggleCollapsed();

    expect(useNavigationStore.getState().collapsed).toBe(true);
    const stored = window.localStorage.getItem(NAVIGATION_COLLAPSE_STORAGE_KEY);
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored ?? '{}')).toMatchObject({
      state: { collapsed: true },
    });
  });

  it('persists the expanded choice again after toggling back', () => {
    useNavigationStore.getState().setCollapsed(true);
    useNavigationStore.getState().setCollapsed(false);

    const stored = window.localStorage.getItem(NAVIGATION_COLLAPSE_STORAGE_KEY);
    expect(JSON.parse(stored ?? '{}')).toMatchObject({
      state: { collapsed: false },
    });
  });
});
