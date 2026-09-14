import { useLayoutEffect } from 'react';
import { useAuthStore } from '../../../stores/authStore';
import { useDraftSession } from '../../../stores/sessionDraftStore';
import * as draftModule from '../dispatchDraft';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { App as AntApp, ConfigProvider } from 'antd';
import { act, fireEvent, render, renderHook, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WORKBENCH_BUTTON_CONFIG } from '../../../app/theme';
import { DRAFT_STORAGE_KEY, loadDraft, saveDraft, parseDraft } from '../dispatchDraft';
import WorkDispatchPage from '../WorkDispatchPage';

/** vitest 下 `import.meta.url` 是 jsdom 的 URL 实例，`fileURLToPath` 不认，先取 `.href`。 */
function readSource(relativePath: string): string {
  return readFileSync(
    fileURLToPath(new URL(relativePath, import.meta.url).href),
    'utf8',
  );
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

function renderPage() {
  return render(
    <ConfigProvider button={WORKBENCH_BUTTON_CONFIG}>
      <AntApp>
        <MemoryRouter>
          <WorkDispatchPage />
        </MemoryRouter>
      </AntApp>
    </ConfigProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  useAuthStore.getState().markAuthenticated();
});

afterEach(() => vi.restoreAllMocks());

describe('WorkDispatchPage form', () => {
  it('locks the single-entry section order and visible structured labels', () => {
    const { container } = renderPage();
    const brief = screen.getByRole('textbox', { name: '用一句话说明要交办的事' });
    expect(brief).toHaveAttribute('aria-label', '用一句话说明要交办的事');
    expect(container.querySelector('label[for="dispatch-brief"]')).toBeNull();
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.getAllByRole('heading', { level: 2 }).map((node) => node.textContent))
      .toEqual(['基本信息', '交办范围与时限', '办理要求与回执']);
    const ordered = container.querySelectorAll('h2, label, [id$="-label"]');
    expect(Array.from(ordered, (node) => node.textContent?.trim())).toEqual([
      '基本信息', '类型', '标题', '交办范围与时限', '责任人 / 责任部门',
      '截止时间', '可见范围', '交办对象（已解析并去重）', '办理要求与回执',
      '办理要求与交付物', '附件', '回执要求', '提醒策略（可多选，各提醒一次）',
    ]);
    for (const node of ordered) {
      expect(node).toBeVisible();
    }
    expect(brief.compareDocumentPosition(ordered[0]!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  /*
   * 2026-08-27 §七 要求交办的九类字段在**发布前**固定展示。这里逐项钉死，缺一项就红；同时钉死三个
   * 分组标题，防止九项被摊成一列长表单。
   */
  it('shows all nine dispatch fields grouped into the three decided sections', () => {
    renderPage();

    expect(
      screen.getByRole('heading', { level: 1, name: '任务交办' }),
    ).toBeInTheDocument();
    for (const section of ['基本信息', '交办范围与时限', '办理要求与回执']) {
      expect(
        screen.getByRole('heading', { level: 2, name: section }),
      ).toBeInTheDocument();
    }

    expect(screen.getByLabelText('类型')).toBeInTheDocument();
    expect(screen.getByLabelText('标题')).toBeInTheDocument();
    expect(screen.getByLabelText('责任人 / 责任部门')).toBeInTheDocument();
    expect(screen.getByLabelText('截止时间')).toBeInTheDocument();
    expect(screen.getByLabelText('可见范围')).toBeInTheDocument();
    expect(screen.getByLabelText('交办对象（已解析并去重）')).toBeInTheDocument();
    expect(screen.getByLabelText('办理要求与交付物')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '附件' })).toBeInTheDocument();
    expect(screen.getByLabelText('回执要求')).toBeInTheDocument();
    expect(
      screen.getByRole('group', { name: '提醒策略（可多选，各提醒一次）' }),
    ).toBeInTheDocument();
  });

  /* 琥珀提示条是硬要求：草稿在点「发布」之前不下发，这一条必须常驻可见。 */
  it('keeps the amber unpublished-draft banner visible', () => {
    renderPage();

    expect(screen.getByText('草稿尚未发布')).toBeInTheDocument();
    expect(
      screen.getByText('逐项核对无误后，点右下角「发布」才会下发。草稿仅在本次登录期间暂存，刷新或关闭页面会丢失。'),
    ).toBeInTheDocument();
  });

  /*
   * 控件闭集：「类型」是单选下拉、「截止时间」是日期时间选择器、「提醒策略」是多选。三者都**不许**
   * 退化成随手输入的文本框——退化了这条就红。
   */
  it('keeps the closed-set fields as pickers instead of free text boxes', () => {
    renderPage();

    const kind = screen.getByLabelText('类型');
    expect(kind).toHaveAttribute('role', 'combobox');
    expect(kind).toHaveAttribute('readonly');

    expect(screen.getByLabelText('截止时间')).toHaveAttribute(
      'type',
      'datetime-local',
    );
  });

  it('offers exactly the four decided dispatch kinds in the type dropdown', () => {
    renderPage();

    fireEvent.mouseDown(screen.getByLabelText('类型'));

    const listbox = screen.getByRole('listbox');
    expect(
      within(listbox)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['通知', '督办令', '工作任务', '提醒']);
  });

  it('preselects the last three reminder steps and lets each one be toggled', () => {
    renderPage();

    const reminders = screen.getByRole('group', {
      name: '提醒策略（可多选，各提醒一次）',
    });
    const pressed = (name: string) =>
      within(reminders).getByRole('button', { name }).getAttribute('aria-pressed');

    expect(pressed('提前 7 天')).toBe('false');
    expect(pressed('提前 3 天')).toBe('true');
    expect(pressed('提前 1 天')).toBe('true');
    expect(pressed('逾期当天')).toBe('true');

    fireEvent.click(within(reminders).getByRole('button', { name: '提前 7 天' }));
    expect(pressed('提前 7 天')).toBe('true');
    fireEvent.click(within(reminders).getByRole('button', { name: '逾期当天' }));
    expect(pressed('逾期当天')).toBe('false');
  });

  it('deduplicates dispatch targets and counts only what was really added', () => {
    renderPage();

    expect(screen.getByText('还没有交办对象。')).toBeInTheDocument();

    const targetInput = screen.getByLabelText('交办对象（已解析并去重）');
    for (const target of ['办公室', '财务科', '办公室']) {
      fireEvent.change(targetInput, { target: { value: target } });
      fireEvent.click(screen.getByRole('button', { name: '添加' }));
    }

    expect(screen.getByText('已添加交办对象 2 个，重复添加的会自动去掉。')).toBeInTheDocument();
    expect(
      screen.getByText(
        '发布后，2 个交办对象的工作事项中各生成一条；发布前对方不可见。',
      ),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '删除交办对象 财务科' }));
    expect(screen.getByText('已添加交办对象 1 个，重复添加的会自动去掉。')).toBeInTheDocument();
  });

  /*
   * 后端不做（2026-09-02 裁决「界面先行、后端不做」）：AI 生成草稿、附件上传、下发三处都没有接进来。
   * 界面必须**逐处如实说明**，不许摆一个能点却什么也不干的按钮，更不许给一个假的成功。
   *
   * 2026-09-04 返修第 2 条把输入框下方的说明段落删成一行，页脚那句「下发还没有接进来……」也删了。
   * 这条断言随之改口径：**告知没有被删掉，只是换了落点**——生成草稿与添加附件仍是 disabled 且各自
   * 带一句话，下发那句改由点「发布」时的 `role="status"` 当场给出（下一条用例钉死）。
   */
  it('says plainly which parts have no backend instead of faking them', () => {
    renderPage();

    expect(screen.getByRole('button', { name: /生成草稿/ })).toBeDisabled();
    expect(
      screen.getByText('生成草稿还没有接进来；可直接在下方逐项填写。'),
    ).toBeInTheDocument();

    expect(screen.getByRole('button', { name: /添加附件/ })).toBeDisabled();
    expect(
      screen.getByText(
        'Word / PDF / 图片，单个不超过 20 MB；附件还传不上去，可先存草稿。',
      ),
    ).toBeInTheDocument();

    // 删掉的是常驻说明，不是如实告知：这一句现在必须由「发布」当场给出。
    expect(
      screen.queryByText(
        '下发还没有接进来，点「发布」发不出去；「存草稿」只存这台电脑。',
      ),
    ).toBeNull();
    fireEvent.change(screen.getByLabelText('标题'), {
      target: { value: '报送第三季度政务信息' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发布' }));
    expect(screen.getByRole('status')).toHaveTextContent(
      '下发还没有接进来，现在发不出去。',
    );
  });

  /*
   * 返修第 2 条的另外两半，都是可被回滚打红的硬事实：
   * 1. 顶部输入框是本页主入口——`minRows` 至少 3 行，不再是原来的 1 行；
   * 2. 输入框与各字段的描边必须是**可辨边界**（`--workbench-field-face`，实算 ≈4.1:1），
   *    不是原来那道 `rgb(22 29 46 / 11%)` 的发丝边（≈1.1:1）。
   */
  it('makes the draft box the visual anchor with a discernible border', () => {
    renderPage();

    const brief = screen.getByLabelText('用一句话说明要交办的事');
    expect(brief.tagName).toBe('TEXTAREA');
    /*
     * antd 的 `autoSize` 靠布局测量算高，jsdom 量到的一律是 0，DOM 上读不出行数。所以起始行数只能
     * 钉在源码上——这一条同样是可回滚打红的：把 `minRows` 调回 1 或删掉 `autoSize`，它立刻变红。
     */
    const source = readSource('../WorkDispatchPage.tsx');
    const autoSize = /autoSize=\{\{([^}]*)\}\}/.exec(source)?.[1] ?? '';
    expect(Number(/minRows:\s*(\d+)/.exec(autoSize)?.[1] ?? '0')).toBeGreaterThanOrEqual(3);

    const css = readSource('../WorkDispatchPage.module.css');
    const briefRule = /\.briefInput,[^{]*\{([^}]*)\}/.exec(css)?.[1] ?? '';
    expect(briefRule).toContain('var(--workbench-field-face)');
    const fieldRule =
      /\.field :global\(\.ant-input\),[^{]*\{([^}]*)\}/.exec(css)?.[1] ?? '';
    expect(fieldRule).toContain('var(--workbench-field-face)');
    expect(css).not.toContain('inset 0 0 0 1px rgb(22 29 46 / 11%)');
  });

  it('answers the publish button with the real outcome, never with a fake success', () => {
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: '发布' }));
    expect(screen.getByRole('status')).toHaveTextContent(
      '还没有填标题。先把标题填上。',
    );

    fireEvent.change(screen.getByLabelText('标题'), {
      target: { value: '报送第三季度政务信息' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发布' }));

    const notice = screen.getByRole('status');
    expect(notice).toHaveTextContent('下发还没有接进来，现在发不出去。');
    expect(notice.textContent).not.toContain('已发布');
    expect(notice.textContent).not.toContain('发送成功');
  });

  it('saves_only_for_the_current_session_and_restores_after_route_return', () => {
    renderPage();

    fireEvent.change(screen.getByLabelText('标题'), {
      target: { value: '报送第三季度政务信息' },
    });
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }));

    expect(screen.getByRole('status')).toHaveTextContent(
      '草稿已暂存；刷新、关闭页面或退出登录后会丢失。',
    );

    expect(window.localStorage.getItem(DRAFT_STORAGE_KEY)).toBeNull();
    expect(loadDraft(token()).title).toBe(
      '报送第三季度政务信息',
    );
  });

  it('restores a saved normalized snapshot and starts empty in a new session', () => {
    saveDraft(
      parseDraft({
        title: '值班表报送',
        kind: '督办令',
        targets: ['办公室', '办公室', '  '],
        reminders: ['提前 7 天', '这一档不存在'],
      }), token(),
    );
    const restored = renderPage();

    expect(screen.getByLabelText('标题')).toHaveValue('值班表报送');
    expect(screen.getByLabelText('类型')).toHaveAccessibleName('类型');
    expect(screen.getByText('督办令')).toBeInTheDocument();
    expect(screen.getByText('已添加交办对象 1 个，重复添加的会自动去掉。')).toBeInTheDocument();
    const reminders = screen.getByRole('group', {
      name: '提醒策略（可多选，各提醒一次）',
    });
    expect(
      within(reminders)
        .getByRole('button', { name: '提前 7 天' })
        .getAttribute('aria-pressed'),
    ).toBe('true');
    expect(
      within(reminders)
        .getByRole('button', { name: '逾期当天' })
        .getAttribute('aria-pressed'),
    ).toBe('false');
    restored.unmount();

    useAuthStore.getState().markAuthenticated();
    window.localStorage.setItem(DRAFT_STORAGE_KEY, '{ 不是 JSON');
    renderPage();
    expect(screen.getByLabelText('标题')).toHaveValue('');
  });

  it('keeps attachments empty and unavailable with an accessible next step', () => {
    renderPage();
    const attachments = screen.getByRole('group', { name: '附件' });
    expect(attachments).toHaveAccessibleDescription(
      'Word / PDF / 图片，单个不超过 20 MB；附件还传不上去，可先存草稿。',
    );
    expect(within(attachments).getAllByRole('button')).toHaveLength(1);
    expect(within(attachments).getByRole('button', { name: /添加附件/ })).toBeDisabled();
    expect(attachments.textContent?.trim()).toBe('添加附件');
    expect(attachments.querySelector('input[type="file"]')).toBeNull();
    expect(screen.getByText(/Word \/ PDF/).querySelector('svg')).not.toBeNull();
    expect(screen.queryByText(/这是 AI 生成/)).toBeNull();
  });

  it('saves_in_memory_when_browser_storage_is_denied', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('synthetic storage refusal');
    });
    renderPage();
    fireEvent.change(screen.getByLabelText('标题'), { target: { value: '合成草稿' } });
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
    expect(screen.getByRole('status')).toHaveTextContent('草稿已暂存；刷新、关闭页面或退出登录后会丢失。');
    expect(loadDraft(token()).title).toBe('合成草稿');
    expect(screen.getByRole('status').querySelector('svg')).not.toBeNull();
    expect(window.localStorage.getItem(DRAFT_STORAGE_KEY)).toBeNull();
    expect(screen.queryByText(/草稿存在这台电脑上/)).toBeNull();
  });

  it('round-trips only decided choices and literal deduplicated targets through the page', () => {
    saveDraft(parseDraft({
      kind: '未知类型', dueAt: '2026-09-07T16:30',
      targets: [' 办公室 ', '办公室', '', 42, '财务科'],
      reminders: ['提前 7 天', '提前 7 天', '未知提醒'],
    }), token());
    renderPage();
    expect(screen.getByText('通知')).toBeVisible();
    expect(screen.getByLabelText('截止时间')).toHaveValue('2026-09-07T16:30');
    const group = screen.getByRole('group', { name: '提醒策略（可多选，各提醒一次）' });
    expect(within(group).getAllByRole('button').map((node) => node.textContent))
      .toEqual(['提前 7 天', '提前 3 天', '提前 1 天', '逾期当天']);
    for (const button of within(group).getAllByRole('button')) {
      fireEvent.click(button);
    }
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
    const stored = loadDraft(token());
    expect(stored).toMatchObject({
      kind: '通知', dueAt: '2026-09-07T16:30', targets: ['办公室', '财务科'],
      reminders: ['提前 3 天', '提前 1 天', '逾期当天'],
    });
  });

  it('draws its icons as inline stroke SVGs instead of text glyphs', () => {
    const { container } = renderPage();

    const icon = container.querySelector('svg');
    expect(icon).not.toBeNull();
    expect(icon?.getAttribute('stroke')).toBe('currentColor');
    expect(icon?.getAttribute('fill')).toBe('none');
    expect(icon?.getAttribute('aria-hidden')).toBe('true');
  });

  it('keeps internal object names out of the user-facing copy', () => {
    renderPage();

    const checkCopy = () => {
      const text = [document.body.textContent, ...Array.from(
        document.body.querySelectorAll('[aria-label], [title]'),
        (node) => `${node.getAttribute('aria-label') ?? ''} ${node.getAttribute('title') ?? ''}`,
      )].join(' ');
      expect(text.length).toBeGreaterThan(0);
      for (const term of FORBIDDEN_INTERNAL_TERMS) expect(text).not.toContain(term);
    };
    checkCopy();
    fireEvent.click(screen.getByRole('button', { name: '发布' }));
    checkCopy();
    fireEvent.change(screen.getByLabelText('标题'), { target: { value: '合成草稿' } });
    fireEvent.click(screen.getByRole('button', { name: '发布' }));
    checkCopy();
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
    checkCopy();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('synthetic storage refusal');
    });
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
    checkCopy();
  });
});

function token() { const hook = renderHook(useDraftSession); const value = hook.result.current; hook.unmount(); return value; }

it.each(['unknown', 'unauthenticated'] as const)('does_not_render_private_fields_while_identity_is_unknown: %s', (status) => {
  useAuthStore.setState({ status });
  renderPage();
  expect(screen.queryByLabelText('标题')).toBeNull();
  expect(screen.queryByRole('status')).toBeNull();
  act(() => useAuthStore.getState().markAuthenticated());
  expect(screen.getByLabelText('标题')).toHaveValue('');
});

it.each(['{broken', JSON.stringify({ title: 'private-A', targets: ['private-A'], visibleTo: ['private-A'] })])('never_restores_an_unowned_legacy_draft: %s', (legacy) => {
  localStorage.setItem(DRAFT_STORAGE_KEY, legacy);
  renderPage();
  expect(screen.getByLabelText('标题')).toHaveValue('');
  for (const node of document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea')) {
    expect(node.value).not.toContain('private-A');
  }
  expect(document.body.textContent).not.toContain('private-A');
});

it.each(['direct', 'batched', 'pagehide', 'pageshow'])('remounts_private_form_on_direct_and_batched_identity_changes: %s', (transition) => {
  const observations: string[][] = [];
  function Host() {
    const current = useDraftSession();
    useLayoutEffect(() => {
      observations.push([
        ...Array.from(document.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('input, textarea'), (node) => node.value),
        document.body.textContent ?? '',
      ]);
    }, [current]);
    return <WorkDispatchPage />;
  }
  render(<Host />);
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: 'private-A' } });
  fireEvent.change(screen.getByLabelText('交办对象（已解析并去重）'), { target: { value: 'chip-A' } });
  fireEvent.click(screen.getByRole('button', { name: '添加' }));
  fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
  fireEvent.change(screen.getByLabelText('交办对象（已解析并去重）'), { target: { value: 'pending-A' } });
  expect(screen.getByRole('status')).toHaveTextContent('草稿已暂存');
  act(() => {
    if (transition === 'batched') useAuthStore.getState().markUnauthenticated();
    if (transition === 'pagehide' || transition === 'pageshow') window.dispatchEvent(new PageTransitionEvent(transition, { persisted: true }));
    else useAuthStore.getState().markAuthenticated();
  });
  expect(observations).toHaveLength(2);
  for (const value of observations[1]!) {
    expect(value).not.toMatch(/private-A|chip-A|pending-A|草稿已暂存/);
  }
  expect(screen.getByLabelText('标题')).toHaveValue('');
  expect(screen.getByLabelText('交办对象（已解析并去重）')).toHaveValue('');
  expect(screen.queryByRole('button', { name: '删除交办对象 chip-A' })).toBeNull();
  expect(screen.queryByRole('status')).toBeNull();
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: 'B' } });
  fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
  expect(loadDraft(token()).title).toBe('B');
});

it('reports_failed_session_save_without_claiming_success', () => {
  vi.spyOn(draftModule, 'saveDraft').mockReturnValue(false);
  renderPage();
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: 'synthetic' } });
  fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
  expect(screen.getByRole('status')).toHaveTextContent('草稿没存上，请确认登录状态后重试。');
  expect(screen.getByRole('status').textContent).not.toMatch(/已暂存|已发布|已审核/);
});

it('restores_only_the_last_explicit_snapshot_after_unmount', () => {
  const mounted = renderPage();
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: 'saved' } });
  fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: 'unsaved' } });
  mounted.unmount();
  renderPage();
  expect(screen.getByLabelText('标题')).toHaveValue('saved');
  expect(screen.queryByRole('status')).toBeNull();
});
