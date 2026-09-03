/**
 * 界面图标：24×24 描边内联 SVG，零依赖、不引图标库、不用图标字体。
 *
 * 路径逐字来自定稿画板的符号表 `_scratch/design/icons.py` 的 `P` 字典（`Inline stroke icons
 * (24x24 viewBox). No dependency, no icon font.`）。此前界面用的是 `✦ ▤ ✎ ▦ ✉ ☻ ⊘ ◇ ➜` 这类字符
 * 符号——同一段文字里字形大小与基线各不相同，是「难看」的直接来源，本组件把它们全部替换掉。
 *
 * 画板的渲染参数（`icons.py` 的 `ic()`）是 `fill="none"`、`stroke="currentColor"`、
 * `stroke-width` 默认 1.7、`stroke-linecap`/`stroke-linejoin` 为 `round`，这里逐项对齐。颜色一律
 * 由 `currentColor` 继承，图标本身不带固定填充色（2026-09-02 玻璃硬约束第 4 条）。
 */
export const ICON_PATHS = {
  alert:
    'M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01',
  bell: 'M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 0 1-3.46 0',
  bolt: 'M13 2 4 14h7l-1 8 9-12h-7z',
  calendar:
    'M4 5h16a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM16 3v4M8 3v4M3 11h18',
  card: 'M2 7a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2zM2 11h20',
  chat: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  check: 'M20 6 9 17l-5-5',
  chevron: 'm6 9 6 6 6-6',
  clock: 'M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18zM12 7v5l3 2',
  close: 'M18 6 6 18M6 6l12 12',
  expandnav: 'm6 17 5-5-5-5M13 17l5-5-5-5',
  external: 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3',
  eye: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM12 9a3 3 0 1 1 0 6 3 3 0 0 1 0-6z',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6',
  folder: 'M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7l-2-3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2z',
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z',
  help: 'M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18zM9.1 9a3 3 0 0 1 5.82 1c0 2-3 3-3 3M12 17h.01',
  inbox:
    'M22 12h-6l-2 3h-4l-2-3H2M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z',
  list: 'M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01',
  mail: 'M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM22 7l-10 6L2 7',
  maximize: 'M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7',
  minus: 'M5 12h14',
  plus: 'M12 5v14M5 12h14',
  search: 'M11 4a7 7 0 1 1 0 14 7 7 0 0 1 0-14zM21 21l-4.35-4.35',
  send: 'M22 2 11 13M22 2l-7 20-4-9-9-4z',
  sliders: 'M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6',
  spark:
    'M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 17l.7 1.9 1.9.7-1.9.7-.7 1.9-.7-1.9-1.9-.7 1.9-.7z',
  tool: 'M14.7 6.3a4 4 0 0 1 5 5L18 13l-7 7-4-4 7-7z M3 21l3-3',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 3a4 4 0 1 1 0 8 4 4 0 0 1 0-8z',
  users:
    'M17 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9.5 3a4 4 0 1 1 0 8 4 4 0 0 1 0-8zM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  droplet:
    'M12 2.7c0 0 6.3 6.6 6.3 10.6a6.3 6.3 0 0 1-12.6 0c0-4 6.3-10.6 6.3-10.6zM9.2 14.2a2.9 2.9 0 0 0 2.8 2.9',
} as const;

export type IconName = keyof typeof ICON_PATHS;

export interface IconProps {
  className?: string;
  /** 画板默认 1.7；坐落在图标底座里的那一档用 1.9。 */
  strokeWidth?: number;
  name: IconName;
  size?: number;
}

/**
 * 图标一律是装饰性的：可访问名称由它旁边的文字或外层控件的 `aria-label` 承担，因此固定
 * `aria-hidden`，不让读屏器把线条读成内容。
 */
export function Icon({ className, name, size = 20, strokeWidth = 1.7 }: IconProps) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      focusable="false"
      height={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={strokeWidth}
      viewBox="0 0 24 24"
      width={size}
    >
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}

export default Icon;
