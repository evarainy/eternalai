/* Thin Site projection over an exact Node. Native Playwright owns ARIA roles,
 * accessible names, visibility and enabled semantics. This code only classifies
 * registered coverage markers and allowlisted Site metadata. */
(node, config) => {
  if (!node || !node.isConnected) throw new Error('detached');
  const approved = (value, choices) => {
    if (typeof value !== 'string') return null;
    const clean = value.replace(/\s+/gu, ' ').trim();
    return clean && clean.length <= 256 && choices.includes(clean) ? clean : null;
  };
  if (config.kind === 'coverage') {
    const site = config.site;
    const visibleMatches = selector => {
      if (!selector) return 0;
      return Array.from(node.querySelectorAll(selector)).filter(element => {
        if (!element.isConnected || element.hidden ||
            element.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
        const style = element.ownerDocument.defaultView.getComputedStyle(element);
        return style.display !== 'none' &&
          style.visibility !== 'hidden' && style.visibility !== 'collapse' &&
          element.getClientRects().length > 0;
      }).length;
    };
    if (node.querySelectorAll('*').length > 4096)
      return {state: 'partial', reason: 'unobservable', trusted_empty: false, overflow: true};
    let state = 'partial', reason = 'unobservable';
    // Locator.get_by_role does not flatten nested Frame descendants. The Site
    // must register and observe those Frames separately before claiming a full
    // result for a region that contains one.
    if (node.querySelectorAll('iframe,frame').length) reason = 'unobservable';
    else if (visibleMatches(site.virtualizedSelector)) reason = 'virtualized';
    else if (visibleMatches(site.paginationSelector)) reason = 'pagination';
    else if (visibleMatches(site.completeSelector) === 1 && node.getAttribute('aria-busy') !== 'true') {
      state = 'complete'; reason = 'complete';
    }
    return {state, reason, trusted_empty: state === 'complete' && visibleMatches(site.emptySelector) === 1, overflow: false};
  }
  if (config.kind !== 'candidate') throw new Error('invalid_kind');
  const type = (node.getAttribute('type') || '').toLowerCase();
  const autocomplete = (node.getAttribute('autocomplete') || '').toLowerCase();
  const identifier = `${node.getAttribute('name') || ''} ${node.getAttribute('id') || ''}`.toLowerCase();
  if ((node.localName === 'input' || node.localName === 'textarea') &&
      (type === 'password' || /(?:password|token|secret|session|cookie|otp|one.time.code|auth)/u.test(identifier) ||
       /(?:password|one-time-code|cc-number|cc-csc)/u.test(autocomplete))) return null;
  const rawContext = node.getAttribute('data-browser-context');
  const context = rawContext === null ? [] : rawContext.split('|').map(value => approved(value, config.context));
  if (context.some(value => value === null)) return null;
  const rawRow = node.getAttribute('data-browser-row-label');
  const rawColumn = node.getAttribute('data-browser-column-label');
  const row_label = rawRow === null ? null : approved(rawRow, config.rowLabels);
  const column_label = rawColumn === null ? null : approved(rawColumn, config.columnLabels);
  if ((rawRow !== null && row_label === null) || (rawColumn !== null && column_label === null)) return null;
  const value_state = (node.localName === 'input' || node.localName === 'textarea' || node.localName === 'select')
    ? (node.value ? 'nonempty' : 'empty') : 'not_applicable';
  return {context, row_label, column_label, value_state};
}
