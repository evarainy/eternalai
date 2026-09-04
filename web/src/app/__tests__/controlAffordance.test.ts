import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { workbenchTokens } from '../theme';

/*
 * 雨爷 2026-09-04 走查第 5 条：「所有右侧页面内的按钮都是纯白色，搭配白色背景区分度不明显」，外加追加
 * 的一条：「点击输入框后周边会显示比视觉上输入框小一圈的橙色边框」。
 *
 * 这两条的判据是 WCAG 2.2 SC 1.4.11 Non-text Contrast：边界若承担「让用户识别这里存在一个可操作控件」
 * 的职责，该边界与相邻色必须 ≥3:1，且**阴影可以增加可见性但不能替代这条 3:1**。所以本文件同时钉两样：
 *
 * 1. **数值**：现役边界令牌合成到两个读物面上，实算 ≥3:1；同时把被换掉的旧值（纯白高光边、48% 蓝）
 *    作为**反例**一并算出来，证明它们确实不达标——不然「换了个颜色」看不出是修复。
 * 2. **落点**：每一处按钮 / 输入框规则真的引用了那组令牌。全站只有一套令牌，改了 CSS 却漏掉某一页，
 *    或者有人把某一处改回只加投影不做边界，这里会红。
 *
 * 反证（把本棒改动回滚后本文件应变红）：`styles.css` 的 `.ant-btn-default` 去掉
 * `var(--workbench-control-ring)` → 「全站按钮」组变红；`--workbench-focus` 改回 `#b44b00` →
 * 焦点环那组变红；`.ant-input-outlined` 那两条删掉 → 输入框那组变红。
 */

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
 * 把 `rgb(r g b / a%)` 这样的一层半透明描边按 source-over 合成到相邻底色上。边界的对比度必须按
 * **合成后**的颜色算：`rgb(255 255 255 / 94%)` 压在近白面上就等于看不见。
 */
function compositeOver(edge: string, backdrop: string): string {
  const matched = /^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*\/\s*([\d.]+)%\s*\)$/.exec(
    edge,
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

/** 边界要与相邻色比，相邻色就是两个读物面：内容面板与最深底图上的大面玻璃。 */
const SURFACES: readonly [string, string][] = [
  ['内容面板', workbenchTokens.colors.surface],
  ['大面玻璃', workbenchTokens.colors.glassSurface],
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
  it.each(SURFACES)('次动作边界在「%s」上过 3:1', (_surfaceName, surface) => {
    expect(
      contrastRatio(
        compositeOver(token('workbench-control-edge'), surface),
        surface,
      ),
    ).toBeGreaterThanOrEqual(3);
  });

  it.each(SURFACES)('主动作边界在「%s」上过 3:1', (_surfaceName, surface) => {
    expect(
      contrastRatio(
        compositeOver(token('workbench-control-edge-primary'), surface),
        surface,
      ),
    ).toBeGreaterThanOrEqual(3);
  });

  /*
   * 反例。这两个值是本轮换掉的旧值：只要有人把它们改回来，上面的断言就会红——但光有上面那组看不出
   * 「为什么非换不可」，所以把不达标这件事本身也钉住。
   */
  it.each([
    ['原按钮白色高光边', 'rgb(255 255 255 / 94%)'],
    ['原「发布」按钮的 48% 蓝内边', 'rgb(47 107 255 / 48%)'],
  ])('%s 在内容面板上达不到 3:1，所以不能当边界用', (_name, edge) => {
    const surface = workbenchTokens.colors.surface;
    expect(contrastRatio(compositeOver(edge, surface), surface)).toBeLessThan(3);
  });

  it('主题色投影取的是调研区间 y 0~3 / blur 2~8 / spread 0 / opacity 20%~26%', () => {
    for (const name of [
      'workbench-control-glow',
      'workbench-control-glow-strong',
    ]) {
      const matched = /^0 (\d)px (\d)px rgb\([\d ]+\/ (\d+)%\)$/.exec(
        token(name),
      );
      expect(matched, name).not.toBeNull();
      const [, offsetY, blur, opacity] = matched as RegExpExecArray;
      expect(Number(offsetY)).toBeGreaterThanOrEqual(0);
      expect(Number(offsetY)).toBeLessThanOrEqual(3);
      expect(Number(blur)).toBeGreaterThanOrEqual(2);
      expect(Number(blur)).toBeLessThanOrEqual(8);
      expect(Number(opacity)).toBeGreaterThanOrEqual(20);
      expect(Number(opacity)).toBeLessThanOrEqual(26);
    }
  });
});

describe('全站按钮统一到同一套三层语言', () => {
  it('次动作：可辨边界 + 轻主题色投影，不是只加投影', () => {
    const declarations = rule(
      GLOBAL_CSS,
      '.ant-btn-default:not(.ant-btn-dangerous),',
    );
    expect(declarations).toContain('var(--workbench-control-ring)');
    expect(declarations).toContain('var(--workbench-control-glow)');
  });

  it('主动作：蓝字 + 蓝色高光内边 + 更重的主题色投影', () => {
    const declarations = rule(
      GLOBAL_CSS,
      '.ant-btn-primary:not(.ant-btn-dangerous) {',
    );
    expect(declarations).toContain('var(--workbench-control-ring-primary)');
    expect(declarations).toContain('var(--workbench-control-glow-strong)');
    expect(declarations).toContain('color: var(--workbench-primary)');
  });

  it('文字按钮：无边框无投影，只有文字色', () => {
    expect(rule(GLOBAL_CSS, '.ant-btn-link:not(.ant-btn-dangerous),')).toContain(
      'box-shadow: none',
    );
  });

  /*
   * 这几处按钮不是 `.ant-btn`（裸 `<button>`、`<a>`、`<Link>`，或者自己改写了 `box-shadow`），全局那条
   * 规则够不着，必须各自引用同一组令牌。少一处就是雨爷说的「这一页的按钮还是看不出来」。
   */
  it.each([
    ['顶栏搜索提交键', '../AppShell.module.css', '.searchSubmit {'],
    ['软件中心「打开」', '../../features/apps/AppsPage.module.css', '.openLink,'],
    ['AI 助手左栏「新对话」', '../../pages/ChatPage.module.css', '.newSessionButton {'],
    ['浮动面板「知道了」', '../AIDock.module.css', '.noticeButton {'],
    ['占位页动作', '../../shared/ui/PlaceholderPage.module.css', '.action {'],
    ['工作事项分段控件', '../../pages/WorkObjectsPage.module.css', '.segmented {'],
  ])('%s 带可辨边界', (_name, path, selector) => {
    expect(rule(readSource(path), selector)).toContain(
      'var(--workbench-control-ring)',
    );
  });

  it.each([
    ['软件中心「去绑账号」', '../../features/apps/AppsPage.module.css', '.bindLink {'],
    [
      '交办页「发布」',
      '../../features/work-dispatch/WorkDispatchPage.module.css',
      ':global(.ant-btn).publishButton.publishButton {',
    ],
  ])('%s 用的是主动作那一组令牌，不是各页手写一套', (_name, path, selector) => {
    const declarations = rule(readSource(path), selector);
    expect(declarations).toContain('var(--workbench-control-ring-primary)');
    expect(declarations).toContain('var(--workbench-control-glow-strong)');
    // 手写的 48% 蓝内边（1.88:1）不许再出现。
    expect(declarations).not.toContain('rgb(47 107 255 / 48%)');
  });
});

describe('焦点环画在可见边框那一层，颜色是主题色', () => {
  it('焦点令牌指向主题色，橙色在渲染路径上清零', () => {
    expect(token('workbench-focus')).toBe('var(--workbench-primary)');
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
    expect(/outline-offset:\s*(\d+)px/.exec(declarations)?.[1]).toBe('3');
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
    expect(rule(GLOBAL_CSS, '.ant-input-outlined,')).toContain(
      'var(--workbench-field-face)',
    );
    expect(rule(GLOBAL_CSS, '.ant-input-outlined:focus,')).toContain(
      'var(--workbench-field-face-focus)',
    );
  });

  it('聚焦环是 2px 主题色，非聚焦是 1px 可辨边界——全站同一对令牌', () => {
    expect(token('workbench-field-face')).toContain(
      'inset 0 0 0 1px var(--workbench-control-edge)',
    );
    expect(token('workbench-field-face-focus')).toContain(
      'inset 0 0 0 2px var(--workbench-primary)',
    );
    for (const [path, selector] of [
      ['../AppShell.module.css', '.searchField:focus-within {'],
      [
        '../../pages/ChatPage.module.css',
        '.sender:global(.ant-sender):focus-within {',
      ],
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

/*
 * 2026-09-04 第三次走查，雨爷截图 `C:.png`：AI 助手页的输入框聚焦后是**两个框叠着**——外层蓝色圆角
 * 框（`.sender:focus-within`，对的），内层一圈内缩、无圆角的框（错的）。
 *
 * 实机 `getComputedStyle` 量到的内层那圈是：`outline: 3px solid rgb(36,80,200)`、`outline-offset: -1px`、
 * `border-radius: 0`，落在 `.ant-sender-input` 这个 textarea 上，也就是上面那条兜底环。第二轮修的时候
 * 抑制清单是**按 antd 类名逐个列**的，`@ant-design/x` 的 `.ant-sender-input` 一个也对不上，于是漏了。
 *
 * 截图里那圈是橙色 `rgb(180,75,0)`（逐像素取样得到，正是已下线的 `#b44b00`），但现役令牌与 dev server
 * 实际响应都已经是 `#2450c8`——那是雨爷浏览器里的**旧样式**，不是仓库里还有橙色。所以本组不再钉颜色
 * （颜色由上面那条「橙色清零」守卫管），只钉**结构**：一个控件同一时刻只画一个环。
 *
 * 反证（把本轮改动回滚后本组应变红）：删掉 `[data-focus-ring='host']` 那条 → 第 1 条红；把任一处
 * `data-focus-ring="host"` 摘掉 → 第 2 条红；`.sender:global(.ant-sender)` 改回单个 `.sender` → 第 3
 * 条红；删掉 `:has(button:focus-visible)` 那三条 → 第 4 条红；删掉 `.ant-select:focus-within` → 第 5
 * 条红；删掉交办页那两条抑制 → 第 6 条红。
 */
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
   */
  it.each([
    [
      '顶栏搜索凹槽',
      '../AppShell.tsx',
      '../AppShell.module.css',
      '.searchField:focus-within {',
    ],
    [
      '交办页「交办对象」凹槽',
      '../../features/work-dispatch/WorkDispatchPage.tsx',
      '../../features/work-dispatch/WorkDispatchPage.module.css',
      '.chipWell:focus-within {',
    ],
    [
      'AI 助手页输入卡',
      '../../pages/ChatPage.tsx',
      '../../pages/ChatPage.module.css',
      '.sender:global(.ant-sender):focus-within {',
    ],
  ])('%s：挂了 host，子树里也确实有人画环', (_name, tsx, css, selector) => {
    expect(readSource(tsx)).toContain('data-focus-ring="host"');
    expect(rule(readSource(css), selector)).toContain(
      'var(--workbench-field-face-focus)',
    );
  });

  it('AI 助手输入卡的规则要两个类才压得过 @ant-design/x 自己那条 .ant-sender', () => {
    const css = readSource('../../pages/ChatPage.module.css');
    expect(css).toContain('.sender:global(.ant-sender) {');
    // 只写一个类时实机生效的是 antd-x 的 `1px solid rgb(0 0 0 / 10%)`，压在白面上只有 1.2:1。
    expect(withoutComments(css)).not.toMatch(/(?:^|\})\s*\.sender\s*\{/);
    expect(rule(css, '.sender:global(.ant-sender) {')).toContain(
      'var(--workbench-field-face)',
    );
  });

  it.each([
    ['顶栏搜索凹槽', '../AppShell.module.css', '.searchField:focus-within:has(button:focus-visible) {'],
    [
      '交办页「交办对象」凹槽',
      '../../features/work-dispatch/WorkDispatchPage.module.css',
      '.chipWell:focus-within:has(button:focus-visible) {',
    ],
    [
      'AI 助手页输入卡',
      '../../pages/ChatPage.module.css',
      '.sender:global(.ant-sender):focus-within:has(button:focus-visible) {',
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

  it('Select 的焦点环压得过 antd 自带的 14% 淡蓝晕', () => {
    /*
     * antd 6 注入的那条特异度更高，实机量到的是 `0 0 0 2px rgb(47 107 255 / 14%)`——远不到 3:1；而
     * `.ant-select-input` 的兜底 outline 又被抑制了，两下一叠等于键盘聚焦时看不见焦点。
     */
    expect(rule(GLOBAL_CSS, '.ant-select:focus-within,')).toContain(
      'var(--workbench-field-face-focus) !important',
    );
  });

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
