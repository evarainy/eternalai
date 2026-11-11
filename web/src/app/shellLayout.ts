import { workbenchTokens } from './theme';

/**
 * AppShell 的尺寸常量与身份缺项文案。
 *
 * 2026-11-11 雨爷决定全站取消顶栏（全局项移到左导航底部个人栏），原先为顶栏六格做的横向宽度核算
 * （2026-09-02 / 09-04）随之删除；左导航宽度与折叠态目标尺寸仍由这里提供。
 */

export const LAYOUT_BASELINE_WIDTH = 1280;

export const SIDEBAR_EXPANDED_WIDTH = 224;
export const SIDEBAR_COLLAPSED_WIDTH = 64;
export const SIDEBAR_COLLAPSED_PADDING = 8;

/** 辅助文字字号；中文字形宽度按字号 1:1 估算，用于单行文案的不换行核算。 */
export const AUXILIARY_FONT_SIZE = workbenchTokens.auxiliaryFontSize;

export const MINIMUM_TARGET_SIZE = workbenchTokens.minimumTargetSize;

/** 折叠态下单个导航项的可点宽度。 */
export function collapsedNavigationTargetWidth(): number {
  return SIDEBAR_COLLAPSED_WIDTH - SIDEBAR_COLLAPSED_PADDING * 2;
}

/** 中文单行文案按辅助字号估算的宽度。 */
export function singleLineTextWidth(text: string): number {
  return [...text].length * AUXILIARY_FONT_SIZE;
}

/**
 * 身份取不到时的 fail-closed 文案。
 *
 * `P2-USER-PROFILE-READ-001` 之后姓名来自服务端签名的会话票据，**只要还登录着就一定有**，所以个人栏
 * 几乎不会整句缺失——最坏也只是少一个部门，此时个人栏只显示姓名，**一个字的提示都不加**。
 * 下面这几行只用在用户菜单里，一处一行。
 *
 * 职务是另一回事：OA 的用户信息接口落盘字段里**根本没有职务**（`DECISIONS.md` 2026-09-02）。
 * 雨爷 2026-09-04 裁定「留位 + 如实说明，不得编造」，所以位置留着，写的是取不到，不是画板上的
 * 「主任科员」。
 */
export const NAME_UNAVAILABLE_LINE = '暂时取不到姓名';
export const DEPARTMENT_UNAVAILABLE_LINE = '部门暂时取不到';
export const JOB_TITLE_UNAVAILABLE_LINE = '职务暂时取不到';

/** 启动确认连不上后端时的唯一一行字与唯一一个动作。不解释原因、不给排查步骤。 */
export const BACKEND_UNREACHABLE_LINE = '连不上服务器';
export const BACKEND_UNREACHABLE_RETRY = '重试';

/** 用户菜单里这几行都要在 268px 的弹层内单行放下。 */
export const USER_MENU_WIDTH = 268;

export const IDENTITY_FALLBACK_LINES = [
  NAME_UNAVAILABLE_LINE,
  DEPARTMENT_UNAVAILABLE_LINE,
  JOB_TITLE_UNAVAILABLE_LINE,
] as const;
