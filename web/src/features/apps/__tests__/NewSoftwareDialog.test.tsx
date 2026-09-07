import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { App as AntApp, ConfigProvider } from 'antd';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NewSoftwareDialog } from '../NewSoftwareDialog';
import {
  EMPTY_NEW_SOFTWARE_DRAFT,
  NEW_SOFTWARE_DRAFT_KEY,
  dedupeVisibleTo,
  loadNewSoftwareDraft,
  parseNewSoftwareDraft,
} from '../newSoftwareDraft';

function readSource(relativePath: string): string {
  return readFileSync(fileURLToPath(new URL(relativePath, import.meta.url).href), 'utf8');
}

/** 2026-08-27 §九：前台不得出现这些内部对象名。 */
const FORBIDDEN_INTERNAL_TERMS = [
  'Skill',
  'Capability',
  'App',
  'capability_id',
  'input_schema',
  'intent_tags',
  'WorkCandidate',
  'Work Object',
];

/** 画板 `AppNew.dc.html` 的九类字段，一个不少。 */
const CANVAS_FIELD_LABELS = [
  '这个软件是哪儿来的？',
  '叫什么名字',
  '一句话说明是干什么的',
  '访问地址',
  '归哪个科室管 / 找谁',
  '点开以后怎么显示',
  '用之前要不要先绑这个系统的账号',
  '这个系统会不会改数据',
  '谁能在软件中心看见它',
];

function typeInto(field: HTMLElement, value: string): void {
  fireEvent.change(field, { target: { value } });
}

function renderDialog() {
  return render(
    <ConfigProvider>
      <AntApp>
        <NewSoftwareDialog onClose={vi.fn()} open />
      </AntApp>
    </ConfigProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => vi.restoreAllMocks());

describe('NewSoftwareDialog form', () => {
  it('uses the shared field boundary and one focus host at the canvas input size', () => {
    renderDialog();
    const css = readSource('../AppsPage.module.css');
    const field = /\.field :global\(\.ant-input\),[^{]*\{([^}]*)\}/.exec(css)?.[1] ?? '';
    expect(field).toContain('var(--workbench-field-face)');
    expect(field).toContain('font-size: 17px');
    const focus = /\.field :global\(\.ant-input\):focus,[^{]*\{([^}]*)\}/.exec(css)?.[1] ?? '';
    expect(focus).toContain('var(--workbench-field-face-focus)');
    const well = /\.chipWell\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';
    expect(well).toContain('var(--workbench-field-face)');
    const wellFocus = /\.chipWell:focus-within\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';
    expect(wellFocus).toContain('var(--workbench-field-face-focus)');
    expect(screen.getByLabelText('谁能在软件中心看见它').parentElement)
      .toHaveAttribute('data-focus-ring', 'host');
    expect(css).toMatch(/\.field \.chipInput:global\(\.ant-input\):focus\s*\{[^}]*box-shadow: none !important/);
  });

  it('lays out every field the finalized canvas asks for', async () => {
    renderDialog();
    await waitFor(() => expect(screen.getByRole('dialog')).toBeVisible());

    for (const label of CANVAS_FIELD_LABELS) {
      expect(screen.getByText(label)).toBeVisible();
      expect(screen.getByLabelText(label)).toHaveAccessibleName(label);
    }
  });

  /*
   * 「嵌在工作台里面」须逐系统核验后才可用；OA 的禁嵌证据不能外推到其余系统。界面
   * 只给一句可执行的下一步，不把响应头技术名搬进用户说明。
   */
  it('disables the embedded open mode and says why, in the page itself', async () => {
    renderDialog();
    await waitFor(() => expect(screen.getByRole('dialog')).toBeVisible());

    const embedded = screen.getByRole('radio', { name: '嵌在工作台里面' });
    expect(embedded).toBeDisabled();
    expect(embedded).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByRole('radio', { name: '开新窗口' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(screen.getByText('嵌入方式还没逐个系统核验，请先开新窗口，不会关掉工作台这一页。')).toBeVisible();
    expect(screen.queryByText(/X-Frame-Options|SAMEORIGIN/)).toBeNull();
  });

  it('restores all nine fields and saves only supported choices through the dialog', () => {
    window.localStorage.setItem(NEW_SOFTWARE_DRAFT_KEY, JSON.stringify({
      source: 'unknown-source', name: '合成软件', summary: '合成用途',
      address: 'https://software.synthetic.invalid', owner: '合成科室',
      openMode: 'embedded', binding: 'unknown-binding', risk: 'unknown-risk',
      visibleTo: [' 办公室 ', '办公室', '', 42, '财务科'],
    }));
    renderDialog();
    const sources = screen.getByRole('radiogroup', { name: '这个软件是哪儿来的？' });
    expect(within(sources).getAllByRole('radio')).toHaveLength(2);
    expect(screen.getByRole('radio', { name: /接入单位已有的系统/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: '要绑' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: '只能查，不改' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByLabelText('叫什么名字')).toHaveValue('合成软件');
    expect(screen.getByLabelText('一句话说明是干什么的')).toHaveValue('合成用途');
    expect(screen.getByLabelText('访问地址')).toHaveValue('https://software.synthetic.invalid');
    expect(screen.getByLabelText('归哪个科室管 / 找谁')).toHaveValue('合成科室');
    expect(screen.getAllByText('办公室')).toHaveLength(1);
    fireEvent.click(screen.getByRole('radio', { name: /装单位发布的软件/ }));
    fireEvent.click(screen.getByRole('button', { name: '删除可见范围 财务科' }));
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
    expect(JSON.parse(window.localStorage.getItem(NEW_SOFTWARE_DRAFT_KEY)!)).toEqual({
      source: 'published_software', name: '合成软件', summary: '合成用途',
      address: 'https://software.synthetic.invalid', owner: '合成科室',
      openMode: 'new_window', binding: 'required', risk: 'read_only', visibleTo: ['办公室'],
    });
  });

  it('lets the user switch the two choices that are genuinely theirs to make', () => {
    renderDialog();

    const writesData = screen.getByRole('radio', { name: '会改数据' });
    expect(writesData).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(writesData);
    expect(writesData).toHaveAttribute('aria-checked', 'true');

    const notRequired = screen.getByRole('radio', { name: '不用绑' });
    fireEvent.click(notRequired);
    expect(notRequired).toHaveAttribute('aria-checked', 'true');
  });

  it('de-duplicates the visibility scopes the user types in', () => {
    renderDialog();

    const input = screen.getByLabelText('谁能在软件中心看见它');
    for (const scope of ['财务科', '办公室', '财务科']) {
      typeInto(input, scope);
      fireEvent.click(screen.getByRole('button', { name: /添加/ }));
    }

    expect(screen.getAllByText('财务科')).toHaveLength(1);
    expect(screen.getByText('办公室')).toBeInTheDocument();
  });
});

describe('NewSoftwareDialog draft-only outcome', () => {
  /*
   * 2026-09-02 裁决「界面先行、后端不做」。这里钉死**没有提交路径**：审核按钮不可用，界面写明提交
   * 审核后才对他人可见。谁给它接上一个未经裁决的端点，这条就变红。
   */
  it('cannot submit for review and says so instead of pretending it published', async () => {
    renderDialog();
    await waitFor(() => expect(screen.getByRole('dialog')).toBeVisible());

    expect(screen.getByRole('button', { name: '提交审核' })).toBeDisabled();
    expect(screen.getByText(/提交审核后才对他人可见/)).toBeInTheDocument();
    expect(screen.getByText(
      '提交审核功能还没有接进来，当前只能存这台电脑上的草稿；提交审核后才对他人可见。',
    )).toBeVisible();
    const explanations = Array.from(screen.getByRole('dialog').querySelectorAll('p, b, span'))
      .filter((node) => node.children.length === 0 && /草稿|审核/.test(node.textContent ?? '')
        && node.closest('button') === null);
    expect(explanations).toHaveLength(1);
    expect(explanations[0]!.parentElement?.parentElement?.querySelector('svg')).not.toBeNull();
  });

  it('refuses to save a nameless draft and says what is missing', () => {
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: '存草稿' }));

    expect(screen.getByRole('status')).toHaveTextContent('还没有填名字');
    expect(window.localStorage.getItem(NEW_SOFTWARE_DRAFT_KEY)).toBeNull();
  });

  it('saves the draft to this browser only, and says that is all it did', () => {
    renderDialog();

    typeInto(screen.getByLabelText('叫什么名字'), '财务报销系统');
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }));

    expect(screen.getByRole('status')).toHaveTextContent('草稿存在这台电脑上');
    const stored = window.localStorage.getItem(NEW_SOFTWARE_DRAFT_KEY);
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored ?? '{}')).toMatchObject({ name: '财务报销系统' });
  });

  it('tells the user the draft was lost when the browser refuses to store it', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage disabled');
    });
    renderDialog();

    typeInto(screen.getByLabelText('叫什么名字'), '财务报销系统');
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }));

    expect(screen.getByRole('status')).toHaveTextContent('草稿没存上');
    setItem.mockRestore();
  });

  it('keeps internal object names out of the dialog copy', () => {
    renderDialog();

    const checkCopy = () => {
      const text = [document.body.textContent, ...Array.from(
        document.body.querySelectorAll('[aria-label], [title]'),
        (node) => `${node.getAttribute('aria-label') ?? ''} ${node.getAttribute('title') ?? ''}`,
      )].join(' ');
      expect(text.length).toBeGreaterThan(0);
      for (const term of FORBIDDEN_INTERNAL_TERMS) expect(text).not.toContain(term);
    };
    checkCopy();
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
    checkCopy();
    typeInto(screen.getByLabelText('叫什么名字'), '合成软件');
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
    checkCopy();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Capability storage refusal');
    });
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
    checkCopy();
  });

  it('closes without saving anything when the user cancels', () => {
    const onClose = vi.fn();
    render(
      <ConfigProvider>
        <AntApp>
          <NewSoftwareDialog onClose={onClose} open />
        </AntApp>
      </ConfigProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(NEW_SOFTWARE_DRAFT_KEY)).toBeNull();
  });
});

describe('newSoftwareDraft storage contract', () => {
  it('drops blanks and repeats while keeping the order the user typed', () => {
    expect(dedupeVisibleTo(['办公室', ' ', '财务科', '办公室', ' 财务科 '])).toEqual([
      '办公室',
      '财务科',
    ]);
  });

  it.each([
    ['not an object at all', '"just a string"'],
    ['broken JSON', '{oops'],
    ['an unknown source value', '{"source":"somewhere_else"}'],
  ])('falls back to an empty draft for %s', (_case, stored) => {
    window.localStorage.setItem(NEW_SOFTWARE_DRAFT_KEY, stored);

    expect(loadNewSoftwareDraft().source).toBe(EMPTY_NEW_SOFTWARE_DRAFT.source);
  });

  /*
   * 「嵌在工作台里面」当前不成立。本机草稿是可以手改的，读回来必须拉回「开新窗口」——不让一个界面上
   * 禁用的取值从存储绕进来。
   */
  it('refuses to restore an open mode the interface does not allow', () => {
    expect(parseNewSoftwareDraft({ openMode: 'embedded', name: '财务报销系统' })).toMatchObject(
      { name: '财务报销系统', openMode: 'new_window' },
    );
  });
});
