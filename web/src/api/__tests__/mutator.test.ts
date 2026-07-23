import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, customInstance } from '../mutator';
import { useRoleStore } from '../../stores/roleStore';

function response(body: unknown, init?: { ok?: boolean; status?: number; statusText?: string }) {
  return {
    ok: init?.ok ?? true,
    status: init?.status ?? 200,
    statusText: init?.statusText ?? 'OK',
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

describe('customInstance role claims', () => {
  beforeEach(() => {
    localStorage.clear();
    useRoleStore.setState({ roles: [] });
    vi.restoreAllMocks();
  });

  it('injects comma-separated roles while preserving existing headers', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ items: [] }));
    vi.stubGlobal('fetch', fetchMock);
    useRoleStore.getState().setRoles(['admin', ' auditor ']);

    await customInstance({
      url: '/api/v1/admin/registry',
      method: 'GET',
      headers: { Accept: 'application/json' },
    });

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/admin/registry',
      expect.objectContaining({
        headers: {
          Accept: 'application/json',
          'X-EternalAI-Roles': 'admin,auditor',
        },
      }),
    );
  });

  it('does not send the role header when the store is empty', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ items: [] }));
    vi.stubGlobal('fetch', fetchMock);

    await customInstance({ url: '/api/v1/admin/registry', method: 'GET' });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(request.headers).not.toHaveProperty('X-EternalAI-Roles');
  });

  it('preserves the backend business error code and message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        response(
          {
            detail: {
              code: 'role_not_allowed',
              message: 'Management role is required.',
            },
          },
          { ok: false, status: 403, statusText: 'Forbidden' },
        ),
      ),
    );

    await expect(
      customInstance({ url: '/api/v1/admin/registry', method: 'GET' }),
    ).rejects.toEqual(
      expect.objectContaining<ApiError>({
        name: 'ApiError',
        status: 403,
        code: 'role_not_allowed',
        message: 'Management role is required.',
      }),
    );
  });
});
