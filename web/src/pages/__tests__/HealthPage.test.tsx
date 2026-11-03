import { afterEach, describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import HealthPage from '../HealthPage';

describe('HealthPage', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('displays the real health checks and asks to log in before a self-check', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ status: 'ok', checks: { database: 'ok', background_worker: 'failed' } }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <HealthPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText('数据库')).toBeInTheDocument();
    expect(screen.getByText('后台服务')).toBeInTheDocument();
    expect(screen.getAllByText('异常')).toHaveLength(1);
    expect(screen.getByRole('link', { name: '登录' })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/health', { credentials: 'same-origin' });
  });
});
