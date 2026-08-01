import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, customInstance } from '../mutator';
import { useAuthStore } from '../../stores/authStore';

function response(body: unknown, init?: { ok?: boolean; status?: number; statusText?: string }) {
  return {
    ok: init?.ok ?? true,
    status: init?.status ?? 200,
    statusText: init?.statusText ?? 'OK',
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

describe('customInstance authentication boundary', () => {
  beforeEach(() => {
    useAuthStore.setState({ generation: 0, status: 'unauthenticated' });
    vi.restoreAllMocks();
  });

  it('removes client-supplied role claims regardless of header casing', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ items: [] }));
    vi.stubGlobal('fetch', fetchMock);

    await customInstance({
      url: '/api/v1/admin/registry',
      method: 'GET',
      headers: {
        Accept: 'application/json',
        'X-EternalAI-Roles': 'admin',
        'x-EtErNaLaI-rOlEs': 'auditor',
      },
    });

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/admin/registry',
      expect.objectContaining({
        headers: {
          Accept: 'application/json',
        },
      }),
    );
  });

  it('marks the session unauthenticated and throws once on a non-JSON 401', async () => {
    const failedResponse = {
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      json: vi.fn().mockRejectedValue(new Error('not json')),
    } as unknown as Response;
    const fetchMock = vi.fn().mockResolvedValue(failedResponse);
    vi.stubGlobal('fetch', fetchMock);
    useAuthStore.setState({ generation: 1, status: 'authenticated' });

    await expect(
      customInstance({ url: '/api/v1/admin/registry', method: 'GET' }),
    ).rejects.toEqual(
      expect.objectContaining<ApiError>({
        code: 'authentication_required',
        message: 'Authentication is required.',
        name: 'ApiError',
        status: 401,
      }),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(failedResponse.json).not.toHaveBeenCalled();
    expect(useAuthStore.getState().status).toBe('unauthenticated');
  });

  it('ignores a late 401 from an older authentication generation', async () => {
    let resolveFetch!: (value: Response) => void;
    const fetchMock = vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve;
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    useAuthStore.setState({ generation: 1, status: 'authenticated' });

    const oldRequest = customInstance({
      url: '/api/v1/admin/registry',
      method: 'GET',
    });
    useAuthStore.getState().markUnauthenticated();
    useAuthStore.getState().markAuthenticated();
    resolveFetch(response({}, { ok: false, status: 401, statusText: 'Unauthorized' }));

    await expect(oldRequest).rejects.toEqual(
      expect.objectContaining<ApiError>({
        code: 'authentication_required',
        message: 'Authentication is required.',
        name: 'ApiError',
        status: 401,
      }),
    );
    expect(useAuthStore.getState()).toEqual(
      expect.objectContaining({ generation: 3, status: 'authenticated' }),
    );
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
