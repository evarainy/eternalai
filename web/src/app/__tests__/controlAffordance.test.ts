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
    ['软件中心「打开」', '../../features/apps/AppsPage.module.css', '.openLink,', ['background: var(--paper)', 'border: 1px solid var(--line-2)']],
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

  it.each([
    ['软件中心「去绑账号」', '../../features/apps/AppsPage.module.css', '.bindLink {', ['background: var(--primary)', 'color: #fff', 'border-color: var(--primary)']],
    [
      '交办页「发布」',
      '../../features/work-dispatch/WorkDispatchPage.module.css',
      ':global(.ant-btn).publishButton.publishButton {',
      ['var(--workbench-control-ring-primary)', 'var(--workbench-control-glow-strong)'],
    ],
  ] as const)('%s 用的是主动作那一组令牌，不是各页手写一套', (_name, path, selector, expected) => {
    const declarations = rule(readSource(path), selector);
    for (const declaration of expected) expect(declarations).toContain(declaration);
    // 手写的 48% 蓝内边（1.88:1）不许再出现。
    expect(declarations).not.toContain('rgb(47 107 255 / 48%)');
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
    for (const [path, selector] of [
      ['../WorkObjectSearchField.module.css', '.searchField:focus-within {'],
      ['../../pages/LoginPage.module.css', '.form :global(.ant-input):focus,'],
      [
        '../../features/work-dispatch/WorkDispatchPage.module.css',
        '.chipWell:focus-within {',
      ],
    ] as const) {
      expect(rule(readSource(path), selector), path).toContain(
        'var(--workbench-field-face-focus)',
      );
    }
  });

  it('登录页输入框不再用看不见的白色凹槽描边', () => {
    const css = readSource('../../pages/LoginPage.module.css');
    expect(rule(css, '.form :global(.ant-input-affix-wrapper),')).toContain(
      'var(--workbench-field-face)',
    );
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
      '交办页「交办对象」凹槽',
      '../../features/work-dispatch/WorkDispatchPage.tsx',
      '../../features/work-dispatch/WorkDispatchPage.module.css',
      '.chipWell:focus-within {',
      'role="group" aria-label="交办对象（目录选择）"',
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
    if (structure === 'role="group" aria-label="交办对象（目录选择）"') {
      expect(readSource(tsx)).not.toContain('data-focus-ring="host"');
    }
    expect(rule(readSource(css), selector)).toContain(focusDeclaration);
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
    [
      '交办页「交办对象」凹槽',
      '../../features/work-dispatch/WorkDispatchPage.module.css',
      '.chipWell:focus-within:has(button:focus-visible) {',
    ],
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

  it('交办页两个输入框自己不再多画一圈', () => {
    const css = readSource(
      '../../features/work-dispatch/WorkDispatchPage.module.css',
    );
    // 凹槽已经画了环，里面的输入框聚焦时不再画第二圈。
    expect(rule(css, '.chipInput:focus,')).toContain('box-shadow: none');
    // 一句话输入框的环画在它自己身上，不再叠外面那圈 3px 兜底 outline。
    expect(rule(css, '.briefInput:focus-visible,')).toContain('outline: none');
  });
});
