import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import WorkDispatchLandingPage from '../WorkDispatchLandingPage';

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
    <MemoryRouter>
      <WorkDispatchLandingPage />
    </MemoryRouter>,
  );
}

describe('WorkDispatchLandingPage', () => {
  /*
   * 2026-08-27「空状态统一规范」的实质（为什么为空 + 下一步）在返修后由「一句原因 + 一句下一步 +
   * 一排按钮」承担。九类字段的枚举归发布前的交办表单（`P2-FE-DISPATCH-FORM-001`），
   * 2026-08-27 §七 的原文约束的就是「发布前固定展示」，不是这块落地页。
   */
  it('states why it is empty and what to do instead, in one sentence each', () => {
    renderPage();

    expect(
      screen.getByRole('heading', { level: 1, name: '任务交办' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText('任务交办还没有开发，这里派不了活，也存不了草稿。'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        '现在要派活，请照原来的方式在 OA 里发；已经派出去的事在「工作事项」里跟进度。',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
  });

  it('does not offer a dispatch form in this landing bar', () => {
    renderPage();

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /发布/ })).not.toBeInTheDocument();
  });

  it('offers a currently usable alternative path as an action, not as prose', () => {
    renderPage();

    expect(screen.getByRole('link', { name: '去工作事项' })).toHaveAttribute(
      'href',
      '/work-objects',
    );
  });

  /* 「一屏一个重点」：整页只有一个标题，不再是「页面标题 + 三个分段标题 + 九类字段长句」。 */
  it('keeps a single heading and no sectioned explanation blocks', () => {
    renderPage();

    expect(screen.getAllByRole('heading')).toHaveLength(1);
    expect(screen.queryByRole('heading', { level: 2 })).not.toBeInTheDocument();
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
    expect(screen.queryByText('以后会做什么')).not.toBeInTheDocument();
    expect(screen.queryByText('现在怎么办')).not.toBeInTheDocument();
  });

  it('draws its icon as an inline stroke SVG instead of a text glyph', () => {
    const { container } = renderPage();

    const icon = container.querySelector('svg');
    expect(icon).not.toBeNull();
    expect(icon?.getAttribute('stroke')).toBe('currentColor');
    expect(icon?.getAttribute('fill')).toBe('none');
    expect(icon?.getAttribute('aria-hidden')).toBe('true');
  });

  it('keeps internal object names out of the user-facing copy', () => {
    renderPage();

    const text = document.body.textContent ?? '';
    expect(text.length).toBeGreaterThan(0);
    for (const term of FORBIDDEN_INTERNAL_TERMS) {
      expect(text).not.toContain(term);
    }
  });
});
