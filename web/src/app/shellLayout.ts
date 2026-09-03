import { workbenchTokens } from './theme';

/**
 * AppShell 的横向宽度核算。
 *
 * 2026-08-27「前端信息架构与终态导航」§四 要求以 1280px 为核算基准；2026-09-02 裁决把顶栏搜索框
 * 加宽到 392px 并取消「当前位置」。这里把每个顶栏元素的宽度写成常量，由 AppShell 以 CSS 自定义
 * 属性注入样式，使得测试核算的数值就是实际渲染使用的数值。
 *
 * 2026-09-04 实机走查发现 1280px 下顶栏溢出、右端头像被裁掉并压出一条横向滚动条。原核算漏掉了外壳
 * 自身的 16px 内边距（左右各一）与左导航和内容区之间的 16px 间隙，因此算出来的可用宽度比真实值多了
 * 48px。这里把这两项补进 `topbarAvailableWidth()`，并把搜索框做成**可收缩**的一格：设计宽度仍是画板
 * 的 392px，但窗口不够时它先让位，顶栏永远不溢出。
 */

export const LAYOUT_BASELINE_WIDTH = 1280;

export const SIDEBAR_EXPANDED_WIDTH = 224;
export const SIDEBAR_COLLAPSED_WIDTH = 64;
export const SIDEBAR_COLLAPSED_PADDING = 8;

/** 外壳自身的内边距与左导航 / 内容区之间的间隙；两项都吃掉顶栏的可用宽度。 */
export const SHELL_PADDING = 16;
export const SHELL_COLUMN_GAP = 16;

export const TOPBAR_HORIZONTAL_PADDING = 16;
export const TOPBAR_GAP = 12;

/** 顶栏固定顺序：搜索 → 部门 / 姓名 → 风格切换 → 系统状态 → 通知 → 用户头像。 */
export const TOPBAR_SEARCH_WIDTH = 392;
/** 搜索框收缩到这个宽度就不再让位；提示文字「搜索工作事项、文件编号、责任人」按 17px 仍能读出意图。 */
export const TOPBAR_SEARCH_MIN_WIDTH = 240;
export const TOPBAR_IDENTITY_WIDTH = 160;
export const TOPBAR_STYLE_WIDTH = 90;
/** 系统状态这一格要同时放下「系统状态」四个字与右侧的计数徽标，否则文字会被省略号截掉。 */
export const TOPBAR_SYSTEM_STATUS_WIDTH = 156;
export const TOPBAR_NOTIFICATIONS_WIDTH = 92;
export const TOPBAR_AVATAR_WIDTH = 44;

export const TOPBAR_ELEMENT_WIDTHS = [
  TOPBAR_SEARCH_WIDTH,
  TOPBAR_IDENTITY_WIDTH,
  TOPBAR_STYLE_WIDTH,
  TOPBAR_SYSTEM_STATUS_WIDTH,
  TOPBAR_NOTIFICATIONS_WIDTH,
  TOPBAR_AVATAR_WIDTH,
] as const;

/** 辅助文字字号；中文字形宽度按字号 1:1 估算，用于顶栏单行文案的不换行核算。 */
export const AUXILIARY_FONT_SIZE = workbenchTokens.auxiliaryFontSize;

export const MINIMUM_TARGET_SIZE = workbenchTokens.minimumTargetSize;

/** 顶栏六个元素加间距后占用的最小宽度。 */
export function topbarRequiredWidth(): number {
  const elements = TOPBAR_ELEMENT_WIDTHS.reduce((total, width) => total + width, 0);
  return elements + TOPBAR_GAP * (TOPBAR_ELEMENT_WIDTHS.length - 1);
}

/** 顶栏六个元素收缩到底（搜索框让到最小）后仍需要的横向宽度。 */
export function topbarMinimumRequiredWidth(): number {
  return topbarRequiredWidth() - (TOPBAR_SEARCH_WIDTH - TOPBAR_SEARCH_MIN_WIDTH);
}

/**
 * 给定左导航宽度时顶栏内部可用的横向宽度。
 *
 * 逐项扣：视口 − 外壳左右内边距 − 左导航 − 外壳列间隙 − 顶栏左右内边距。漏掉任何一项都会算出一个
 * 比真实值宽的预算，实机就会溢出。
 */
export function topbarAvailableWidth(
  sidebarWidth: number,
  viewportWidth: number = LAYOUT_BASELINE_WIDTH,
): number {
  return (
    viewportWidth -
    SHELL_PADDING * 2 -
    sidebarWidth -
    SHELL_COLUMN_GAP -
    TOPBAR_HORIZONTAL_PADDING * 2
  );
}

/** 折叠态下单个导航项的可点宽度。 */
export function collapsedNavigationTargetWidth(): number {
  return SIDEBAR_COLLAPSED_WIDTH - SIDEBAR_COLLAPSED_PADDING * 2;
}

/** 中文单行文案按辅助字号估算的宽度。 */
export function singleLineTextWidth(text: string): number {
  return [...text].length * AUXILIARY_FONT_SIZE;
}

/**
 * 顶栏「部门 / 姓名」的 fail-closed 文案。
 *
 * 后端当前没有「当前用户身份」读取接口（`LoginResponse` 只有 `authenticated`），前端拿不到部门与
 * 姓名。按 2026-08-27「低数字素养用户的界面硬约束」，这里如实说明取不到，不显示空位、不显示登录
 * 标识、不显示假名。
 *
 * 顶栏这一格照画板只占**一行**，因此只放 `IDENTITY_UNAVAILABLE_STATEMENT`；下一步
 * （`IDENTITY_UNAVAILABLE_NEXT_STEP`）放在头像点开的用户菜单里，两句合起来仍然满足「说明 + 下一步」。
 * 两行都必须在 `TOPBAR_IDENTITY_WIDTH` 内单行放下。
 */
export const IDENTITY_UNAVAILABLE_STATEMENT = '暂时取不到部门和姓名';
export const IDENTITY_UNAVAILABLE_NEXT_STEP = '请刷新或重新登录';

export const IDENTITY_UNAVAILABLE_LINES = [
  IDENTITY_UNAVAILABLE_STATEMENT,
  IDENTITY_UNAVAILABLE_NEXT_STEP,
] as const;
