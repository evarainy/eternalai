import { render, screen, within } from '@testing-library/react';
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
  it('writes the three decided empty-state sections instead of "暂无数据"', () => {
    renderPage();

    expect(screen.getByRole('heading', { level: 1, name: '消息' })).toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: '这个页面现在做不了什么' }),
    ).toHaveTextContent('消息功能还没有开发');
    expect(screen.getByRole('region', { name: '以后会做什么' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '现在怎么办' })).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
  });

  it('states the human-confirmation boundary in user-facing words', () => {
    renderPage();

    const planned = screen.getByRole('region', { name: '以后会做什么' });
    expect(planned).toHaveTextContent('还没确认的事项');
    expect(planned).toHaveTextContent('要你本人看过并确认，它才会变成正式的工作事项');
    expect(planned).toHaveTextContent('不会替你直接办事');
  });

  it('offers a currently usable alternative path', () => {
    renderPage();

    const alternatives = screen.getByRole('region', { name: '现在怎么办' });
    expect(within(alternatives).getByRole('link', { name: '工作事项' })).toHaveAttribute(
      'href',
      '/work-objects',
    );
    expect(alternatives).toHaveTextContent('直接去 OA 查看');
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
