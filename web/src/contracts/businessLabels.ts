import type { UIComponentTargetSystem } from '../generated/runtime/runtime.schemas';

/**
 * Business wording of confirmation cards, shared by the chat confirm card and the MCP recovery
 * card. Only approved definitions are listed: the D fill-stop manifest field names
 * (`app/infra/browser/d_form_manifest.py::build_fill_stop_manifest`). A key without an approved
 * business name is shown as-is; names are never invented here. Display only: execution
 * arguments, `expected_revision` and `preview_digest` never pass through this module.
 */
const fieldLabels: Readonly<Record<string, string>> = {
  category: '报修类别',
  position: '故障位置',
  description: '故障描述',
};

export const targetSystemLabels: Record<Exclude<UIComponentTargetSystem, null>, string> = {
  oa: 'OA',
  u8: 'U8',
  hikvision_ivms: '海康 iVMS',
  business_platform: '业务平台',
};

export function fieldLabel(fieldName: string): string {
  return (Object.prototype.hasOwnProperty.call(fieldLabels, fieldName)
    ? fieldLabels[fieldName] : undefined) ?? fieldName;
}

/**
 * The operation name of a confirm card summary (`name：short_description` from the capability
 * definition). The description part is dropped: it can carry technical placeholders such as
 * the governed MCP step text.
 */
export function operationName(summary: string): string {
  const separator = summary.indexOf('：');
  return separator > 0 ? summary.slice(0, separator) : summary;
}
