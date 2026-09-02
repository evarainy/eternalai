import { render, screen, within } from '@testing-library/react';
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
  it('writes the three decided empty-state sections instead of "暂无数据"', () => {
    renderPage();

    expect(
      screen.getByRole('heading', { level: 1, name: '任务交办' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: '这个页面现在做不了什么' }),
    ).toHaveTextContent('任务交办还没有开发');
    expect(screen.getByRole('region', { name: '现在怎么办' })).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
  });

  it('describes the single ordered entry: one sentence, same-page draft, human publish', () => {
    renderPage();

    const planned = screen.getByRole('region', { name: '以后会做什么' });
    expect(planned).toHaveTextContent('先用一句话把要派的活说清楚');
    expect(planned).toHaveTextContent('在同一页展开成一份草稿');
    expect(planned).toHaveTextContent('要你自己点「发布」才真的派出去');
  });

  it('lists the nine decided draft fields in government wording', () => {
    renderPage();

    const planned = screen.getByRole('region', { name: '以后会做什么' });
    for (const field of [
      '类型',
      '标题',
      '责任人或责任部门',
      '截止时间',
      '办理要求与交付物',
      '回执要求',
      '提醒策略',
      '可见范围',
      '交办对象',
    ]) {
      expect(planned).toHaveTextContent(field);
    }
  });

  it('does not offer a dispatch form in this landing bar', () => {
    renderPage();

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /发布/ })).not.toBeInTheDocument();
  });

  it('offers a currently usable alternative path', () => {
    renderPage();

    const alternatives = screen.getByRole('region', { name: '现在怎么办' });
    expect(within(alternatives).getByRole('link', { name: '工作事项' })).toHaveAttribute(
      'href',
      '/work-objects',
    );
    expect(alternatives).toHaveTextContent('请照原来的方式在 OA 里发');
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
