/** Explicit 30-character whitespace contract; ordinary lower, without locale.
 * Special-script casing is not guaranteed equivalent to PostgreSQL.
 */
export type SearchQuery = string | null;

const SEARCH_WHITESPACE =
  '\u0009\u000a\u000b\u000c\u000d\u001c\u001d\u001e\u001f\u0020' +
  '\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006' +
  '\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff';
const whitespace = new RegExp(`[${SEARCH_WHITESPACE}]+`, 'gu');

export function normalizeSearchValue(value: string): string {
  return value.replace(whitespace, ' ').replace(/^ +| +$/gu, '').toLowerCase();
}

export function normalizeSearchQuery(value: SearchQuery): SearchQuery {
  return value === null ? null : normalizeSearchValue(value) || null;
}
