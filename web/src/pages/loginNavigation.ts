const DEFAULT_RETURN_PATH = '/admin/registry';

export function getReturnPath(state: unknown): string {
  if (
    typeof state === 'object' &&
    state !== null &&
    'from' in state &&
    typeof state.from === 'string' &&
    (state.from === '/chat' || state.from.startsWith('/admin/'))
  ) {
    return state.from;
  }
  return DEFAULT_RETURN_PATH;
}
