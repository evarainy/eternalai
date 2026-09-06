import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import MessagesPage from '../MessagesPage';

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
      <MessagesPage />
    </MemoryRouter>,
  );
}

describe('MessagesPage placeholder', () => {
  /*
   * 2026-08-27「空状态统一规范」要求说明**为什么为空**和**下一步能做什么**，不得只写「暂无数据」。
   * 返修后这两件事由「一句原因 + 一句下一步 + 一排按钮」承担，不再是三个带标题的区块——所以下面钉的
   * 是**这两项实质**，而不是原来的三个 `region` 结构。
   */
  it('states why it is empty and what to do instead, in one sentence each', () => {
    renderPage();

    expect(screen.getByRole('heading', { level: 1, name: '消息' })).toBeInTheDocument();
    expect(screen.getByText('消息功能还没有开发，这里收不到也发不出消息。')).toBeInTheDocument();
    expect(
      screen.getByText('OA 里的通知和待办，请照原来的方式直接去 OA 查看。'),
    ).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
  });

  it('offers a currently usable alternative path as an action, not as prose', () => {
    renderPage();

    expect(screen.getByRole('link', { name: '去工作事项' })).toHaveAttribute(
      'href',
      '/work-objects',
    );
  });

  /*
   * 「一屏一个重点」：整页只有一个标题，不再是「页面标题 + 三个分段标题」。低数字素养用户的界面靠层级
   * 对比，不靠满屏说明；下面这条钉死不许再长回三段结构。
   */
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
