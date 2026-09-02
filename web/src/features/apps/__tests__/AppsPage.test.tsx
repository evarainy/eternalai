import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import AppsPage from '../AppsPage';

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
      <AppsPage />
    </MemoryRouter>,
  );
}

describe('AppsPage landing', () => {
  it('writes the three decided empty-state sections instead of "暂无数据"', () => {
    renderPage();

    expect(
      screen.getByRole('heading', { level: 1, name: '软件中心' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: '这个页面现在做不了什么' }),
    ).toHaveTextContent('软件中心还没有开发');
    expect(screen.getByRole('region', { name: '现在怎么办' })).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
  });

  it('names the three decided blocks plus self-registration as future work', () => {
    renderPage();

    const planned = screen.getByRole('region', { name: '以后会做什么' });
    expect(planned).toHaveTextContent('业务系统');
    expect(planned).toHaveTextContent('单位软件');
    expect(planned).toHaveTextContent('我的功能');
    expect(planned).toHaveTextContent('提交审核后才对别人可见');
  });

  it('does not offer a create-software button in this landing bar', () => {
    renderPage();

    expect(screen.queryByRole('button', { name: /新建/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /新建/ })).not.toBeInTheDocument();
  });

  it('offers a currently usable alternative path', () => {
    renderPage();

    const alternatives = screen.getByRole('region', { name: '现在怎么办' });
    expect(within(alternatives).getByRole('link', { name: '账号绑定' })).toHaveAttribute(
      'href',
      '/admin/bindings',
    );
    expect(alternatives).toHaveTextContent('OA 请照原来的方式打开');
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
