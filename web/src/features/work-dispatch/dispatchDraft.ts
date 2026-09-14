import { createSessionDraftStore, LEGACY_DISPATCH_DRAFT_KEY } from '../../stores/sessionDraftStore';
import type { DraftSessionToken } from '../../stores/sessionDraftStore';

/** 草稿字段规范化与当前认证会话内的显式快照；不持久化。 */

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
  /** Confirmed content time only; never a frozen publication request. */
  dueInstant?: string;
  dueZone?: string;
  dueOffset?: string;
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

export const DRAFT_STORAGE_KEY = LEGACY_DISPATCH_DRAFT_KEY;

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
 * 输入字段可能不合法，读不懂的字段按默认值处理。
 */
export function parseDraft(raw: unknown): DispatchDraft {
  if (raw === null || typeof raw !== 'object') {
    return { ...EMPTY_DRAFT, targets: [], reminders: [...DEFAULT_REMINDERS] };
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
    : [...DEFAULT_REMINDERS];
  return {
    brief: textField(candidate.brief),
    kind: isDispatchKind(candidate.kind) ? candidate.kind : EMPTY_DRAFT.kind,
    title: textField(candidate.title),
    assignee: textField(candidate.assignee),
    dueAt: textField(candidate.dueAt),
    ...(typeof candidate.dueInstant === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(candidate.dueInstant)
      && Number.isFinite(Date.parse(candidate.dueInstant))
      ? { dueInstant: candidate.dueInstant, dueZone: textField(candidate.dueZone), dueOffset: textField(candidate.dueOffset) } : {}),
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

const slot = createSessionDraftStore<DispatchDraft>(parseDraft);
if (import.meta.hot) import.meta.hot.dispose(() => slot.dispose());

export function loadDraft(token: DraftSessionToken | null): DispatchDraft {
  return slot.read(token) ?? parseDraft(null);
}

export function saveDraft(draft: DispatchDraft, token: DraftSessionToken | null): boolean {
  return slot.save(token, draft);
}

export function clearDraft(token: DraftSessionToken | null): boolean {
  return slot.clear(token);
}
