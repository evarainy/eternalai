/**
 * 「新建应用」草稿的取值闭集与本机草稿存取。
 *
 * 2026-09-02 裁决「界面先行、后端不做」：这里没有任何 API 调用。产出物是**草稿**，只落本机
 * `localStorage`；「提交审核」在审核端点接进来之前不可用，界面上写明「提交审核后才对他人可见」。
 */

export const SOFTWARE_SOURCES = [
  { value: 'existing_system', title: '接入单位已有的系统', hint: '填一个地址，登记成大家能点开的入口' },
  { value: 'published_software', title: '装单位发布的软件', hint: '从单位软件库里挑，不用填地址' },
] as const;

export type SoftwareSource = (typeof SOFTWARE_SOURCES)[number]['value'];

export const OPEN_MODES = ['new_window', 'embedded'] as const;

export type OpenMode = (typeof OPEN_MODES)[number];

export const OPEN_MODE_LABELS: Record<OpenMode, string> = {
  new_window: '开新窗口',
  embedded: '嵌在工作台里面',
};

export const BINDING_CHOICES = ['required', 'not_required'] as const;

export type BindingChoice = (typeof BINDING_CHOICES)[number];

export const BINDING_CHOICE_LABELS: Record<BindingChoice, string> = {
  required: '要绑',
  not_required: '不用绑',
};

export const RISK_CHOICES = ['read_only', 'writes_data'] as const;

export type RiskChoice = (typeof RISK_CHOICES)[number];

export const RISK_CHOICE_LABELS: Record<RiskChoice, string> = {
  read_only: '只能查，不改',
  writes_data: '会改数据',
};

export interface NewSoftwareDraft {
  source: SoftwareSource;
  name: string;
  summary: string;
  address: string;
  owner: string;
  openMode: OpenMode;
  binding: BindingChoice;
  risk: RiskChoice;
  visibleTo: readonly string[];
}

export const EMPTY_NEW_SOFTWARE_DRAFT: NewSoftwareDraft = {
  source: 'existing_system',
  name: '',
  summary: '',
  address: '',
  owner: '',
  openMode: 'new_window',
  binding: 'required',
  risk: 'read_only',
  visibleTo: [],
};

export const NEW_SOFTWARE_DRAFT_KEY = 'eternalai.apps.new-software-draft';

function textField(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function memberOr<Value extends string>(
  members: readonly Value[],
  value: unknown,
  fallback: Value,
): Value {
  return members.some((member) => member === value) ? (value as Value) : fallback;
}

export function dedupeVisibleTo(values: readonly string[]): string[] {
  const seen = new Set<string>();
  const kept: string[] = [];
  for (const value of values) {
    const trimmed = value.trim();
    if (trimmed.length === 0 || seen.has(trimmed)) {
      continue;
    }
    seen.add(trimmed);
    kept.push(trimmed);
  }
  return kept;
}

export function parseNewSoftwareDraft(raw: unknown): NewSoftwareDraft {
  if (raw === null || typeof raw !== 'object') {
    return EMPTY_NEW_SOFTWARE_DRAFT;
  }
  const candidate = raw as Record<string, unknown>;
  const visibleTo: readonly unknown[] = Array.isArray(candidate.visibleTo)
    ? candidate.visibleTo
    : [];
  return {
    source: memberOr(
      SOFTWARE_SOURCES.map((entry) => entry.value),
      candidate.source,
      EMPTY_NEW_SOFTWARE_DRAFT.source,
    ),
    name: textField(candidate.name),
    summary: textField(candidate.summary),
    address: textField(candidate.address),
    owner: textField(candidate.owner),
    /*
     * 「嵌在工作台里面」这一档在界面上是禁用的（OA 实测 `X-Frame-Options: SAMEORIGIN`，嵌不进来）。
     * 本机存的草稿可能被手改成 `embedded`，读回来时一律拉回「开新窗口」，不让一个当前不成立的取值从
     * 存储绕进界面。
     */
    openMode: 'new_window',
    binding: memberOr(BINDING_CHOICES, candidate.binding, EMPTY_NEW_SOFTWARE_DRAFT.binding),
    risk: memberOr(RISK_CHOICES, candidate.risk, EMPTY_NEW_SOFTWARE_DRAFT.risk),
    visibleTo: dedupeVisibleTo(
      visibleTo.filter((item): item is string => typeof item === 'string'),
    ),
  };
}

export function loadNewSoftwareDraft(): NewSoftwareDraft {
  let stored: string | null;
  try {
    stored = window.localStorage.getItem(NEW_SOFTWARE_DRAFT_KEY);
  } catch {
    return EMPTY_NEW_SOFTWARE_DRAFT;
  }
  if (stored === null) {
    return EMPTY_NEW_SOFTWARE_DRAFT;
  }
  try {
    return parseNewSoftwareDraft(JSON.parse(stored));
  } catch {
    return EMPTY_NEW_SOFTWARE_DRAFT;
  }
}

/** 存成功返回 `true`；浏览器禁写本机存储时返回 `false`，由调用方如实告诉用户没存上。 */
export function saveNewSoftwareDraft(draft: NewSoftwareDraft): boolean {
  try {
    window.localStorage.setItem(NEW_SOFTWARE_DRAFT_KEY, JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}
