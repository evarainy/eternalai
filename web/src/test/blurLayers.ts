import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * 模糊层预算的取证工具。
 *
 * 2026-09-02「视觉方向改为 iOS 26 玻璃拟态」把「单屏带 `backdrop-filter` 的元素不得超过 6 个」定为
 * 硬约束。这里刻意**不**去数源码里出现过几次 `backdrop-filter` 字符串——那样加一行注释就能骗过。
 * 做法是三步：
 *
 * 1. 解析每个 CSS 文件的**规则体**（先剥掉注释、再剥掉嵌套块），挑出真正声明了非 `none` 的
 *    `backdrop-filter` 的**选择器**；CSS Module 的局部名再通过同一文件的 class 映射换成渲染时真正
 *    会出现在 DOM 上的名字。
 * 2. 同样解析 antd / `@ant-design/x` 在运行时注入到 `document` 里的 `<style>`。这两个库自己就带
 *    `backdrop-filter`（`@ant-design/x` 的 `Attachments` 拖放区 `blur(10px)`、antd 的 Drawer 遮罩
 *    `blur(4px)`、Image 预览遮罩、Button 加载进度 `blur(8px)`），只扫仓库自己的 CSS 会漏掉它们，
 *    预算就成了只管自家那一半的假账。
 * 3. 在**真实渲染出来的那一屏**上用 `Element.matches()` 逐元素比对整条选择器，只统计可见（未被
 *    `hidden` / `display: none` 摘掉）的元素。比对整条选择器而不是拆出来的 class 名，是因为运行时
 *    样式里满是 `.ant-drawer-mask.css-dev-only-xxx` 这种复合选择器——只要有一个 class 命中就算数的
 *    话，那个贴在几乎每个 antd 元素上的 `css-dev-only-*` 会把计数直接顶到几十。
 *
 * 因此：给某个面加上模糊，只有当它真的渲染在该屏上时才会计入；反过来，把玻璃材质整体回滚掉，计数
 * 会掉到 0，钉死预期集合的断言随即变红。
 */

const moduleDirectory = dirname(fileURLToPath(import.meta.url));
const sourceRoot = resolve(moduleDirectory, '..');

/**
 * CSS Module 的 `局部名 → 渲染名` 映射。Vitest 默认不处理 CSS，CSS Module 会解析成一个按键回填
 * `_<局部名>_<每文件哈希>` 的对象；生产构建里则是真实的哈希名。两种情况下这里拿到的都是「该文件的
 * 这个局部名在 DOM 上长什么样」，所以比对不依赖具体实现。
 */
const cssModuleMaps = import.meta.glob<Record<string, string>>(
  '../**/*.module.css',
  { eager: true, import: 'default' },
);

/** 非 CSS Module 的全局样式表；里面的 class 名不做哈希。 */
const GLOBAL_STYLESHEETS = ['styles.css'] as const;

/** 运行时注入样式的来源标记；它没有对应的源文件路径。 */
export const RUNTIME_STYLE_SOURCE = '(runtime <style>)';

export interface BlurSource {
  /** 相对 `web/src/` 的文件路径；运行时注入的样式为 `RUNTIME_STYLE_SOURCE`。 */
  file: string;
  /** CSS 里写的选择器（CSS Module 为局部名形态）。 */
  declaredSelector: string;
  /** 能直接喂给 `Element.matches()` 的选择器（局部名已换成渲染名）。 */
  matchSelector: string;
}

export interface BlurLayer extends BlurSource {
  element: Element;
}

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, ' ');
}

/** 把规则体里的嵌套块整块剥掉，只留下本规则的直接声明。 */
function directDeclarations(body: string): string {
  let current = body;
  for (;;) {
    const next = current.replace(/\{[^{}]*\}/g, ' ');
    if (next === current) {
      return next;
    }
    current = next;
  }
}

function declaresBlur(body: string): boolean {
  // 只看直接声明；`none` 与空值都不算一层。嵌套规则由 collectRules 单独展开，不会误算到父规则头上。
  for (const found of directDeclarations(body).matchAll(
    /(?:^|[;\s])(?:-webkit-)?backdrop-filter\s*:([^;]*)/gi,
  )) {
    const value = (found[1] ?? '').trim();
    if (value.length > 0 && !/^none$/i.test(value)) {
      return true;
    }
  }
  return false;
}

interface CssRule {
  selector: string;
  body: string;
}

/** CSS 嵌套：子选择器里的 `&` 换成父选择器，没有 `&` 就按后代组合。 */
function nestSelector(parent: string, child: string): string {
  if (parent.length === 0) {
    return child;
  }
  if (child.includes('&')) {
    return child.split('&').join(parent);
  }
  return `${parent} ${child}`;
}

function collectRules(css: string, into: CssRule[], parent = ''): void {
  let cursor = 0;
  while (cursor < css.length) {
    const open = css.indexOf('{', cursor);
    if (open === -1) {
      return;
    }
    // 嵌套块前面可能还挂着父规则自己的声明（`color: green; .nested { … }`），选择器只取最后一个
    // `;` 之后的那一段。
    const rawSelector = (css.slice(cursor, open).split(';').pop() ?? '').trim();
    let depth = 1;
    let scan = open + 1;
    while (scan < css.length && depth > 0) {
      if (css[scan] === '{') {
        depth += 1;
      } else if (css[scan] === '}') {
        depth -= 1;
      }
      scan += 1;
    }
    const body = css.slice(open + 1, Math.max(open + 1, scan - 1));
    if (rawSelector.startsWith('@')) {
      // `@media` / `@supports` 这类条件组不改变选择器，直接带着当前父选择器往里走。
      collectRules(body, into, parent);
    } else {
      const selector = nestSelector(parent, rawSelector);
      into.push({ body, selector });
      if (body.includes('{')) {
        // CSS 嵌套：子块自己也是一条规则，否则嵌在里面的玻璃会整条漏掉。
        collectRules(body, into, selector);
      }
    }
    cursor = scan;
  }
}

/** 拆逗号分组，同时避开 `:not(a, b)` 这类括号里的逗号。 */
function splitSelectorList(selector: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let index = 0; index < selector.length; index += 1) {
    const character = selector[index];
    if (character === '(' || character === '[') {
      depth += 1;
    } else if (character === ')' || character === ']') {
      depth -= 1;
    } else if (character === ',' && depth === 0) {
      parts.push(selector.slice(start, index));
      start = index + 1;
    }
  }
  parts.push(selector.slice(start));
  return parts
    .map((part) => part.replace(/\s+/g, ' ').trim())
    .filter((part) => part.length > 0);
}

/**
 * 从一段 CSS 里取出「声明了模糊」的选择器。导出是为了让检查本身也能被检查：注释里的
 * `backdrop-filter`、`backdrop-filter: none`、以及嵌套在子块里的声明都不得算到父规则头上。
 */
export function blurDeclaringSelectors(css: string): string[] {
  const rules: CssRule[] = [];
  collectRules(stripComments(css), rules);
  const selectors = new Set<string>();
  for (const rule of rules) {
    if (!declaresBlur(rule.body)) {
      continue;
    }
    for (const single of splitSelectorList(rule.selector)) {
      selectors.add(single);
    }
  }
  return [...selectors];
}

/** 伪元素永远不会是一个可比对的元素；`matches()` 也不接受它们。 */
function stripPseudoElements(selector: string): string {
  return selector
    .replace(/::[-\w]+(\([^()]*\))?/g, '')
    .replace(
      /:(before|after|first-line|first-letter|placeholder|selection|marker|backdrop)\b(\([^()]*\))?/gi,
      '',
    )
    .replace(/\s+/g, ' ')
    .trim();
}

function toMatchSelector(
  selector: string,
  classMap: Record<string, string> | null,
): string {
  const withGlobals = selector.replace(
    /:global\(([^()]*)\)/g,
    (_match, inner: string) => ` ${inner} `,
  );
  const mapped =
    classMap === null
      ? withGlobals
      : withGlobals.replace(
          /\.([A-Za-z_][\w-]*)/g,
          (_match, name: string) => `.${classMap[name] ?? name}`,
        );
  return stripPseudoElements(mapped);
}

function pushRule(
  into: BlurSource[],
  file: string,
  declaredSelector: string,
  classMap: Record<string, string> | null,
): void {
  const matchSelector = toMatchSelector(declaredSelector, classMap);
  if (matchSelector.length === 0) {
    return;
  }
  into.push({ declaredSelector, file, matchSelector });
}

let cachedFileRules: BlurSource[] | null = null;

/** 仓库自己的 CSS 里声明了模糊的规则。文件内容不会在一次运行里变化，所以只解析一次。 */
export function fileBlurRules(): BlurSource[] {
  if (cachedFileRules !== null) {
    return cachedFileRules;
  }
  const rules: BlurSource[] = [];

  for (const [globKey, classMap] of Object.entries(cssModuleMaps)) {
    const absolutePath = resolve(moduleDirectory, globKey);
    const file = absolutePath.slice(sourceRoot.length + 1).split('\\').join('/');
    const css = readFileSync(absolutePath, 'utf8');
    for (const selector of blurDeclaringSelectors(css)) {
      pushRule(rules, file, selector, classMap);
    }
  }

  for (const relativePath of GLOBAL_STYLESHEETS) {
    const css = readFileSync(resolve(sourceRoot, relativePath), 'utf8');
    for (const selector of blurDeclaringSelectors(css)) {
      pushRule(rules, relativePath, selector, null);
    }
  }

  cachedFileRules = rules;
  return rules;
}

/**
 * antd / `@ant-design/x` 用 CSS-in-JS 在运行时把样式注入 `document.head`，注入内容随本屏实际用到的
 * 组件而定，因此每次都重新解析、不缓存。这些选择器是全局形态，不做 class 名映射。
 */
export function runtimeBlurRules(owner: Document): BlurSource[] {
  const rules: BlurSource[] = [];
  for (const styleElement of owner.querySelectorAll('style')) {
    for (const selector of blurDeclaringSelectors(styleElement.textContent ?? '')) {
      pushRule(rules, RUNTIME_STYLE_SOURCE, selector, null);
    }
  }
  return rules;
}

function isRendered(element: Element): boolean {
  let current: Element | null = element;
  while (current !== null) {
    if (current instanceof HTMLElement) {
      if (current.hidden || current.style.display === 'none') {
        return false;
      }
    }
    // `aria-hidden` 只影响可访问性树，元素照样进合成层，因此不据此排除。
    current = current.parentElement;
  }
  return true;
}

function inlineBlur(element: Element): boolean {
  if (!(element instanceof HTMLElement)) {
    return false;
  }
  const declared = `${element.style.getPropertyValue('backdrop-filter')} ${
    element.style.getPropertyValue('-webkit-backdrop-filter')
  }`.trim();
  return declared.length > 0 && !/^none$/i.test(declared);
}

/**
 * 本次比对中 `Element.matches()` 拒绝解析的模糊选择器。静默跳过就等于给预算开了个看不见的口子，
 * 所以把它们暴露出来，由 `blurBudget.test.tsx` 断言为空。
 */
export function unparsableBlurSelectors(container: HTMLElement): BlurSource[] {
  const unparsable: BlurSource[] = [];
  for (const rule of [
    ...fileBlurRules(),
    ...runtimeBlurRules(container.ownerDocument),
  ]) {
    try {
      container.matches(rule.matchSelector);
    } catch {
      unparsable.push(rule);
    }
  }
  return unparsable;
}

/** 统计一屏里真正带模糊的元素。同一元素命中多条模糊规则也只算一层。 */
export function findBlurLayers(container: HTMLElement): BlurLayer[] {
  const rules = [
    ...fileBlurRules(),
    ...runtimeBlurRules(container.ownerDocument),
  ];
  const layers: BlurLayer[] = [];
  const candidates: Element[] = [container, ...container.querySelectorAll('*')];
  for (const element of candidates) {
    if (!isRendered(element)) {
      continue;
    }
    let matched: BlurSource | undefined;
    for (const rule of rules) {
      let hit = false;
      try {
        hit = element.matches(rule.matchSelector);
      } catch {
        // 解析不了的选择器由 unparsableBlurSelectors() 单独报出来，这里不吞成「没命中」。
        hit = false;
      }
      if (hit) {
        matched = rule;
        break;
      }
    }
    if (matched === undefined && inlineBlur(element)) {
      matched = {
        declaredSelector: 'style=""',
        file: '(inline style)',
        matchSelector: 'style=""',
      };
    }
    if (matched !== undefined) {
      layers.push({ ...matched, element });
    }
  }
  return layers;
}

/** 把一屏的模糊层写成可直接断言的字符串列表，例如 `app/AppShell.module.css .sidebar`。 */
export function describeBlurLayers(container: HTMLElement): string[] {
  return findBlurLayers(container)
    .map((layer) => `${layer.file} ${layer.declaredSelector}`)
    .sort();
}
