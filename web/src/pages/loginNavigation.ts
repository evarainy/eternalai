const DEFAULT_RETURN_PATH = '/';
const NAMED_PROTECTED_PATHS = new Set(['/', '/chat', '/search', '/work-objects']);

export function getReturnPath(state: unknown): string {
  const from =
    typeof state === 'object' &&
    state !== null &&
    'from' in state &&
    typeof state.from === 'string'
      ? state.from
      : null;
  const queryStart = from?.indexOf('?') ?? -1;
  const pathname =
    from === null || queryStart === -1 ? from : from.slice(0, queryStart);

  if (
    from !== null &&
    pathname !== null &&
    (NAMED_PROTECTED_PATHS.has(pathname) || pathname.startsWith('/admin/'))
  ) {
    return from;
  }
  return DEFAULT_RETURN_PATH;
}
