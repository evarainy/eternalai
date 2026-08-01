import { useAuthStore } from '../stores/authStore';

interface AdminErrorEnvelope {
  detail: {
    code: string;
    message: string;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

function isAdminErrorEnvelope(value: unknown): value is AdminErrorEnvelope {
  if (typeof value !== 'object' || value === null || !('detail' in value)) {
    return false;
  }
  const detail = value.detail;
  return (
    typeof detail === 'object' &&
    detail !== null &&
    'code' in detail &&
    typeof detail.code === 'string' &&
    'message' in detail &&
    typeof detail.message === 'string'
  );
}

export const customInstance = async <T>(
  config: { url: string; method: string; headers?: Record<string, string>; params?: Record<string, unknown>; data?: unknown; signal?: AbortSignal },
): Promise<T> => {
  const { url, method, headers, params, data, signal } = config;
  const authGeneration = useAuthStore.getState().generation;
  const requestHeaders = Object.fromEntries(
    Object.entries(headers ?? {}).filter(
      ([name]) => name.toLowerCase() !== 'x-eternalai-roles',
    ),
  );
  if (data !== undefined && requestHeaders['Content-Type'] === undefined) {
    requestHeaders['Content-Type'] = 'application/json';
  }

  const searchParams = params ? '?' + new URLSearchParams(params as Record<string, string>).toString() : '';
  const response = await fetch(url + searchParams, {
    method,
    headers: requestHeaders,
    body: data !== undefined ? JSON.stringify(data) : undefined,
    signal,
  });
  if (response.status === 401) {
    useAuthStore.getState().markUnauthenticated(authGeneration);
    throw new ApiError(
      response.status,
      'authentication_required',
      'Authentication is required.',
    );
  }
  const payload: unknown = await response.json();
  if (!response.ok) {
    if (isAdminErrorEnvelope(payload)) {
      throw new ApiError(response.status, payload.detail.code, payload.detail.message);
    }
    throw new ApiError(
      response.status,
      `http_${response.status}`,
      `HTTP ${response.status}: ${response.statusText}`,
    );
  }
  return payload as T;
};
