import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import * as timeModule from '../dispatchTime';
import { useLayoutEffect } from 'react';
import { useAuthStore } from '../../../stores/authStore';
import { useDraftSession } from '../../../stores/sessionDraftStore';
import * as draftModule from '../dispatchDraft';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { App as AntApp, ConfigProvider } from 'antd';
import { act, fireEvent, render, renderHook, screen, within, waitFor } from '@testing-library/react';
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

function renderPage(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={client}><ConfigProvider button={WORKBENCH_BUTTON_CONFIG} theme={{ token: { motion: false } }}>
      <AntApp>
        <MemoryRouter>
          <WorkDispatchPage />
        </MemoryRouter>
      </AntApp>
    </ConfigProvider></QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal('fetch', vi.fn(directoryFetch));
  useAuthStore.getState().markAuthenticated();
});

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

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
      '截止时间', '可见范围', '交办对象（目录选择）', '办理要求与回执',
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
    expect(screen.getByRole('group', { name: '交办对象（目录选择）' })).toBeInTheDocument();
    expect(screen.getByLabelText('办理要求与交付物')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: '附件' })).toBeInTheDocument();
    expect(screen.getByLabelText('回执要求')).toBeInTheDocument();
    expect(
      screen.getByRole('group', { name: '提醒策略（可多选，各提醒一次）' }),
    ).toBeInTheDocument();
  });

  /* 琥珀提示条是硬要求：草稿在点「发布」之前不下发，这一条必须常驻可见。 */
  it('keeps the amber neutral-content banner visible', () => {
    renderPage();

    expect(screen.getByText('交办内容')).toBeInTheDocument();
    expect(
      screen.getByText('刷新前如已点过发布：结果待确认，请先到工作事项核对。草稿仅在本次登录期间暂存，刷新或关闭页面会丢失。'),
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

    fireEvent.change(screen.getByLabelText('截止时间'), { target: { value: '2026-09-11T01:30' } });
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

  it('deduplicates directory tuples and counts only selected targets', async () => {
    renderPage();
    expect(screen.getByText('还没有交办对象。')).toBeInTheDocument();
    const button = await screen.findByRole('button', { name: '选择 办公室' });
    fireEvent.click(button); fireEvent.click(button);
    expect(screen.getByText('已选择交办对象 1 个，同一对象只保留一次；最多 100 个。')).toBeInTheDocument();
    expect(screen.getByText('发布后，1 个交办对象的工作事项中各生成一条；发布前对方不可见。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '移除交办对象 办公室' }));
    expect(screen.getByText('还没有交办对象。')).toBeInTheDocument();
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

    expect(screen.getByText(/仅记录提醒设置，自动提醒尚未启用/)).toBeVisible();
    expect(screen.getByRole('button', { name: '发布' })).toBeDisabled();
    expect(posts()).toHaveLength(0);
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

  it('answers publish with a verified receipt and blocks empty titles', async () => {
    renderPage(); await selectDepartment();
    fireEvent.click(screen.getByRole('button', { name: '发布' }));
    expect(screen.getByRole('alert')).toHaveTextContent('请核对标题与字段长度');
    expect(posts()).toHaveLength(0);
    fireEvent.change(screen.getByLabelText('标题'), { target: { value: '报送第三季度政务信息' } });
    fireEvent.click(screen.getByRole('button', { name: '发布' }));
    expect(await screen.findByRole('status')).toHaveTextContent('已发布，共1条。');
    expect(posts()).toHaveLength(1);
  });

  it('saves_only_for_the_current_session_and_restores_after_route_return', () => {
    renderPage();

    fireEvent.change(screen.getByLabelText('标题'), {
      target: { value: '报送第三季度政务信息' },
    });
    fireEvent.click(screen.getByRole('button', { name: '存草稿' }));

    expect(screen.getByRole('status')).toHaveTextContent(
      '草稿已暂存；刷新、关闭页面或退出登录后会丢失。交办对象下次需重新选择。',
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
    expect(screen.getByText(/旧内容待重新确认/)).toHaveTextContent('办公室');
    expect(screen.getByRole('button', { name: '发布' })).toBeDisabled();
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
    expect(screen.getByRole('status')).toHaveTextContent('草稿已暂存；刷新、关闭页面或退出登录后会丢失。交办对象下次需重新选择。');
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
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><Host /></MemoryRouter></QueryClientProvider>);
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: 'private-A' } });
  fireEvent.change(screen.getByLabelText('办理要求与交付物'), { target: { value: 'chip-A' } });
  fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
  expect(screen.getByRole('status')).toHaveTextContent('草稿已暂存');
  fireEvent.change(screen.getByLabelText('回执要求'), { target: { value: 'pending-A' } });
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
  expect(screen.getByRole('group', { name: '交办对象（目录选择）' })).toBeEmptyDOMElement();
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
  expect(screen.getByRole('alert')).toHaveTextContent('草稿没存上，请确认登录状态后重试。');
  expect(screen.getByRole('alert').textContent).not.toMatch(/已暂存|已发布|已审核/);
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

const department = { kind: 'department' as const, department_id: 'd1', department_display_name: '办公室' };
const person = { kind: 'user' as const, department_id: 'd1', department_display_name: '办公室', directory_user_id: 'u1', display_name: '同名' };
function options(items: unknown[], kind = 'department', extra = {}) {
  return { kind, items, snapshot_version: 1, unselectable_count: 0, next_cursor: null, has_more: false, ...extra };
}
function response(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }); }
function receipt(count = 1, replayed = false) {
  return { created_count: count, replayed, items: Array.from({ length: count }, (_, i) => ({ work_object_id: `internal-${i}`, state_authority: 'internal', handling_action: 'view_only' })) };
}
function directoryFetch(url: string, init?: RequestInit): Promise<Response> {
  if (init?.method === 'POST') return Promise.resolve(response(receipt(JSON.parse(String(init.body)).targets.length)));
  return Promise.resolve(response(url.includes('kind=user') ? options([person], 'user') : options([department])));
}
function posts() { return vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST'); }
async function selectDepartment() { fireEvent.click(await screen.findByRole('button', { name: '选择 办公室' })); }
async function ready() {
  renderPage(); await selectDepartment();
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: '合成交办' } });
}
function publish() { fireEvent.click(screen.getByRole('button', { name: '发布' })); }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((r) => { resolve = r; }); return { resolve, promise }; }

it('C1 selects_names_after_department and omits undefined parameters', async () => {
  renderPage();
  await screen.findByRole('button', { name: '选择 办公室' });
  expect(vi.mocked(fetch).mock.calls[0]![0]).toBe('/api/v1/work-objects/dispatch-options?kind=department&limit=50');
  expect(screen.queryByText(/同名/)).toBeNull(); expect(posts()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '查看 办公室 人员' }));
  fireEvent.click(await screen.findByRole('button', { name: '选择 同名（办公室，目录编号 u1）' }));
  expect(vi.mocked(fetch).mock.calls[1]![0]).toBe('/api/v1/work-objects/dispatch-options?kind=user&department_id=d1&limit=50');
  expect(screen.getByLabelText('责任人 / 责任部门')).toHaveValue('同名（办公室，目录编号 u1）');
  expect(posts()).toHaveLength(0);
});
it('C2 keeps_same_name_targets_distinct and deduplicates exact tuples', async () => {
  vi.mocked(fetch).mockImplementation((url, init) => String(url).includes('kind=user')
    ? Promise.resolve(response(options([person, { ...person, directory_user_id: 'u2' }], 'user')))
    : directoryFetch(String(url), init));
  await ready();
  fireEvent.click(screen.getByRole('button', { name: '查看 办公室 人员' }));
  for (const id of ['u1', 'u2', 'u1']) fireEvent.click(await screen.findByRole('button', { name: `选择 同名（办公室，目录编号 ${id}）` }));
  publish(); await screen.findByRole('status');
  expect(JSON.parse(String(posts()[0]![1]!.body)).targets).toEqual([
    { kind: 'department', department_id: 'd1' },
    { kind: 'user', department_id: 'd1', directory_user_id: 'u1' },
    { kind: 'user', department_id: 'd1', directory_user_id: 'u2' },
  ]);
});
it('C3 shows_unselectable_count_without_fabricating_people across pages', async () => {
  vi.mocked(fetch).mockImplementation((url, init) => String(url).includes('kind=user')
    ? Promise.resolve(response(options(String(url).includes('cursor=') ? [] : [person], 'user', { unselectable_count: 2, ...(String(url).includes('cursor=') ? {} : { has_more: true, next_cursor: 'opaque+/=' }) })))
    : directoryFetch(String(url), init));
  renderPage(); fireEvent.click(await screen.findByRole('button', { name: '查看 办公室 人员' }));
  fireEvent.click(await screen.findByRole('button', { name: '下一页' }));
  await waitFor(() => expect(screen.queryByRole('button', { name: '下一页' })).toBeNull());
  expect(screen.getByText(/有人员暂不可选/)).toHaveTextContent('（2 人）');
  expect(screen.getAllByRole('button', { name: /^选择 同名/ })).toHaveLength(1);
  expect(String(vi.mocked(fetch).mock.calls[2]![0])).toContain('cursor=opaque%2B%2F%3D');
  expect(posts()).toHaveLength(0);
});
it('C4 reloads_snapshot_and_rejects_late_pages', async () => {
  const late = deferred<Response>(); let count = 0;
  vi.mocked(fetch).mockImplementation((url, init) => {
    if (String(url).includes('kind=user')) return late.promise;
    count++;
    return init?.method === 'POST' ? directoryFetch(String(url), init) : Promise.resolve(response(options([department], 'department', { snapshot_version: count === 1 ? 1 : 2 })));
  });
  await ready();
  fireEvent.click(screen.getByRole('button', { name: '查看 办公室 人员' }));
  fireEvent.click(screen.getByRole('button', { name: '返回部门首页' }));
  await screen.findByText(/目录已更新/);
  expect(screen.getByLabelText('责任人 / 责任部门')).toHaveValue('办公室（待核对）');
  expect(screen.getByRole('button', { name: '发布' })).toBeDisabled();
  await act(async () => late.resolve(response(options([person], 'user'))));
  expect(screen.queryByRole('button', { name: /^选择 同名/ })).toBeNull();
  expect(posts()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '选择 办公室' }));
  expect(screen.getByRole('button', { name: '发布' })).toBeEnabled();
});
it('C5 blocks_new_publish_on_directory_failure without automatic retry', async () => {
  await ready();
  vi.mocked(fetch).mockResolvedValue(response({ detail: { code: 'organization_directory_stale', message: 'synthetic' } }, 503));
  fireEvent.click(screen.getByRole('button', { name: '重新读取目录' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('目录已过期');
  expect(screen.getByLabelText('标题')).toHaveValue('合成交办');
  expect(screen.getByRole('button', { name: '发布' })).toBeDisabled();
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2); expect(posts()).toHaveLength(0);
});
it('C6 requires_legacy_intent_confirmation and sends seven canonical fields', async () => {
  saveDraft(parseDraft({ title: '\u0085合成标题\u001c', requirement: '\u001c要求\u0085', receipt: ' 回执 ', assignee: '旧责任', visibility: '旧可见', targets: ['旧对象'] }), token());
  renderPage(); await selectDepartment();
  expect(screen.getByLabelText('责任人 / 责任部门')).toHaveAttribute('readonly');
  expect(screen.getByLabelText('可见范围')).toHaveAttribute('readonly');
  expect(screen.getByText(/旧内容待重新确认/)).toHaveTextContent('旧责任；可见范围 旧可见；对象 旧对象');
  publish(); expect(posts()).toHaveLength(0);
  fireEvent.click(screen.getByRole('checkbox'));
  publish(); await screen.findByRole('status');
  expect(JSON.parse(String(posts()[0]![1]!.body))).toEqual({ kind: '通知', title: '合成标题', requirement: '要求', receipt_requirement: '回执', due_at: null, reminder_choices: [], targets: [{ kind: 'department', department_id: 'd1' }] });
});
it.each(['title', 'requirement', 'receipt'] as const)('C6 blocks overlong %s while preserving content', async (field) => {
  await ready();
  const names = { title: '标题', requirement: '办理要求与交付物', receipt: '回执要求' };
  const limits = { title: 200, requirement: 10000, receipt: 2000 };
  fireEvent.change(screen.getByLabelText(names[field]), { target: { value: '字'.repeat(limits[field] + 1) } });
  publish(); expect(screen.getByRole('alert')).toHaveTextContent('请核对标题与字段长度'); expect(posts()).toHaveLength(0);
  fireEvent.change(screen.getByLabelText(names[field]), { target: { value: '字'.repeat(limits[field]) } });
  publish(); expect(await screen.findByRole('status')).toHaveTextContent('已发布，共1条');
});
it.each([
  { kind: 'user' }, { items: [{ ...department, kind: 'user' }] }, { items: [{ ...department, department_id: '' }] },
  { items: [{ ...department, department_display_name: null }] }, { snapshot_version: 0 }, { snapshot_version: 1.5 },
  { unselectable_count: -1 }, { unselectable_count: 1 }, { next_cursor: '' }, { has_more: true },
  { items: Array.from({ length: 51 }, () => department) },
])('C7 validates_candidate_response_shape %j', async (bad) => {
  vi.mocked(fetch).mockResolvedValue(response(options([department], 'department', bad)));
  renderPage(); expect(await screen.findByRole('alert')).toHaveTextContent('目录读取失败');
  expect(screen.queryByRole('button', { name: /^选择 / })).toBeNull();
  expect(screen.getByRole('button', { name: '发布' })).toBeDisabled(); expect(posts()).toHaveLength(0);
});
it('P2 freezes_and_retries_same_submission and prevents duplicate pending sends', async () => {
  const pending = deferred<Response>(); await ready();
  vi.mocked(fetch).mockImplementationOnce(() => pending.promise);
  publish(); publish();
  expect(posts()).toHaveLength(1); expect(screen.getByLabelText('标题')).toBeDisabled();
  await act(async () => pending.resolve(new Response('not json')));
  expect(screen.getByRole('alert')).toHaveTextContent('结果待确认');
  expect(screen.queryByRole('status')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '重试原请求' }));
  await screen.findByRole('status');
  expect(posts()[1]![1]).toEqual(posts()[0]![1]);
});
it.each([
  {}, { created_count: 0 }, { replayed: 'true' }, { items: [] },
  { items: [{ work_object_id: '', state_authority: 'internal', handling_action: 'view_only' }] },
  { items: [{ work_object_id: 'x', state_authority: 'external_snapshot', handling_action: 'view_only' }] },
  { items: [{ work_object_id: 'x', state_authority: 'internal', handling_action: 'self_serve' }] },
])('P3 accepts_only_usable_receipts %j', async (bad) => {
  await ready(); vi.mocked(fetch).mockResolvedValueOnce(response(Object.keys(bad).length ? { ...receipt(), ...bad } : {}));
  publish(); expect(await screen.findByRole('alert')).toHaveTextContent('结果待确认');
  expect(screen.queryByRole('status')).toBeNull(); expect(posts()).toHaveLength(1);
});
it('P3 confirms replay and requires explicit new intent after success', async () => {
  await ready(); vi.mocked(fetch).mockResolvedValueOnce(response(receipt(1, true)));
  publish(); expect(await screen.findByRole('status')).toHaveTextContent('已确认原提交，共1条');
  publish(); expect(posts()).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '新建交办' }));
  expect(screen.getByLabelText('标题')).toBeEnabled(); expect(screen.getByRole('button', { name: '发布' })).toBeDisabled();
});
it.each(['success', 'error'])('P4 discards_old_identity_results %s', async (kind) => {
  const client = new QueryClient(); const oldGeneration = useAuthStore.getState().generation;
  client.setQueryData(['work-objects', oldGeneration], { items: [] });
  renderPage(client); await selectDepartment(); fireEvent.change(screen.getByLabelText('标题'), { target: { value: 'old-content' } });
  const pending = deferred<Response>(); vi.mocked(fetch).mockImplementationOnce(() => pending.promise); publish();
  act(() => useAuthStore.getState().markAuthenticated());
  await act(async () => pending.resolve(kind === 'success' ? response(receipt()) : response({ detail: { code: 'dispatch_target_not_found', message: 'private' } }, 404)));
  expect(screen.getByLabelText('标题')).toHaveValue('');
  expect(screen.queryByRole('status')).toBeNull(); expect(screen.queryByText(/先前提交/)).toBeNull();
  expect(screen.getByRole('group', { name: '交办对象（目录选择）' })).toBeEmptyDOMElement();
  expect(client.getQueryState(['work-objects', oldGeneration])?.isInvalidated).toBe(false);
});
it('P5 saves content without selected targets or submission state', async () => {
  await ready(); fireEvent.click(screen.getByRole('button', { name: '存草稿' }));
  expect(screen.getByRole('status')).toHaveTextContent('交办对象下次需重新选择');
  expect(loadDraft(token()).targets).toEqual([]);
  expect(JSON.stringify(loadDraft(token()))).not.toMatch(/department_id|directory_user_id|Idempotency|replayed|internal-0/);
  expect(localStorage.getItem(DRAFT_STORAGE_KEY)).toBeNull();
});
it('P6 refresh_leaves_result_unconfirmed without an automatic POST', async () => {
  const first = renderPage(); await selectDepartment(); fireEvent.change(screen.getByLabelText('标题'), { target: { value: 'synthetic' } });
  vi.mocked(fetch).mockRejectedValueOnce(new Error('offline')); publish(); await screen.findByRole('alert'); first.unmount();
  vi.mocked(fetch).mockClear(); renderPage(); await screen.findByRole('button', { name: '选择 办公室' });
  expect(screen.getByText(/刷新前如已点过发布：结果待确认/)).toBeVisible();
  expect(screen.getByRole('link', { name: '核对工作事项' })).toHaveAttribute('href', '/work-objects');
  expect(posts()).toHaveLength(0); expect(screen.queryByRole('status')).toBeNull();
});
it('P7 rejection_does_not_erase_prior_uncertainty', async () => {
  await ready(); vi.mocked(fetch).mockRejectedValueOnce(new Error('offline')); publish(); await screen.findByRole('alert');
  vi.mocked(fetch).mockResolvedValueOnce(response({ detail: { code: 'dispatch_target_membership_ambiguous', message: 'synthetic' } }, 403));
  fireEvent.click(screen.getByRole('button', { name: '重试原请求' }));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('本次整批未获准'));
  expect(screen.getByRole('alert')).toHaveTextContent('先前提交结果待确认');
  expect(posts()[1]![1]).toEqual(posts()[0]![1]); expect(screen.getByLabelText('标题')).toBeDisabled(); expect(posts()).toHaveLength(2);
});
it('P8 refreshes_work_objects_after_confirmed_publish for only current generation', async () => {
  const client = new QueryClient(); const generation = useAuthStore.getState().generation;
  client.setQueryData(['work-objects', generation], { items: [] });
  client.setQueryData(['work-objects', generation, 'search', 'q'], { items: [] });
  client.setQueryData(['work-objects', generation + 1], { items: ['other'] });
  renderPage(client); await selectDepartment(); fireEvent.change(screen.getByLabelText('标题'), { target: { value: 'synthetic' } }); publish();
  await screen.findByRole('status');
  expect(client.getQueryState(['work-objects', generation])?.isInvalidated).toBe(true);
  expect(client.getQueryState(['work-objects', generation, 'search', 'q'])?.isInvalidated).toBe(true);
  expect(client.getQueryState(['work-objects', generation + 1])?.isInvalidated).toBe(false);
  expect(posts()).toHaveLength(1);
});
it('P9 edits_after_definite_rejection', async () => {
  await ready(); vi.mocked(fetch).mockResolvedValueOnce(response({ detail: { code: 'dispatch_target_not_found', message: 'synthetic' } }, 404));
  publish(); await screen.findAllByRole('alert'); expect(screen.getByLabelText('标题')).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: '重新读取目录' }));
  await selectDepartment(); fireEvent.change(screen.getByLabelText('标题'), { target: { value: '改正后标题' } }); publish(); await screen.findByRole('status');
  expect(posts()[0]![1]!.headers).not.toEqual(posts()[1]![1]!.headers);
  expect(JSON.parse(String(posts()[1]![1]!.body)).title).toBe('改正后标题');
});
it('P9 retries_after_directory_recovery with original frozen body', async () => {
  await ready(); vi.mocked(fetch).mockResolvedValueOnce(response({ detail: { code: 'organization_directory_stale', message: 'synthetic' } }, 503));
  publish(); await screen.findAllByRole('alert');
  expect(screen.getByRole('button', { name: '重试原请求' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '重新读取目录' })); await screen.findByRole('button', { name: '选择 办公室' });
  expect(posts()).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: '重试原请求' })); await screen.findByRole('status');
  expect(posts()[0]![1]).toEqual(posts()[1]![1]);
});
it('T2 posts_time_and_empty_reminders and does not restore cancelled reminders', async () => {
  vi.spyOn(timeModule, 'browserZone').mockReturnValue('Asia/Tokyo'); await ready();
  fireEvent.change(screen.getByLabelText('截止时间'), { target: { value: '2026-09-11T01:30' } });
  expect(screen.getByText('Asia/Tokyo UTC+09:00')).toBeVisible();
  publish(); await screen.findByRole('status'); expect(JSON.parse(String(posts()[0]![1]!.body)).due_at).toBe('2026-09-10T16:30:00Z');
  fireEvent.click(screen.getByRole('button', { name: '新建交办' })); await selectDepartment();
  fireEvent.change(screen.getByLabelText('截止时间'), { target: { value: '' } });
  for (const button of within(screen.getByRole('group', { name: /提醒策略/ })).getAllByRole('button')) expect(button).toBeDisabled();
  publish(); await screen.findByRole('status'); expect(JSON.parse(String(posts()[1]![1]!.body))).toMatchObject({ due_at: null, reminder_choices: [] });
  fireEvent.click(screen.getByRole('button', { name: '新建交办' }));
  fireEvent.change(screen.getByLabelText('截止时间'), { target: { value: '2026-10-01T10:00' } });
  for (const button of within(screen.getByRole('group', { name: /提醒策略/ })).getAllByRole('button')) expect(button).toHaveAttribute('aria-pressed', 'false');
});
it('T3 requires_legacy_timezone_confirmation', async () => {
  vi.spyOn(timeModule, 'browserZone').mockReturnValue('Asia/Tokyo');
  saveDraft(parseDraft({ title: 'legacy', dueAt: '2026-09-11T01:30' }), token());
  renderPage(); await selectDepartment(); publish(); expect(posts()).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: '确认旧截止时间与时区' })); publish();
  await screen.findByRole('status'); expect(JSON.parse(String(posts()[0]![1]!.body)).due_at).toBe('2026-09-10T16:30:00Z');
});
it('T3 converts saved content to another browser zone without changing instant', async () => {
  vi.spyOn(timeModule, 'browserZone').mockReturnValue('Asia/Shanghai');
  saveDraft(parseDraft({ title: 'saved', dueAt: '2026-09-11T01:30', dueInstant: '2026-09-10T16:30:00Z', dueZone: 'Asia/Tokyo', dueOffset: 'UTC+09:00' }), token());
  renderPage(); await selectDepartment(); expect(screen.getByLabelText('截止时间')).toHaveValue('2026-09-11T00:30');
  fireEvent.change(screen.getByLabelText('办理要求与交付物'), { target: { value: '正文修改' } }); publish();
  await screen.findByRole('status'); expect(JSON.parse(String(posts()[0]![1]!.body)).due_at).toBe('2026-09-10T16:30:00Z');
});
it.each(['2026-11-01T05:30:00Z', '2026-11-01T06:30:00Z'])('T5 blocks_dst_until_explicit_choice %s', async (instant) => {
  vi.spyOn(timeModule, 'browserZone').mockReturnValue('America/New_York'); await ready();
  fireEvent.change(screen.getByLabelText('截止时间'), { target: { value: '2026-03-08T02:30' } });
  expect(screen.getByRole('alert')).toHaveTextContent('该日期时间不存在'); publish(); expect(posts()).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('截止时间'), { target: { value: '2026-11-01T01:30' } });
  expect(screen.getByLabelText('选择截止时间偏移')).toHaveValue(''); publish(); expect(posts()).toHaveLength(0);
  expect(screen.getByText('America/New_York UTC-04:00 / UTC-05:00')).toBeVisible();
  fireEvent.change(screen.getByLabelText('选择截止时间偏移'), { target: { value: instant } }); publish();
  await screen.findByRole('status'); expect(JSON.parse(String(posts()[0]![1]!.body)).due_at).toBe(instant);
});

it.each([
  [403, 'directory_scope_denied', '当前身份暂不能'], [403, 'directory_membership_missing', '未找到您的部门'],
  [403, 'directory_membership_ambiguous', '您有多个部门'], [403, 'not_department_head', '没有任务派发权限'],
  [403, 'cross_department_dispatch_denied', '不在可派发范围'], [404, 'dispatch_target_not_found', '部分对象已无法确认'],
  [409, 'organization_directory_snapshot_changed', '目录已更新'], [422, 'dispatch_options_request_invalid', '目录查询信息有误'],
  [503, 'organization_directory_missing', '目录尚未完成首次同步'], [503, 'organization_directory_stale', '目录已过期'],
  [503, 'organization_directory_unavailable', '部门和人员目录暂时不可用'], [503, 'work_object_unavailable', '工作事项服务尚未配置'],
  [500, 'unknown', '目录读取失败'],
] as const)('E2 consumes GET fetch error %s %s', async (status, code, text) => {
  renderPage(); await selectDepartment();
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: '保留正文' } });
  vi.mocked(fetch).mockImplementation(() => Promise.resolve(response({ detail: { code, message: 'private-response-must-not-be-rendered' } }, status)));
  fireEvent.click(screen.getByRole('button', { name: '重新读取目录' }));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(text));
  expect(screen.getByLabelText('标题')).toHaveValue('保留正文'); expect(screen.getByRole('button', { name: '发布' })).toBeDisabled();
  expect(document.body.textContent).not.toContain('private-response'); expect(posts()).toHaveLength(0);
});
it.each([
  [403, 'csrf_validation_failed', '页面校验未通过'], [403, 'directory_membership_missing', '未找到您的部门'],
  [403, 'directory_membership_ambiguous', '您有多个部门'], [403, 'not_department_head', '没有任务派发权限'],
  [403, 'dispatch_target_membership_ambiguous', '本次整批未获准'], [403, 'cross_department_dispatch_denied', '不在可派发范围'],
  [404, 'dispatch_target_not_found', '部分对象已无法确认'], [409, 'idempotency_key_reused', '提交标识与内容不一致'],
  [422, 'idempotency_key_invalid', '提交标识有误'], [422, 'dispatch_request_invalid', '交办信息有误'],
  [503, 'organization_directory_missing', '目录尚未完成首次同步'], [503, 'organization_directory_stale', '目录已过期'],
  [503, 'organization_directory_unavailable', '部门和人员目录暂时不可用'], [503, 'work_object_unavailable', '工作事项服务尚未配置'],
  [503, 'work_object_audit_unavailable', '提交记录暂无法确认'], [503, 'work_object_dispatch_failed', '结果待确认'],
  [500, 'unknown', '结果待确认'],
] as const)('E2 consumes POST fetch error %s %s', async (status, code, text) => {
  await ready(); vi.mocked(fetch).mockResolvedValueOnce(response({ detail: { code, message: 'private-response-must-not-be-rendered' } }, status)); publish();
  await waitFor(() => expect(screen.getAllByRole('alert').some((node) => node.textContent?.includes(text))).toBe(true));
  expect(screen.getByLabelText('标题')).toHaveValue('合成交办'); expect(screen.queryByRole('status')).toBeNull();
  expect(document.body.textContent).not.toContain('private-response'); expect(posts()).toHaveLength(1);
  if (code.startsWith('idempotency_')) expect(screen.queryByRole('button', { name: '重试原请求' })).toBeNull();
});
it.each(['GET', 'POST'])('E2 consumes 401 through existing authentication transport %s', async (method) => {
  await ready(); vi.mocked(fetch).mockResolvedValueOnce(response({ detail: { code: 'authentication_required', message: 'private' } }, 401));
  if (method === 'GET') fireEvent.click(screen.getByRole('button', { name: '重新读取目录' })); else publish();
  await waitFor(() => expect(useAuthStore.getState().status).toBe('unauthenticated'));
  expect(screen.queryByLabelText('标题')).toBeNull(); expect(document.body.textContent).not.toContain('private');
  expect(posts()).toHaveLength(method === 'POST' ? 1 : 0);
});
it('C4 restarts opaque cursor chain after snapshot conflict', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response(options([department], 'department', { has_more: true, next_cursor: 'old-cursor' })));
  await ready();
  vi.mocked(fetch).mockResolvedValueOnce(response({ detail: { code: 'organization_directory_snapshot_changed', message: 'synthetic' } }, 409));
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(3));
  expect(String(vi.mocked(fetch).mock.calls[1]![0])).toContain('cursor=old-cursor');
  expect(String(vi.mocked(fetch).mock.calls[2]![0])).toBe('/api/v1/work-objects/dispatch-options?kind=department&limit=50');
  expect(screen.getByLabelText('责任人 / 责任部门')).toHaveValue('办公室（待核对）'); expect(posts()).toHaveLength(0);
});
it('C7 fails closed on malformed user candidates at page boundary', async () => {
  for (const bad of [{ display_name: null }, { directory_user_id: '' }, { department_id: 'd2' }, { unselectable_count: 0.5 }]) {
    vi.mocked(fetch).mockResolvedValueOnce(response(options([{ ...person, ...bad }], 'user', 'unselectable_count' in bad ? bad : {})));
    await expect(readOptionsForTest()).rejects.toThrow('invalid_dispatch_options');
  }
});
async function readOptionsForTest() { const api = await import('../dispatchApi'); return api.readOptions({ kind: 'user', department_id: 'd1' }); }

it('C2 refuses the 101st selected target while allowing exactly 100', async () => {
  vi.mocked(fetch).mockImplementation((url, init) => {
    if (init?.method === 'POST') return directoryFetch(String(url), init);
    const cursor = String(url).includes('cursor=');
    return Promise.resolve(response(options(Array.from({ length: 50 }, (_, i) => ({ ...department, department_id: `d${i + (cursor ? 50 : 0)}`, department_display_name: `部门${i + (cursor ? 50 : 0)}` })), 'department', cursor ? {} : { has_more: true, next_cursor: 'second' })));
  });
  renderPage(); fireEvent.click(await screen.findByText('下一页'));
  await waitFor(() => expect(document.querySelector('button[aria-label="选择 部门99"]')).toBeInTheDocument());
  const candidates = document.querySelectorAll('button[aria-label^="选择 部门"]');
  expect(candidates).toHaveLength(100);
  act(() => { for (const button of candidates) button.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
  expect(screen.getByText('已选择交办对象 100 个，同一对象只保留一次；最多 100 个。')).toBeVisible();
  vi.mocked(fetch).mockResolvedValueOnce(response(options([{ ...department, department_id: 'd100', department_display_name: '第101个部门' }])));
  fireEvent.click(screen.getByText('重新读取目录'));
  await screen.findByText('第101个部门');
  expect(document.querySelector('button[aria-label="选择 第101个部门"]')).toBeDisabled();
  fireEvent.change(screen.getByLabelText('标题'), { target: { value: '100个对象' } }); fireEvent.click(screen.getByText('发布'));
  expect(await screen.findByRole('status')).toHaveTextContent('已发布，共100条');
  expect(JSON.parse(String(posts()[0]![1]!.body)).targets).toHaveLength(100);
});
it('P8 preserves confirmed publication when active list refresh fails', async () => {
  const { QueryObserver } = await import('@tanstack/react-query');
  const { listWorkObjectsApiV1WorkObjectsGet } = await import('../../../generated/work-objects/work-objects');
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const generation = useAuthStore.getState().generation;
  const key = ['work-objects', generation];
  client.setQueryData(key, { items: [] });
  const observer = new QueryObserver(client, { queryKey: key, queryFn: () => listWorkObjectsApiV1WorkObjectsGet(), staleTime: Infinity });
  const unsubscribe = observer.subscribe(() => {});
  try {
    renderPage(client); await selectDepartment(); fireEvent.change(screen.getByLabelText('标题'), { target: { value: '合成发布' } });
    vi.mocked(fetch).mockImplementation((url, init) => init?.method === 'POST' ? directoryFetch(String(url), init)
      : Promise.resolve(response({ detail: { code: 'organization_directory_unavailable', message: 'synthetic' } }, 503)));
    publish(); await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('已发布，共1条。列表刷新失败'));
    expect(vi.mocked(fetch).mock.calls.some(([url]) => url === '/api/v1/work-objects')).toBe(true);
    expect(posts()).toHaveLength(1); expect(screen.getByRole('button', { name: '发布' })).toBeDisabled();
  } finally { unsubscribe(); }
});
