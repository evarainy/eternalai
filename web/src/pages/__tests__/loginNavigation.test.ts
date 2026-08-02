import { describe, expect, it } from 'vitest';
import { getReturnPath } from '../loginNavigation';

const DEFAULT_RETURN_PATH = '/admin/registry';

describe('getReturnPath', () => {
  it.each([
    ['/admin/tasks', '/admin/tasks'],
    ['/chat', '/chat'],
  ])('allows the named protected path %s', (from, expected) => {
    expect(getReturnPath({ from })).toBe(expected);
  });

  const rejectedStates: Array<[string, unknown]> = [
    ['protocol-relative URL', { from: '//evil.com' }],
    ['backslash URL variant', { from: '/\\evil.com' }],
    ['absolute URL', { from: 'https://evil.com' }],
    ['chat prefix confusion', { from: '/chatevil' }],
    ['null state', null],
    ['non-object state', '/chat'],
    ['non-string from', { from: 123 }],
    ['unlisted path', { from: '/health' }],
  ];

  it.each(rejectedStates)('falls back for %s', (_label, state) => {
    expect(getReturnPath(state)).toBe(DEFAULT_RETURN_PATH);
  });
});
