import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { workbenchTheme, workbenchTokens } from '../theme';

// 保留可辨边界与焦点可见性；已移除的 antd 全局玻璃覆盖不再作为样式合同。

function readSource(relativePath: string): string {
  return readFileSync(
    fileURLToPath(new URL(relativePath, import.meta.url).href),
    'utf-8',
  );
}

function channels(hex: string): [number, number, number] {
  const parsed = hex
    .slice(1)
    .match(/.{2}/g)
    ?.map((channel) => Number.parseInt(channel, 16));
  const [red, green, blue] = parsed ?? [];
  if (red === undefined || green === undefined || blue === undefined) {
    throw new Error(`invalid_hex_color:${hex}`);
  }
  return [red, green, blue];
}

function luminance(hex: string): number {
  const linear = channels(hex).map((channel) => {
    const scaled = channel / 255;
    return scaled <= 0.03928
      ? scaled / 12.92
      : ((scaled + 0.055) / 1.055) ** 2.4;
  });
  const [red, green, blue] = linear;
  if (red === undefined || green === undefined || blue === undefined) {
    throw new Error(`invalid_hex_color:${hex}`);
  }
  return red * 0.2126 + green * 0.7152 + blue * 0.0722;
}

function contrastRatio(foreground: string, background: string): number {
  const first = luminance(foreground);
  const second = luminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

/**
 * 把一层描边按 source-over 合成到相邻底色上。边界的对比度必须按**合成后**的颜色算：
 * `rgb(255 255 255 / 94%)` 压在近白面上就等于看不见。
 *
 * 三种写法都收：`rgb(r g b / a%)` 半透明、`#rrggbb` 不透明，以及兼容令牌在用的
 * `var(--other-token)` 间接引用（一层层跟到底，跟不到就抛，不静默当成不透明黑）。**不许跟丢**——
 * 跟丢会让 6.11:1 悄悄算成别的数。
 */
function compositeOver(edge: string, backdrop: string): string {
  let resolved = edge;
  for (let hop = 0; hop < 8 && resolved.startsWith('var(--'); hop += 1) {
    const reference = /^var\(\s*--([\w-]+)\s*\)$/.exec(resolved);
    if (reference === null) {
      throw new Error(`unsupported_edge:${edge}`);
    }
    resolved = token(reference[1] as string);
  }
  if (/^#[0-9a-f]{6}$/i.test(resolved)) {
    return resolved;
  }
  const matched = /^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*\/\s*([\d.]+)%\s*\)$/.exec(
    resolved,
  );
  if (matched === null) {
    throw new Error(`unsupported_edge:${edge}`);
  }
  const alpha = Number.parseFloat(matched[4] as string) / 100;
  const source = [matched[1], matched[2], matched[3]].map((raw) =>
    Number.parseInt(raw as string, 10),
  );
  return `#${channels(backdrop)
    .map((channel, index) =>
      Math.round(alpha * (source[index] as number) + (1 - alpha) * channel)
        .toString(16)
        .padStart(2, '0'),
    )
    .join('')}`;
}

const GLOBAL_CSS = readSource('../../styles.css');

/** 从 `styles.css` 的 `:root` 里取一个令牌的字面值，不用手抄。 */
function token(name: string): string {
  const matched = new RegExp(`\\n\\s*--${name}:\\s*([^;]+);`).exec(GLOBAL_CSS);
  if (matched?.[1] === undefined) {
    throw new Error(`missing_token:${name}`);
  }
  return matched[1].trim().replace(/\s+/g, ' ');
}

/** 边界要与相邻色比，相邻色就是两个读物面：白色纸面与浅色纸面。 */
const SURFACES: readonly [string, string][] = [
  ['内容面板', workbenchTokens.colors.paper],
  ['浅色纸面', workbenchTokens.colors.paperSoft],
];

/** 去掉块注释：注释里复盘旧值不算「还在渲染路径上」，只有声明里的才算。 */
function withoutComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '');
}

/**
 * 一个 CSS 文件里某条规则的声明块。尾随的 `{` 与空白会被剥掉。
 *
 * 两个锚点缺一不可，不然会串台：`(?:^|\})\s*` 要求选择器是**这条规则选择器列表的第一项**——否则
 * `.ant-btn-primary:not(...)` 会命中前面那条 `.ant-btn-default:not(...), .ant-btn-primary:not(...)`
 * 的合并规则，读到的是次动作的声明；`(?=[\s,{])` 要求选择器整词结束——否则 `.action` 会命中 `.actions`。
 * 两条都是我写这份守卫时先踩后补的。
 */
function rule(css: string, selector: string): string {
  const head = selector.replace(/\s*\{\s*$/, '');
  const escaped = head.replace(/[.*+?^${}()|[\]\\]/g, String.raw`\$&`);
  const matched = new RegExp(
    `(?:^|\\})\\s*${escaped}(?=[\\s,{])[^{}]*\\{([^}]*)\\}`,
  ).exec(withoutComments(css));
  if (matched?.[1] === undefined) {
    throw new Error(`missing_rule:${selector}`);
  }
  return matched[1];
}

describe('可辨边界的数值（WCAG 2.2 SC 1.4.11）', () => {
  /*
   * 2026-11-13 改版：v7 样板的次动作按钮与输入框是「白底 + 细线条」，静息边界取线条色，不再要求 3:1
   * （主动作是实心蓝底，聚焦态是主题色边框加外晕，这两处的 3:1 仍在下面核对）。这里钉住的是「仍在用
   * 兼容令牌的自绘控件确实有一条看得见的线」：令牌指向样板的线条色，而不是透明或白色。
   */
  it('次动作与输入框的静息边界取样板的线条色', () => {
    expect(token('workbench-control-edge')).toBe('var(--line-2)');
    expect(token('workbench-field-edge')).toBe('var(--line-3)');
    expect(token('line-2')).toBe('rgb(16 26 46 / 15%)');
    expect(token('line-3')).toBe('rgb(16 26 46 / 22%)');
  });

  it.each(SURFACES)('主动作边界在「%s」上过 3:1', (_surfaceName, surface) => {
    expect(
      contrastRatio(
        compositeOver(token('workbench-control-edge-primary'), surface),
        surface,
      ),
    ).toBeGreaterThanOrEqual(3);
  });

  it('主动作按钮边框是主题色本色，不带 alpha', () => {
    expect(token('workbench-control-edge-primary')).toBe('var(--primary)');
    expect(token('primary')).toBe(workbenchTokens.colors.primary);
  });

  /*
   * 反例。这两个值是本轮换掉的旧值：只要有人把它们改回来，上面的断言就会红——但光有上面那组看不出
   * 「为什么非换不可」，所以把不达标这件事本身也钉住。
   */
  it.each([
    ['原按钮白色高光边', 'rgb(255 255 255 / 94%)'],
    ['原「发布」按钮的 48% 蓝内边', 'rgb(47 107 255 / 48%)'],
  ])('%s 在内容面板上达不到 3:1，所以不能当边界用', (_name, edge) => {
    const surface = workbenchTokens.colors.paper;
    expect(contrastRatio(compositeOver(edge, surface), surface)).toBeLessThan(3);
  });
});

describe('自绘控件的边界与动作层级', () => {
  // 沿用兼容令牌的控件保留原落点；新版控件核对 v7 的纸面与细线。
  it.each([
    ['工作事项搜索提交键（原顶栏搜索）', '../WorkObjectSearchField.module.css', '.searchSubmit {', ['var(--workbench-control-ring)']],
    ['浮动面板「知道了」', '../AIDock.module.css', '.noticeButton {', ['var(--workbench-control-ring)']],
    ['占位页动作', '../../shared/ui/PlaceholderPage.module.css', '.action {', ['var(--workbench-control-ring)']],
    ['工作事项分段控件', '../../pages/WorkObjectsPage.module.css', '.segmented {', ['background: var(--paper-2)']],
  ] as const)('%s 带可辨边界', (_name, path, selector, expected) => {
    const css = readSource(path);
    for (const declaration of expected) expect(rule(css, selector)).toContain(declaration);
    if (selector === '.segmented {') {
      const selected = rule(css, '.segmented :global(.ant-radio-button-wrapper-checked) {');
      expect(selected).toContain('background: #fff !important');
      expect(selected).toContain('0 0 0 1px var(--line)');
    }
  });

  /*
   * 2026-11-14 改版：交办页「发布」改用全站共用的 `wb-btn wb-btn-primary`（实心主题色底），页内样式
   * 只调尺寸，不另画底色、边框或投影。
   */
  it('交办页「发布」 用的是主动作那一组令牌，不是各页手写一套', () => {
    const tsx = readSource('../../features/work-dispatch/WorkDispatchPage.tsx');
    expect(tsx).toContain('<button className="wb-btn wb-btn-primary" disabled={!canPublish} onClick={publish} type="button">{submitLabel}</button>');
    const shared = rule(readSource('../../shared/ui/workbench.css'), '.wb-btn-primary {');
    for (const declaration of ['background: var(--primary)', 'color: #fff', 'border-color: var(--primary)']) {
      expect(shared).toContain(declaration);
    }
    const pageCss = readSource('../../features/work-dispatch/WorkDispatchPage.module.css');
    const local = rule(pageCss, '.aside > :global(.wb-btn-primary) {');
    for (const property of ['background', 'background-color', 'background-image', 'border', 'border-color', 'box-shadow', 'color']) {
      expect(local).not.toMatch(new RegExp(`(^|[;\\s])${property}\\s*:`));
    }
    // 手写的 48% 蓝内边（1.88:1）不许再出现。
    expect(shared).not.toContain('rgb(47 107 255 / 48%)');
    expect(withoutComments(pageCss)).not.toContain('rgb(47 107 255 / 48%)');
  });
});

describe('焦点环画在可见边框那一层，颜色是主题色', () => {
  it('焦点令牌指向主题色，橙色在渲染路径上清零', () => {
    expect(token('workbench-focus')).toBe('var(--primary)');
    expect(token('primary')).toBe(workbenchTokens.colors.primary);
    /*
     * 注释里可以复盘那个橙色（不然后人不知道为什么换），声明里不许再有——所以先剥注释再查，并且
     * 把整条渲染路径上的样式表都扫一遍，别只盯着改过的那两个文件。
     */
    for (const path of [
      '../../styles.css',
      '../theme.ts',
      '../AppShell.module.css',
      '../AIDock.module.css',
      '../../components/RuntimeViews.module.css',
      '../../features/apps/AppsPage.module.css',
      '../../features/apps/NewSoftwareDialog.module.css',
      '../../features/work-dispatch/WorkDispatchPage.module.css',
      '../../features/work-dispatch/WorkObjectSearchPage.module.css',
      '../../pages/ChatPage.module.css',
      '../../pages/LoginPage.module.css',
      '../../pages/WorkObjectsPage.module.css',
      '../../shared/ui/PlaceholderPage.module.css',
      '../../shared/ui/QueryTable.module.css',
    ]) {
      expect(withoutComments(readSource(path)), path).not.toContain('b44b00');
    }
  });

  it('键盘焦点的兜底环没有被删，且画在元素外沿', () => {
    const declarations = rule(
      GLOBAL_CSS,
      ':where(a, button, input, textarea, summary, [tabindex]):focus-visible',
    );
    expect(declarations).toContain('var(--workbench-focus)');
    expect(/outline-offset:\s*(\d+)px/.exec(declarations)?.[1]).toBe('2');
    expect(declarations).toContain('outline: 2px solid var(--workbench-focus) !important');
  });

  it('antd 输入框：内层 outline 抑制，焦点态画在外层 -outlined 那一层', () => {
    expect(GLOBAL_CSS).toContain(
      '.ant-input-affix-wrapper > input.ant-input:focus-visible',
    );
    expect(
      rule(GLOBAL_CSS, '.ant-input-affix-wrapper > input.ant-input:focus-visible,'),
    ).toContain('outline: none');
    expect(GLOBAL_CSS).toContain('.ant-select-input:focus-visible,');
    // 外层自己已经有 2px 主题色环，兜底 outline 不再叠第二圈同色环（实机截图核对过只剩一圈）。
    expect(rule(GLOBAL_CSS, '.ant-input-outlined:focus-visible,')).toContain(
      'outline: none',
    );
    // antd 外层焦点边框和外晕现在由主题驱动。
    expect(workbenchTheme.token?.colorPrimary).toBe(workbenchTokens.colors.focus);
    expect(workbenchTheme.token?.controlOutline).toBe('rgba(36, 80, 200, 0.16)');
    expect(workbenchTheme.token?.controlOutlineWidth).toBe(3);

  });

  it('自绘输入框保留主题色聚焦边界与外晕，非聚焦为 1px 边界', () => {
    expect(token('workbench-field-face')).toContain(
      'inset 0 0 0 1px var(--line-3)',
    );
    expect(token('workbench-field-face-focus')).toContain(
      'inset 0 0 0 1px var(--primary), 0 0 0 3px var(--primary-ring)',
    );
    expect(rule(readSource('../WorkObjectSearchField.module.css'), '.searchField:focus-within {')).toContain(
      'var(--workbench-field-face-focus)',
    );
    /*
     * 2026-11-14 改版：登录页与交办页改用原生输入框（v7 样板），不再经过兼容令牌，直接写同样的两层：
     * 非聚焦 1px 线条色边框；聚焦时边框换主题色、外加 3px 主题色外晕。
     */
    for (const [path, base, focus] of [
      ['../../pages/LoginPage.module.css', '.field > input {', '.field > input:focus {'],
      ['../../features/work-dispatch/WorkDispatchPage.module.css', '.box {', '.box:focus {'],
    ] as const) {
      const css = readSource(path);
      expect(rule(css, base), path).toContain('border: 1px solid var(--line-2)');
      expect(rule(css, focus), path).toContain('border-color: var(--primary)');
      expect(rule(css, focus), path).toContain('box-shadow: 0 0 0 3px var(--primary-ring)');
    }
  });

  it('登录页输入框不再用看不见的白色凹槽描边', () => {
    const css = readSource('../../pages/LoginPage.module.css');
    expect(rule(css, '.field > input {')).toContain('border: 1px solid var(--line-2)');
    expect(css).not.toContain('var(--workbench-well-edge)');
  });
});

// 输入框的兜底 outline 由 host 抑制，外层承担可见聚焦态；按钮继续使用全局键盘焦点环。
describe('一个控件同一时刻只画一个焦点环', () => {
  it('「外层画环、内层是真控件」的结构统一挂 data-focus-ring="host"，内层兜底 outline 抑制', () => {
    expect(
      rule(
        GLOBAL_CSS,
        "[data-focus-ring='host'] :is(input, textarea, [contenteditable='true']):focus-visible",
      ),
    ).toContain('outline: none');
  });

  /*
   * 抑制与画环是一对，缺后半截就是把焦点态做没了——键盘用户会不知道焦点在哪。所以每挂一处属性，都要
   * 在同一棵子树里指出**谁**画那个环，两边一起钉。
   * 交办对象按获批接线方案改为目录选择组，已无自由文本输入；移除按钮沿用按钮焦点环，不挂输入抑制标记。
   */
  it.each([
    [
      '工作事项搜索凹槽（原顶栏搜索）',
      '../WorkObjectSearchField.tsx',
      '../WorkObjectSearchField.module.css',
      '.searchField:focus-within {',
      'data-focus-ring="host"',
      'var(--workbench-field-face-focus)',
    ],
    [
      'AI 助手页输入卡',
      '../../pages/ChatPage.tsx',
      '../../pages/ChatPage.module.css',
      '.composer:focus-within {',
      'data-focus-ring="host"',
      '0 0 0 4px rgb(139 92 246 / 14%)',
    ],
  ])('%s：焦点标记符合当前控件结构，子树里也确实有人画环', (_name, tsx, css, selector, structure, focusDeclaration) => {
    expect(readSource(tsx)).toContain(structure);
    expect(rule(readSource(css), selector)).toContain(focusDeclaration);
  });

  /*
   * 交办对象是一行已选标签加「选择对象/添加」按钮，里面没有输入框，也不再有画环的凹槽；每个按钮用全局
   * 键盘焦点环，所以这一组不挂输入抑制标记。
   */
  it('交办页「交办对象」：目录选择组不挂输入抑制标记，按钮沿用全局焦点环', () => {
    const tsx = readSource('../../features/work-dispatch/WorkDispatchPage.tsx');
    expect(tsx).toContain('<div aria-label="交办对象（目录选择）" className={styles.chips} role="group">');
    // 「交办对象」这一行（含目录选择组）本身不挂 host：里面只有按钮，按钮要保留全局兜底环。
    expect(tsx).toMatch(/<div className=\{styles\.f\}>\s*<span className=\{styles\.fl\}>交办对象<\/span>\s*<div>\s*<div aria-label="交办对象（目录选择）"/);
  });

  it('AI 助手原生输入卡由外层彩色边画环，textarea 不叠内框', () => {
    const tsx = readSource('../../pages/ChatPage.tsx');
    const css = readSource('../../pages/ChatPage.module.css');
    const shared = readSource('../../shared/ui/workbench.css');
    expect(tsx).toContain('className={`${styles.composer} wb-ring`}');
    expect(tsx).toContain('data-focus-ring="host"');
    expect(tsx).toContain('<textarea');
    expect(rule(css, '.input {')).toContain('outline: 0');
    expect(rule(css, '.input {')).toContain('border: 0');
    expect(rule(shared, '.wb-ring {')).toContain('border: 2px solid transparent');
    expect(rule(shared, '.wb-ringfx > i {')).toContain('background: conic-gradient(');
    expect(rule(shared, '.wb-ring:focus-within .wb-ringfx > i {')).toContain('animation-duration: 7s');
  });

  it.each([
    ['工作事项搜索凹槽（原顶栏搜索）', '../WorkObjectSearchField.module.css', '.searchField:focus-within:has(button:focus-visible) {'],
  ])(
    '%s：里面的按钮拿到焦点时凹槽那圈收回去，一次只留一个环',
    (_name, path, selector) => {
      /*
       * 收回去的是**聚焦那圈**，退回非聚焦的可辨边界，不是退到没有边界；不支持 `:has()` 的浏览器整条
       * 规则失效，退回的是「多一个环」而不是「一个环都没有」。
       */
      const declarations = rule(readSource(path), selector);
      expect(declarations).toContain('var(--workbench-field-face)');
      expect(declarations).not.toContain('var(--workbench-field-face-focus)');
    },
  );

  /*
   * 2026-11-14 v7：交办页与登录页改用原生输入框，聚焦环画在输入框自己身上（主题色边框 + 3px 外晕）。
   * 这些输入框的外层必须挂 `data-focus-ring="host"`，让全局兜底 outline 让位，聚焦时只剩一圈；
   * 大标题输入框自己不画环，故意不挂，由全局兜底 outline 承担唯一的一圈。
   */
  it('交办页与登录页自己画环的输入框不再多画一圈', () => {
    const hostRule = rule(
      GLOBAL_CSS,
      "[data-focus-ring='host'] :is(input, textarea, [contenteditable='true']):focus-visible",
    );
    expect(hostRule).toContain('outline: none');

    const dispatchTsx = readSource('../../features/work-dispatch/WorkDispatchPage.tsx');
    const dispatchCss = readSource('../../features/work-dispatch/WorkDispatchPage.module.css');
    /** 某个控件最近的外层 `.f` 行的开标签。 */
    const rowOpening = (marker: string) => {
      const at = dispatchTsx.indexOf(marker);
      expect(at, marker).toBeGreaterThan(-1);
      const start = dispatchTsx.lastIndexOf('<div className={styles.f}', at);
      return dispatchTsx.slice(start, dispatchTsx.indexOf('>', start) + 1);
    };
    for (const marker of ['id="dispatch-due"', 'id="dispatch-requirement"', 'id="dispatch-receipt"']) {
      expect(rowOpening(marker), marker).toContain('data-focus-ring="host"');
    }
    // 三行里的输入框确实自己画环（边框换主题色 + 外晕）。
    expect(dispatchTsx.match(/className=\{(?:`\$\{)?styles\.box\b/g)?.length).toBeGreaterThanOrEqual(3);
    expect(rule(dispatchCss, '.box:focus {')).toContain('box-shadow: 0 0 0 3px var(--primary-ring)');
    // 目录搜索框：外层 `.wb-field` 用 :focus-within 画环，里面的输入框不再叠兜底 outline。
    expect(dispatchTsx).toContain('<label className={`wb-field ${styles.pickerSearch}`} data-focus-ring="host">');
    expect(rule(readSource('../../shared/ui/workbench.css'), '.wb-field:focus-within {')).toContain('0 0 0 3px var(--primary-ring)');
    // 大标题输入框自己不画环：不挂 host，也没有自己的聚焦规则。
    expect(dispatchTsx).toMatch(/<input aria-label="标题" className=\{styles\.titleInput\}/);
    const titleAt = dispatchTsx.indexOf('<input aria-label="标题"');
    const titleParent = dispatchTsx.lastIndexOf('<div', titleAt);
    expect(dispatchTsx.slice(titleParent, dispatchTsx.indexOf('>', titleParent) + 1)).toBe('<div className={styles.form}>');
    expect(dispatchTsx).toContain('return <div className={styles.composer}>\n    <div className={styles.form}>');
    expect(withoutComments(dispatchCss)).not.toMatch(/\.titleInput:focus/);

    const loginTsx = readSource('../../pages/LoginPage.tsx');
    expect(loginTsx.match(/<label className=\{styles\.field\} data-focus-ring="host"/g)).toHaveLength(2);
    expect(rule(readSource('../../pages/LoginPage.module.css'), '.field > input:focus {')).toContain('box-shadow: 0 0 0 3px var(--primary-ring)');
  });
});
