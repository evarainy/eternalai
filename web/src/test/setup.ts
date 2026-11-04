import '@testing-library/jest-dom/vitest';
import { beforeEach, vi } from 'vitest';
import { completeIMLogout } from '../features/messages/hostBridge';

beforeEach(() => {
  completeIMLogout();
  // jsdom has no Web Locks; existing login/renew/logout cases use a granted lock.
  Object.defineProperty(navigator, 'locks', {
    configurable: true,
    value: { request: async (name: string, _options: LockOptions, callback: LockGrantedCallback) =>
      callback({ name, mode: 'exclusive' } as Lock) },
  });
});

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

class ResizeObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}

vi.stubGlobal('ResizeObserver', ResizeObserverMock);

const getComputedStyle = window.getComputedStyle.bind(window);
window.getComputedStyle = (element) => getComputedStyle(element);
