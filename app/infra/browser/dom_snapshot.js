/* A fixed, bounded projection. The caller supplies only a registered region and
 * frozen allowlists. Raw DOM text and input values never leave this function. */
(region, config) => {
  const allowed = config.policy;
  const site = config.site;
  const roleSet = new Set(allowed.roles);
  const names = new Set(allowed.names);
  const contexts = new Set(allowed.context);
  const rows = new Set(allowed.rowLabels);
  const columns = new Set(allowed.columnLabels);
  const maxCandidates = allowed.maximumCandidates;
  const maxScanned = 4096;

  const normalized = (value) => {
    if (typeof value !== "string") return null;
    const clean = value.replace(/\s+/gu, " ").trim();
    return clean && clean.length <= 256 ? clean : null;
  };
  const approved = (value, choices) => {
    const clean = normalized(value);
    return clean && choices.has(clean) ? clean : null;
  };
  const isVisible = (element) => {
    if (!element || !element.isConnected || element.hidden || element.closest("[hidden], [inert], [aria-hidden='true']")) return false;
    const style = element.ownerDocument.defaultView.getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden" && style.visibility !== "collapse" && element.getClientRects().length > 0;
  };
  const matches = (selector) => {
    if (!selector) return [];
    return Array.from(region.querySelectorAll(selector)).filter(isVisible);
  };
  const marker = (selector) => matches(selector).length === 1;
  const roleOf = (element) => {
    const explicit = normalized(element.getAttribute("role"));
    if (explicit) return explicit;
    const tag = element.localName;
    if (tag === "button") return "button";
    if (tag === "a" && element.hasAttribute("href")) return "link";
    if (tag === "select") return "combobox";
    if (tag === "option") return "option";
    if (tag === "tr") return "row";
    if (tag === "input") {
      return ["text", "search", "email", "tel", "url", "number", ""].includes(element.type || "") ? "textbox" : null;
    }
    if (tag === "textarea") return "textbox";
    return null;
  };
  const isSensitiveInput = (element) => {
    if (element.localName !== "input" && element.localName !== "textarea") return false;
    const type = (element.getAttribute("type") || "").toLowerCase();
    const autocomplete = (element.getAttribute("autocomplete") || "").toLowerCase();
    const identifier = `${element.getAttribute("name") || ""} ${element.getAttribute("id") || ""}`.toLowerCase();
    return type === "password" || /(?:password|token|secret|session|cookie|otp|one.time.code|auth)/u.test(identifier) || /(?:password|one-time-code|cc-number|cc-csc)/u.test(autocomplete);
  };
  const rawName = (element) => {
    const aria = element.getAttribute("aria-label");
    if (aria !== null) return aria;
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const ids = labelledBy.split(/\s+/u).filter(Boolean);
      if (ids.length > 4) return null;
      const labels = ids.map((id) => element.ownerDocument.getElementById(id));
      if (labels.some((label) => !label || !isVisible(label))) return null;
      return labels.map((label) => label.textContent || "").join(" ");
    }
    if (element.labels && element.labels.length === 1 && isVisible(element.labels[0])) return element.labels[0].textContent;
    if (["button", "a", "option", "th"].includes(element.localName)) return element.textContent;
    return element.getAttribute("title");
  };

  let coverage = { state: "partial", reason: "unobservable", trusted_empty: false };
  if (matches(site.virtualizedSelector).length > 0) coverage = { state: "partial", reason: "virtualized", trusted_empty: false };
  else if (matches(site.paginationSelector).length > 0) coverage = { state: "partial", reason: "pagination", trusted_empty: false };
  else if (marker(site.completeSelector) && region.getAttribute("aria-busy") !== "true") coverage = { state: "complete", reason: "complete", trusted_empty: false };

  const nodes = [];
  const candidates = [];
  const scanned = region.querySelectorAll("*");
  if (scanned.length > maxScanned) return { metadata: { coverage: { state: "partial", reason: "unobservable", trusted_empty: false }, candidates: [], overflow: true }, nodes: [] };

  for (const element of scanned) {
    const role = roleOf(element);
    if (!roleSet.has(role) || !isVisible(element) || isSensitiveInput(element)) continue;
    const raw = rawName(element);
    const name = approved(raw, names);
    if (normalized(raw) && !name) continue;
    const rawContext = element.getAttribute("data-browser-context");
    const context = rawContext ? rawContext.split("|").map((entry) => approved(entry, contexts)) : [];
    if (context.some((entry) => entry === null)) continue;
    const rawRow = element.getAttribute("data-browser-row-label");
    const rawColumn = element.getAttribute("data-browser-column-label");
    const rowLabel = rawRow === null ? null : approved(rawRow, rows);
    const columnLabel = rawColumn === null ? null : approved(rawColumn, columns);
    if ((rawRow !== null && !rowLabel) || (rawColumn !== null && !columnLabel)) continue;
    const enabled = !element.matches(":disabled") && element.getAttribute("aria-disabled") !== "true" && !element.closest("[aria-disabled='true']");
    let valueState = "not_applicable";
    if (role === "textbox" || role === "combobox") {
      // Only a boolean leaves the browser; the value is never serialized.
      valueState = element.value ? "nonempty" : "empty";
    }
    nodes.push(element);
    candidates.push({ role, name, context, row_label: rowLabel, column_label: columnLabel, value_state: valueState, visible: true, enabled });
    if (candidates.length > maxCandidates) return { metadata: { coverage, candidates: [], overflow: true }, nodes: [] };
  }

  coverage.trusted_empty = coverage.state === "complete" && candidates.length === 0 && marker(site.emptySelector);
  return { metadata: { coverage, candidates, overflow: false }, nodes };
}
