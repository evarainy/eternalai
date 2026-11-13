/**
 * AppShell 的尺寸常量与身份缺项文案。
 *
 * 2026-11-13 改版：左侧是固定 76px 的窄栏，不再有展开 / 收起两态；设置菜单 268px。
 */

/** 窄栏宽度。 */
export const RAIL_WIDTH = 76;

/** 窄栏上每个导航项的高度；选中指示块按它的整数倍平移。 */
export const RAIL_ITEM_HEIGHT = 58;

/** 纸面与墨框之间的留边（上、右、下）。 */
export const SHEET_INSET = 8;

/** 设置菜单宽度。 */
export const USER_MENU_WIDTH = 268;

/** 设置菜单里身份那两行的字号（0.867rem，标准字号下 13px）；用于单行文案的不换行核算。 */
export const USER_MENU_META_FONT_SIZE = 13;

/** 设置菜单左右内边距之和（面板 8px + 身份区 12px，两侧）。 */
export const USER_MENU_HORIZONTAL_PADDING = 40;

/** 中文单行文案按给定字号估算的宽度（中文字形宽度按字号 1:1 估算）。 */
export function singleLineTextWidth(text: string, fontSize: number = USER_MENU_META_FONT_SIZE): number {
  return [...text].length * fontSize;
}

/**
 * 身份取不到时的 fail-closed 文案。
 *
 * 姓名来自服务端签名的会话票据，只要还登录着就一定有；最坏也只是少一个部门。
 * 职务是另一回事：OA 的用户信息接口落盘字段里根本没有职务（`DECISIONS.md` 2026-09-02）。
 * 雨爷 2026-09-04 裁定「留位 + 如实说明，不得编造」，所以位置留着，写的是取不到。
 */
export const NAME_UNAVAILABLE_LINE = '暂时取不到姓名';
export const DEPARTMENT_UNAVAILABLE_LINE = '部门暂时取不到';
export const JOB_TITLE_UNAVAILABLE_LINE = '职务暂时取不到';

/** 启动确认连不上后端时的唯一一行字与唯一一个动作。不解释原因、不给排查步骤。 */
export const BACKEND_UNREACHABLE_LINE = '连不上服务器';
export const BACKEND_UNREACHABLE_RETRY = '重试';

export const IDENTITY_FALLBACK_LINES = [
  NAME_UNAVAILABLE_LINE,
  DEPARTMENT_UNAVAILABLE_LINE,
  JOB_TITLE_UNAVAILABLE_LINE,
] as const;
