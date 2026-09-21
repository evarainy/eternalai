import { renderHook } from '@testing-library/react';
import { useDraftSession } from '../../stores/sessionDraftStore';
import { loadDraft, saveDraft, parseDraft } from '../../features/work-dispatch/dispatchDraft';
import { loadNewSoftwareDraft, saveNewSoftwareDraft, parseNewSoftwareDraft } from '../../features/apps/newSoftwareDraft';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, customInstance } from '../mutator';
import { useAuthStore } from '../../stores/authStore';
import { useRoleStore } from '../../stores/roleStore';

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
    const b = draftToken();
    expect(saveDraft(parseDraft({ title: 'B' }), b)).toBe(true);
    expect(saveNewSoftwareDraft(parseNewSoftwareDraft({ name: 'B' }), b)).toBe(true);
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
    expect(loadDraft(b).title).toBe('B');
    expect(loadNewSoftwareDraft(b).name).toBe('B');
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

describe('customInstance CSRF header', () => {
  beforeEach(() => {
    localStorage.clear();
    useRoleStore.setState({ roles: [] });
    vi.restoreAllMocks();
  });

  it('injects the fixed header for POST while preserving other headers', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await customInstance({
      url: '/api/v1/admin/registry',
      method: 'POST',
      headers: { Accept: 'application/json', 'X-Request-ID': 'request-1' },
      data: { name: 'example' },
    });

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/admin/registry',
      expect.objectContaining({
        headers: {
          Accept: 'application/json',
          'X-Request-ID': 'request-1',
          'X-EternalAI-CSRF': '1',
          'Content-Type': 'application/json',
        },
      }),
    );
  });

  it.each(['GET', 'HEAD', 'OPTIONS'])('does not send the header for %s', async (method) => {
    const fetchMock = vi.fn().mockResolvedValue(response({ items: [] }));
    vi.stubGlobal('fetch', fetchMock);

    await customInstance({ url: '/api/v1/admin/registry', method });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(request.headers).not.toHaveProperty('X-EternalAI-CSRF');
  });

  it('removes caller-supplied casing variants before setting the fixed value', async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await customInstance({
      url: '/api/v1/runtime/handle',
      method: 'post',
      headers: {
        'x-eternalai-csrf': 'caller-value',
        'X-ETERNALAI-CsRf': 'second-caller-value',
      },
      data: {},
    });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const csrfHeaders = Object.entries(request.headers as Record<string, string>).filter(
      ([name]) => name.toLowerCase() === 'x-eternalai-csrf',
    );
    expect(csrfHeaders).toEqual([['X-EternalAI-CSRF', '1']]);
  });
});

it('rejects_a_draft_write_from_a_late_success_response', async () => {
  useAuthStore.getState().markAuthenticated();
  const a = draftToken();
  let release!: (value: Response) => void;
  const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise<Response>((resolve) => { release = resolve; }));
  try {
    const pending = customInstance<{ title: string }>({ url: '/api/v1/me', method: 'GET' });
    useAuthStore.getState().markAuthenticated();
    const b = draftToken();
    saveDraft(parseDraft({ title: 'B' }), b);
    saveNewSoftwareDraft(parseNewSoftwareDraft({ name: 'B' }), b);
    release(response({ title: 'late A' }));
    const result = await pending;
    expect(saveDraft(parseDraft(result), a)).toBe(false);
    expect(saveNewSoftwareDraft(parseNewSoftwareDraft({ name: result.title }), a)).toBe(false);
    expect(loadDraft(b).title).toBe('B');
    expect(loadNewSoftwareDraft(b).name).toBe('B');
  } finally { fetchSpy.mockRestore(); }
});

function draftToken() { const hook = renderHook(useDraftSession); const value = hook.result.current; hook.unmount(); return value; }


describe('logout status boundary', () => {
  it.each([201, 202, 204])('logout_status_guard_preserves_other_callers %s', async (status) => {
    const payload = { authenticated: false };
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => response(payload, { status }));
    useAuthStore.setState({ generation: 22, status: 'authenticated' });
    await expect(customInstance({ url: '/api/v1/auth/logout', method: 'POST' })).rejects.toMatchObject({ status, code: 'logout_unconfirmed' });
    expect(useAuthStore.getState().status).toBe('authenticated');
    for (const [url, method] of [['/api/v1/auth/login', 'POST'], ['/other', 'POST'], ['/other', 'GET'], ['/api/v1/auth/logout', 'GET']] as const) {
      await expect(customInstance({ url, method })).resolves.toEqual(payload);
    }
    fetchSpy.mockRestore();
  });
});
