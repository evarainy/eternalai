const DEFAULT_RETURN_PATH = '/';
const NAMED_PROTECTED_PATHS = new Set(['/', '/chat', '/search', '/work-objects']);

export function getReturnPath(state: unknown): string {
  if (
    typeof state === 'object' &&
    state !== null &&
    'from' in state &&
    typeof state.from === 'string' &&
    (NAMED_PROTECTED_PATHS.has(state.from) || state.from.startsWith('/admin/'))
  ) {
    return state.from;
  }
  return DEFAULT_RETURN_PATH;
}
