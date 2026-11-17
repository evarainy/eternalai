import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/**
 * 首页的摆法：每一列有哪些卡片、各是什么大小。每个人可以自己增删、排列、改大小。
 *
 * 按登录用户保存在后端（见 `app/userPreferencesSync.ts`）；浏览器本地这份只用于刷新后的首屏，登录后以
 * 服务器为准。存储里出现认不得的卡片、重复的卡片或列数不对时，整份退回默认摆法，不让坏数据把首页弄空。
 */
export type CardSize = 'S' | 'M' | 'L';

export const CARD_SIZES: readonly CardSize[] = ['S', 'M', 'L'];

export const CARD_SIZE_LABELS: Record<CardSize, string> = { S: '小', M: '中', L: '大' };

export const CARD_IDS = ['todo', 'types', 'tasks', 'week', 'oa', 'done'] as const;

export type CardId = (typeof CARD_IDS)[number];

export interface CardSlot {
  id: CardId;
  size: CardSize;
}

export type HomeColumns = readonly (readonly CardSlot[])[];

/** 默认摆法：每列凑满 6 个高度单位（大 + 小、中 + 中、中 + 中）。 */
export const DEFAULT_COLUMNS: HomeColumns = [
  [{ id: 'todo', size: 'L' }, { id: 'types', size: 'S' }],
  [{ id: 'tasks', size: 'M' }, { id: 'week', size: 'M' }],
  [{ id: 'oa', size: 'M' }, { id: 'done', size: 'M' }],
];

export const HOME_LAYOUT_STORAGE_KEY = 'eternalai-home-layout';

function parseColumns(value: unknown): HomeColumns | null {
  if (!Array.isArray(value) || value.length !== DEFAULT_COLUMNS.length) return null;
  const seen = new Set<string>();
  const columns: CardSlot[][] = [];
  for (const column of value) {
    if (!Array.isArray(column)) return null;
    const slots: CardSlot[] = [];
    for (const slot of column as unknown[]) {
      if (slot === null || typeof slot !== 'object') return null;
      const { id, size } = slot as { id?: unknown; size?: unknown };
      if (!CARD_IDS.includes(id as CardId) || !CARD_SIZES.includes(size as CardSize) || seen.has(id as string)) {
        return null;
      }
      seen.add(id as string);
      slots.push({ id: id as CardId, size: size as CardSize });
    }
    columns.push(slots);
  }
  return columns;
}

function without(columns: HomeColumns, id: CardId): CardSlot[][] {
  return columns.map((column) => column.filter((slot) => slot.id !== id));
}

interface HomeLayoutState {
  columns: HomeColumns;
  /** 正在自定义首页：卡片可以拖动、改大小、移出。这个状态不保存，刷新就回到平时的样子。 */
  customizing: boolean;
  startCustomizing: () => void;
  finishCustomizing: () => void;
  /** 把一张卡片放到某一列的某个位置；卡片原来在别处就挪过来，原来不在首页就加进来。 */
  place: (id: CardId, column: number, index: number, size?: CardSize) => void;
  resize: (id: CardId, size: CardSize) => void;
  remove: (id: CardId) => void;
  reset: () => void;
}

export const useHomeLayoutStore = create<HomeLayoutState>()(
  persist(
    (set) => ({
      columns: DEFAULT_COLUMNS,
      customizing: false,
      startCustomizing: () => set({ customizing: true }),
      finishCustomizing: () => set({ customizing: false }),
      place: (id, column, index, size) =>
        set((state) => {
          const current = state.columns.flat().find((slot) => slot.id === id);
          const from = state.columns.findIndex((slots) => slots.some((slot) => slot.id === id));
          const fromIndex = from < 0 ? -1 : state.columns[from]!.findIndex((slot) => slot.id === id);
          const next = without(state.columns, id);
          const target = next[column];
          if (target === undefined) return state;
          // 在同一列里往后挪时，抽走自己以后目标位置要前移一格。
          const at = from === column && fromIndex >= 0 && fromIndex < index ? index - 1 : index;
          target.splice(Math.max(0, Math.min(at, target.length)), 0, {
            id,
            size: size ?? current?.size ?? 'M',
          });
          return { columns: next };
        }),
      resize: (id, size) =>
        set((state) => ({
          columns: state.columns.map((column) =>
            column.map((slot) => (slot.id === id ? { ...slot, size } : slot))),
        })),
      remove: (id) => set((state) => ({ columns: without(state.columns, id) })),
      reset: () => set({ columns: DEFAULT_COLUMNS }),
    }),
    {
      name: HOME_LAYOUT_STORAGE_KEY,
      partialize: ({ columns }) => ({ columns }),
      merge: (persisted, current) => ({
        ...current,
        columns: parseColumns((persisted as { columns?: unknown } | null)?.columns) ?? DEFAULT_COLUMNS,
      }),
    },
  ),
);

/** 采用服务器上本人的摆法（`{ columns }`）；没有记录或认不得就是默认摆法。 */
export function adoptHomeLayout(value: unknown): void {
  const columns = value !== null && typeof value === 'object'
    ? parseColumns((value as { columns?: unknown }).columns)
    : null;
  useHomeLayoutStore.setState({ columns: columns ?? DEFAULT_COLUMNS });
}

/** 给外壳的设置菜单用：进入自定义首页。 */
export function startCustomizingHome(): void {
  useHomeLayoutStore.getState().startCustomizing();
}
