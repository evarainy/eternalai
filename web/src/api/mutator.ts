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

export interface ApiRequestConfig {
  url: string;
  method: string;
  headers?: Record<string, string>;
  params?: Record<string, unknown>;
  data?: unknown;
  signal?: AbortSignal;
  responseType?: 'json' | 'blob';
  rawBody?: BodyInit;
}

export const requestWithResponse = async <T>(
  config: ApiRequestConfig,
): Promise<{ data: T; etag: string | null }> => {
  const { url, method, headers, params, data, signal, rawBody, responseType = 'json' } = config;
  const authGeneration = useAuthStore.getState().generation;
  const requestHeaders = { ...headers };
  removeHeaderCaseInsensitive(requestHeaders, 'X-EternalAI-Roles');
  removeHeaderCaseInsensitive(requestHeaders, CSRF_HEADER_NAME);
  if (!SAFE_HTTP_METHODS.has(method.toUpperCase())) requestHeaders[CSRF_HEADER_NAME] = '1';
  if (data !== undefined && !(data instanceof Blob) && !Object.keys(requestHeaders).some((key) => key.toLowerCase() === 'content-type')) {
    requestHeaders['Content-Type'] = 'application/json';
  }
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null) query.set(key, String(value));
  }
  const suffix = query.size > 0 ? '?' + query.toString() : '';
  const response = await fetch(url + suffix, {
    method, headers: requestHeaders, body: rawBody ?? (data instanceof Blob ? data : data === undefined ? undefined : JSON.stringify(data)), signal,
  });
  if (response.status === 401) {
    useAuthStore.getState().markUnauthenticated(authGeneration);
    throw new ApiError(401, 'authentication_required', 'Authentication is required.');
  }
  if (method.toUpperCase() === 'POST' && url === '/api/v1/auth/logout' && response.ok && response.status !== 200) {
    throw new ApiError(response.status, 'logout_unconfirmed', 'Logout was not confirmed.');
  }
  if (!response.ok) {
    const errorBody: unknown = response.headers.get('content-type')?.includes('json') ? await response.json() : null;
    if (isAdminErrorEnvelope(errorBody)) throw new ApiError(response.status, errorBody.detail.code, errorBody.detail.message);
    throw new ApiError(response.status, `http_${response.status}`, `HTTP ${response.status}: ${response.statusText}`);
  }
  const mime = response.headers.get('content-type');
  const binary = responseType === 'blob' || (mime !== null && !mime.includes('json'));
  const payload: unknown = response.status === 204 ? null : binary ? await response.blob() : await response.json();
  if (useAuthStore.getState().generation !== authGeneration) {
    throw new ApiError(409, 'identity_changed', '身份已变化，请重新读取。');
  }
  return { data: payload as T, etag: response.headers.get('ETag') };
};

export const customInstance = async <T>(config: ApiRequestConfig): Promise<T> =>
  (await requestWithResponse<T>(config)).data;
