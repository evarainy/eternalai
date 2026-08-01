import { useRoleStore } from '../stores/roleStore';

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

const CSRF_HEADER_NAME = 'X-EternalAI-CSRF';
const SAFE_HTTP_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

function removeHeaderCaseInsensitive(headers: Record<string, string>, name: string): void {
  const normalizedName = name.toLowerCase();
  for (const headerName of Object.keys(headers)) {
    if (headerName.toLowerCase() === normalizedName) {
      delete headers[headerName];
    }
  }
}

export const customInstance = async <T>(
  config: { url: string; method: string; headers?: Record<string, string>; params?: Record<string, unknown>; data?: unknown; signal?: AbortSignal },
): Promise<T> => {
  const { url, method, headers, params, data, signal } = config;
  const requestHeaders = { ...headers };
  delete requestHeaders['X-EternalAI-Roles'];
  delete requestHeaders['x-eternalai-roles'];
  removeHeaderCaseInsensitive(requestHeaders, CSRF_HEADER_NAME);

  if (!SAFE_HTTP_METHODS.has(method.toUpperCase())) {
    requestHeaders[CSRF_HEADER_NAME] = '1';
  }

  const roles = useRoleStore
    .getState()
    .roles.map((role) => role.trim())
    .filter(Boolean);
  if (roles.length > 0) {
    requestHeaders['X-EternalAI-Roles'] = roles.join(',');
  }
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
