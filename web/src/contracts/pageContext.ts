export const PAGE_CONTEXT_FIELD_NAMES = [
  'surface_id',
  'organization_scope',
  'work_object_refs',
  'source_refs',
  'filters',
  'selected_metric',
  'allowed_capabilities',
  'freshness',
  'visibility',
] as const;

const MAX_REFERENCES = 200;
const MAX_FILTERS = 32;
const MAX_CAPABILITIES = 64;
export const PAGE_CONTEXT_TIMESTAMP_MAX_LENGTH = 64;
export const PAGE_CONTEXT_TIMESTAMP_PATTERN = String.raw`^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,6})?Z$`;
const surfaceIdPattern = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/;
const identifierPattern = /^[A-Za-z0-9]+(?:[._:@/-][A-Za-z0-9]+)*$/;
const capabilityIdPattern = /^[a-z0-9]+(?:[._:-][a-z0-9]+)*$/;
const domMarkupPattern =
  /<!doctype\s+html|<\s*\/?\s*(?:html|body|form|input|script|table|div|main)\b/i;
export const PAGE_CONTEXT_SENSITIVE_VALUE_PATTERNS = [
  String.raw`(?<!\d)(?:\d{17}[\dXx]|\d{15})(?!\d)`,
  String.raw`bearer\s+\S+`,
  String.raw`\b[a-z][a-z0-9+.-]*://[^\s/@]+@`,
  String.raw`(?:authorization|session(?:[\s_-]?id)?|access[\s_-]?token|refresh[\s_-]?token|set[\s_-]?cookie|cookie|password|passwd|api[\s_-]?key|secret|client[\s_-]?secret|private[\s_-]?key|loginid|userpassword|oa[\s_-]?userid|userid)\s*[:=]\s*\S+`,
] as const;
const sensitiveValuePatterns = PAGE_CONTEXT_SENSITIVE_VALUE_PATTERNS.map(
  (pattern) => new RegExp(pattern, 'i'),
);
const timestampPattern = new RegExp(PAGE_CONTEXT_TIMESTAMP_PATTERN);

type PageVisibility = 'principal' | 'department' | 'organization';
type PageFilterOperator =
  | 'equals'
  | 'not_equals'
  | 'contains'
  | 'greater_than'
  | 'less_than';
type PageFilterValue = string | number | boolean;
type PageFreshnessState = 'unknown' | 'reported' | 'stale';

export interface OrganizationScope {
  readonly tenant_id: string;
  readonly organization_id: string | null;
  readonly department_id: string | null;
}

export interface WorkObjectReference {
  readonly work_object_id: string;
}

export interface SourceReference {
  readonly source_system: string;
  readonly source_ref: string;
}

export interface PageFilter {
  readonly field: string;
  readonly operator: PageFilterOperator;
  readonly value: PageFilterValue;
  readonly source: 'visible_control';
}

export interface PageFreshness {
  readonly state: PageFreshnessState;
  readonly observed_at: string | null;
}

/** Page-declared data only. It is never a backend authorization result. */
export interface PageContextDeclaration {
  readonly surface_id: string;
  readonly organization_scope: OrganizationScope | null;
  readonly work_object_refs: readonly WorkObjectReference[];
  readonly source_refs: readonly SourceReference[];
  readonly filters: readonly PageFilter[];
  readonly selected_metric: string | null;
  readonly allowed_capabilities: readonly string[];
  readonly freshness: PageFreshness;
  readonly visibility: PageVisibility;
}

export class PageContextValidationError extends Error {
  readonly code = 'invalid_page_context';

  constructor(path: string, reason: string) {
    super(`${path}: ${reason}`);
    this.name = 'PageContextValidationError';
  }
}

function fail(path: string, reason: string): never {
  throw new PageContextValidationError(path, reason);
}

function asRecord(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    return fail(path, 'must be an object');
  }
  return value as Record<string, unknown>;
}

function assertExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
  path: string,
) {
  const actual = Object.keys(value);
  const missing = expected.filter(
    (key) => !Object.prototype.hasOwnProperty.call(value, key),
  );
  const extra = actual.filter((key) => !expected.includes(key));
  if (missing.length > 0 || extra.length > 0) {
    fail(path, 'has missing or unrecognized fields');
  }
}

function safeText(value: unknown, path: string, maxLength: number): string {
  if (typeof value !== 'string' || value.length === 0 || value.length > maxLength) {
    return fail(path, `must be a non-empty string of at most ${maxLength} characters`);
  }
  if (
    [...value].some((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint <= 31 || codePoint === 127;
    })
  ) {
    return fail(path, 'must contain only printable single-line text');
  }
  if (domMarkupPattern.test(value)) {
    return fail(path, 'must not contain DOM markup');
  }
  if (sensitiveValuePatterns.some((pattern) => pattern.test(value))) {
    return fail(path, 'must not contain credential-like material');
  }
  return value;
}

function identifier(value: unknown, path: string): string {
  const parsed = safeText(value, path, 256);
  if (!identifierPattern.test(parsed)) {
    return fail(path, 'contains unsupported identifier characters');
  }
  return parsed;
}

function surfaceId(value: unknown): string {
  const parsed = safeText(value, 'surface_id', 80);
  if (!surfaceIdPattern.test(parsed)) {
    return fail('surface_id', 'must use lowercase slug form');
  }
  return parsed;
}

function capabilityId(value: unknown, path: string): string {
  const parsed = safeText(value, path, 120);
  if (!capabilityIdPattern.test(parsed)) {
    return fail(path, 'must use lowercase dotted slug form');
  }
  return parsed;
}

function arrayValue(value: unknown, path: string, maxLength: number): unknown[] {
  if (!Array.isArray(value) || value.length > maxLength) {
    return fail(path, `must be an array with at most ${maxLength} items`);
  }
  return value;
}

function optionalIdentifier(value: unknown, path: string): string | null {
  return value === null ? null : identifier(value, path);
}

function organizationScope(value: unknown): OrganizationScope | null {
  if (value === null) {
    return null;
  }
  const raw = asRecord(value, 'organization_scope');
  assertExactKeys(
    raw,
    ['tenant_id', 'organization_id', 'department_id'],
    'organization_scope',
  );
  const parsed = {
    tenant_id: identifier(raw.tenant_id, 'organization_scope.tenant_id'),
    organization_id: optionalIdentifier(
      raw.organization_id,
      'organization_scope.organization_id',
    ),
    department_id: optionalIdentifier(
      raw.department_id,
      'organization_scope.department_id',
    ),
  };
  if (parsed.department_id !== null && parsed.organization_id === null) {
    return fail('organization_scope', 'department_id requires organization_id');
  }
  return Object.freeze(parsed);
}

function workObjectRefs(value: unknown): readonly WorkObjectReference[] {
  const parsed = arrayValue(value, 'work_object_refs', MAX_REFERENCES).map(
    (item, index) => {
      const raw = asRecord(item, `work_object_refs[${index}]`);
      assertExactKeys(raw, ['work_object_id'], `work_object_refs[${index}]`);
      return Object.freeze({
        work_object_id: identifier(
          raw.work_object_id,
          `work_object_refs[${index}].work_object_id`,
        ),
      });
    },
  );
  if (new Set(parsed.map((item) => item.work_object_id)).size !== parsed.length) {
    return fail('work_object_refs', 'must not contain duplicates');
  }
  return Object.freeze(parsed);
}

function sourceRefs(value: unknown): readonly SourceReference[] {
  const parsed = arrayValue(value, 'source_refs', MAX_REFERENCES).map(
    (item, index) => {
      const raw = asRecord(item, `source_refs[${index}]`);
      assertExactKeys(
        raw,
        ['source_system', 'source_ref'],
        `source_refs[${index}]`,
      );
      return Object.freeze({
        source_system: identifier(
          raw.source_system,
          `source_refs[${index}].source_system`,
        ),
        source_ref: safeText(
          raw.source_ref,
          `source_refs[${index}].source_ref`,
          256,
        ),
      });
    },
  );
  const keys = parsed.map((item) => `${item.source_system}\u0000${item.source_ref}`);
  if (new Set(keys).size !== keys.length) {
    return fail('source_refs', 'must not contain duplicates');
  }
  return Object.freeze(parsed);
}

function filters(value: unknown): readonly PageFilter[] {
  const operators: readonly PageFilterOperator[] = [
    'equals',
    'not_equals',
    'contains',
    'greater_than',
    'less_than',
  ];
  return Object.freeze(
    arrayValue(value, 'filters', MAX_FILTERS).map((item, index) => {
      const path = `filters[${index}]`;
      const raw = asRecord(item, path);
      assertExactKeys(raw, ['field', 'operator', 'value', 'source'], path);
      if (
        typeof raw.operator !== 'string' ||
        !operators.includes(raw.operator as PageFilterOperator)
      ) {
        return fail(`${path}.operator`, 'is not a supported operator');
      }
      if (raw.source !== 'visible_control') {
        return fail(`${path}.source`, 'must identify a visible control');
      }
      return Object.freeze({
        field: identifier(raw.field, `${path}.field`),
        operator: raw.operator as PageFilterOperator,
        value: filterValue(raw.value, `${path}.value`),
        source: 'visible_control' as const,
      });
    }),
  );
}

function filterValue(value: unknown, path: string): PageFilterValue {
  if (typeof value === 'string') {
    return safeText(value, path, 500);
  }
  if (typeof value === 'boolean') {
    return value;
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  return fail(path, 'must be a finite scalar value');
}

function allowedCapabilities(value: unknown): readonly string[] {
  const parsed = arrayValue(
    value,
    'allowed_capabilities',
    MAX_CAPABILITIES,
  ).map((item, index) => capabilityId(item, `allowed_capabilities[${index}]`));
  if (new Set(parsed).size !== parsed.length) {
    return fail('allowed_capabilities', 'must not contain duplicates');
  }
  return Object.freeze(parsed);
}

function freshness(value: unknown): PageFreshness {
  const raw = asRecord(value, 'freshness');
  assertExactKeys(raw, ['state', 'observed_at'], 'freshness');
  const states: readonly PageFreshnessState[] = ['unknown', 'reported', 'stale'];
  if (typeof raw.state !== 'string' || !states.includes(raw.state as PageFreshnessState)) {
    return fail('freshness.state', 'is not a supported freshness state');
  }
  const observedAt =
    raw.observed_at === null
      ? null
      : safeText(
          raw.observed_at,
          'freshness.observed_at',
          PAGE_CONTEXT_TIMESTAMP_MAX_LENGTH,
        );
  if (raw.state !== 'unknown' && observedAt === null) {
    return fail('freshness', 'reported or stale state requires observed_at');
  }
  if (observedAt !== null && !isValidTimestamp(observedAt)) {
    return fail(
      'freshness.observed_at',
      'must be a valid UTC RFC3339 timestamp',
    );
  }
  return Object.freeze({
    state: raw.state as PageFreshnessState,
    observed_at: observedAt,
  });
}

function isValidTimestamp(value: string): boolean {
  if (!timestampPattern.test(value)) {
    return false;
  }
  const year = Number(value.slice(0, 4));
  const month = Number(value.slice(5, 7));
  const day = Number(value.slice(8, 10));
  const hour = Number(value.slice(11, 13));
  const minute = Number(value.slice(14, 16));
  const second = Number(value.slice(17, 19));
  if (year === 0) {
    return false;
  }
  const parsed = new Date(0);
  parsed.setUTCFullYear(year, month - 1, day);
  parsed.setUTCHours(hour, minute, second, 0);
  return (
    parsed.getUTCFullYear() === year &&
    parsed.getUTCMonth() === month - 1 &&
    parsed.getUTCDate() === day &&
    parsed.getUTCHours() === hour &&
    parsed.getUTCMinutes() === minute &&
    parsed.getUTCSeconds() === second
  );
}

function visibility(value: unknown): PageVisibility {
  if (value === 'principal' || value === 'department' || value === 'organization') {
    return value;
  }
  return fail('visibility', 'is not a supported visibility');
}

export function parsePageContext(value: unknown): PageContextDeclaration {
  const raw = asRecord(value, 'page_context');
  assertExactKeys(raw, PAGE_CONTEXT_FIELD_NAMES, 'page_context');
  const parsedOrganizationScope = organizationScope(raw.organization_scope);
  const parsedVisibility = visibility(raw.visibility);
  if (
    parsedVisibility === 'department' &&
    parsedOrganizationScope?.department_id == null
  ) {
    return fail('visibility', 'department visibility requires a department scope');
  }
  if (
    parsedVisibility === 'organization' &&
    parsedOrganizationScope?.organization_id == null
  ) {
    return fail(
      'visibility',
      'organization visibility requires an organization scope',
    );
  }

  return Object.freeze({
    surface_id: surfaceId(raw.surface_id),
    organization_scope: parsedOrganizationScope,
    work_object_refs: workObjectRefs(raw.work_object_refs),
    source_refs: sourceRefs(raw.source_refs),
    filters: filters(raw.filters),
    selected_metric: optionalIdentifier(raw.selected_metric, 'selected_metric'),
    allowed_capabilities: allowedCapabilities(raw.allowed_capabilities),
    freshness: freshness(raw.freshness),
    visibility: parsedVisibility,
  });
}

export function createGeneralPageContext(
  context: PageContextDeclaration,
): PageContextDeclaration {
  return parsePageContext({
    ...context,
    allowed_capabilities: [],
    source_refs: [],
    work_object_refs: [],
  });
}
