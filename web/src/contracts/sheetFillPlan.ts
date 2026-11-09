/**
 * Sheet fill plan v1 (`data.sheet_fill_plan`): the server-validated mapping a confirmation
 * card shows and resubmits unchanged. Any other shape is not a plan.
 */

export type SheetFillSource = 'oa_pending' | 'document';
export type SheetFillField =
  | 'status' | 'received_at' | 'created_at' | 'workflow_type_id' | 'todo_id' | 'quote';

export interface SheetFillTarget {
  readonly column: string;
  readonly header: string;
  readonly field: SheetFillField;
}

export interface SheetFillMapping {
  readonly sheet: string;
  readonly first_row: number;
  readonly last_row: number;
  readonly source: SheetFillSource;
  readonly key_column: string;
  readonly key_header: string;
  readonly fills: readonly SheetFillTarget[];
}

export interface SheetFillPlan {
  readonly plan_version: 1;
  readonly artifact_id: string;
  readonly sha256: string;
  readonly filename: string;
  readonly mappings: readonly SheetFillMapping[];
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SHA256 = /^[0-9a-f]{64}$/;
const COLUMN = /^[A-Z]{1,2}$/;
const MAX_EXCEL_ROW = 1048576;
const FIELDS: Record<SheetFillSource, readonly SheetFillField[]> = {
  oa_pending: ['status', 'received_at', 'created_at', 'workflow_type_id', 'todo_id'],
  document: ['quote'],
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.length && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key));
}

function text(value: unknown, max: number): value is string {
  return typeof value === 'string' && value.length >= 1 && value.length <= max;
}

function row(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 2 && value <= MAX_EXCEL_ROW;
}

function target(value: unknown, source: SheetFillSource): SheetFillTarget | null {
  if (!isObject(value) || !hasExactKeys(value, ['column', 'header', 'field'])) return null;
  const { column, header, field } = value;
  if (typeof column !== 'string' || !COLUMN.test(column) || !text(header, 65)
      || !FIELDS[source].includes(field as SheetFillField)) return null;
  return { column, header, field: field as SheetFillField };
}

function mapping(value: unknown): SheetFillMapping | null {
  if (!isObject(value) || !hasExactKeys(
    value, ['sheet', 'first_row', 'last_row', 'source', 'key_column', 'key_header', 'fills'],
  )) return null;
  const { sheet, first_row: firstRow, last_row: lastRow, source, key_column: keyColumn,
    key_header: keyHeader, fills } = value;
  if (!text(sheet, 31) || !row(firstRow) || !row(lastRow) || firstRow > lastRow
      || (source !== 'oa_pending' && source !== 'document')
      || typeof keyColumn !== 'string' || !COLUMN.test(keyColumn) || !text(keyHeader, 65)
      || !Array.isArray(fills) || fills.length < 1 || fills.length > 5) return null;
  const targets = fills.map((item: unknown) => target(item, source));
  if (targets.some((item) => item === null)) return null;
  const columns = new Set([keyColumn, ...targets.map((item) => item!.column)]);
  if (columns.size !== targets.length + 1) return null;
  return {
    sheet, first_row: firstRow, last_row: lastRow, source, key_column: keyColumn,
    key_header: keyHeader, fills: targets as SheetFillTarget[],
  };
}

/** Strict plan v1 check; null for anything else. */
export function parseSheetFillPlan(value: unknown): SheetFillPlan | null {
  if (!isObject(value) || !hasExactKeys(
    value, ['plan_version', 'artifact_id', 'sha256', 'filename', 'mappings'],
  )) return null;
  const { plan_version: version, artifact_id: artifactId, sha256, filename, mappings } = value;
  if (version !== 1 || typeof artifactId !== 'string' || !UUID.test(artifactId)
      || typeof sha256 !== 'string' || !SHA256.test(sha256) || !text(filename, 255)
      || !Array.isArray(mappings) || mappings.length < 1 || mappings.length > 5) return null;
  const parsed = mappings.map(mapping);
  if (parsed.some((item) => item === null)) return null;
  const sheets = new Set(parsed.map((item) => item!.sheet));
  if (sheets.size !== parsed.length) return null;
  return {
    plan_version: 1, artifact_id: artifactId, sha256, filename,
    mappings: parsed as SheetFillMapping[],
  };
}
