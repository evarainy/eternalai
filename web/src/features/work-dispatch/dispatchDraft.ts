/**
 * 任务交办草稿的取值闭集与本机草稿存取。
 *
 * 后端不做（2026-09-02 裁决「界面先行、后端不做」）：这里没有任何 API 调用，「存草稿」只落到本机
 * `localStorage`，「发布」在下发接口接进来之前不会真的下发。取值闭集写在这里而不是散在组件里，是为了
 * 让「类型」「提醒策略」这两项**只能选、不能自由输入**这件事在类型层就成立。
 */

/** 交办类型：单选闭集，界面上是下拉不是文本框。 */
export const DISPATCH_KINDS = ['通知', '督办令', '工作任务', '提醒'] as const;

export type DispatchKind = (typeof DISPATCH_KINDS)[number];

/** 提醒策略：多选闭集，默认选中后三档。 */
export const REMINDER_CHOICES = [
  '提前 7 天',
  '提前 3 天',
  '提前 1 天',
  '逾期当天',
] as const;

export type ReminderChoice = (typeof REMINDER_CHOICES)[number];

export const DEFAULT_REMINDERS: readonly ReminderChoice[] = [
  '提前 3 天',
  '提前 1 天',
  '逾期当天',
];

export interface DispatchDraft {
  /** 顶部一句话输入。 */
  brief: string;
  kind: DispatchKind;
  title: string;
  /** 责任人 / 责任部门。 */
  assignee: string;
  /** 截止时间，`<input type="datetime-local">` 的取值（本地时间，无时区）。 */
  dueAt: string;
  visibility: string;
  /** 交办对象，去重后的顺序表。 */
  targets: readonly string[];
  /** 办理要求与交付物。 */
  requirement: string;
  /** 回执要求。 */
  receipt: string;
  reminders: readonly ReminderChoice[];
}

export const EMPTY_DRAFT: DispatchDraft = {
  brief: '',
  kind: '通知',
  title: '',
  assignee: '',
  dueAt: '',
  visibility: '',
  targets: [],
  requirement: '',
  receipt: '',
  reminders: DEFAULT_REMINDERS,
};

export const DRAFT_STORAGE_KEY = 'eternalai.work-dispatch.draft';

function isDispatchKind(value: unknown): value is DispatchKind {
  return DISPATCH_KINDS.some((kind) => kind === value);
}

function isReminderChoice(value: unknown): value is ReminderChoice {
  return REMINDER_CHOICES.some((choice) => choice === value);
}

function textField(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

/**
 * 坏值一律回退到空草稿的对应项，不抛错、不半途留下一个「一半是旧草稿一半是默认值」的形态之外的东西：
 * 本机存的东西可能被手改过，读不懂的字段按没存过处理。
 */
export function parseDraft(raw: unknown): DispatchDraft {
  if (raw === null || typeof raw !== 'object') {
    return EMPTY_DRAFT;
  }
  const candidate = raw as Record<string, unknown>;
  const targets = Array.isArray(candidate.targets)
    ? dedupeTargets(candidate.targets.filter((item): item is string => typeof item === 'string'))
    : [];
  const storedReminders: readonly unknown[] = Array.isArray(candidate.reminders)
    ? candidate.reminders
    : [];
  const reminders = Array.isArray(candidate.reminders)
    ? REMINDER_CHOICES.filter((choice) =>
        storedReminders.some((stored) => isReminderChoice(stored) && stored === choice),
      )
    : DEFAULT_REMINDERS;
  return {
    brief: textField(candidate.brief),
    kind: isDispatchKind(candidate.kind) ? candidate.kind : EMPTY_DRAFT.kind,
    title: textField(candidate.title),
    assignee: textField(candidate.assignee),
    dueAt: textField(candidate.dueAt),
    visibility: textField(candidate.visibility),
    targets,
    requirement: textField(candidate.requirement),
    receipt: textField(candidate.receipt),
    reminders,
  };
}

/** 交办对象去重：按去掉首尾空白后的字面量比较，保留首次出现的顺序。 */
export function dedupeTargets(values: readonly string[]): string[] {
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

export function loadDraft(): DispatchDraft {
  let stored: string | null;
  try {
    stored = window.localStorage.getItem(DRAFT_STORAGE_KEY);
  } catch {
    return EMPTY_DRAFT;
  }
  if (stored === null) {
    return EMPTY_DRAFT;
  }
  try {
    return parseDraft(JSON.parse(stored));
  } catch {
    return EMPTY_DRAFT;
  }
}

/** 存成功返回 `true`；浏览器禁写本机存储时返回 `false`，由调用方如实告诉用户没存上。 */
export function saveDraft(draft: DispatchDraft): boolean {
  try {
    window.localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}
