/**
 * IM 本机偏好：只存会话列表宽度和输入框高度（FE_PLAN §5.2「本机持久化」）。滚动位置只放内存。
 * 存储可能不可用（隐私窗口、站点数据被清），读写都包 try/catch，失败时用默认值，不影响使用。
 */
const STORAGE_KEY = 'eternalai-im-prefs';

export const LIST_WIDTH = { min: 240, max: 420, initial: 296 } as const;
export const COMPOSER_HEIGHT = { min: 64, max: 320, initial: 64 } as const;

export interface ImPrefs {
  listWidth: number;
  composerHeight: number;
}

function clamp(value: unknown, range: { min: number; max: number; initial: number }): number {
  return typeof value === 'number' && Number.isFinite(value)
    ? Math.min(range.max, Math.max(range.min, Math.round(value)))
    : range.initial;
}

export function readImPrefs(): ImPrefs {
  let stored: Partial<ImPrefs> = {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) stored = JSON.parse(raw) as Partial<ImPrefs>;
  } catch {
    stored = {};
  }
  return {
    listWidth: clamp(stored.listWidth, LIST_WIDTH),
    composerHeight: clamp(stored.composerHeight, COMPOSER_HEIGHT),
  };
}

export function writeImPrefs(change: Partial<ImPrefs>): ImPrefs {
  const next = readImPrefs();
  if (change.listWidth !== undefined) next.listWidth = clamp(change.listWidth, LIST_WIDTH);
  if (change.composerHeight !== undefined) next.composerHeight = clamp(change.composerHeight, COMPOSER_HEIGHT);
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // 存不下只影响下次打开时的默认尺寸。
  }
  return next;
}

export function clampListWidth(value: number): number {
  return clamp(value, LIST_WIDTH);
}

export function clampComposerHeight(value: number): number {
  return clamp(value, COMPOSER_HEIGHT);
}
