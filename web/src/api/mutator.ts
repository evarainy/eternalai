export const customInstance = async <T>(
  config: { url: string; method: string; headers?: Record<string, string>; params?: Record<string, unknown>; data?: unknown; signal?: AbortSignal },
): Promise<T> => {
  const { url, method, headers, params, data, signal } = config;
  const searchParams = params ? '?' + new URLSearchParams(params as Record<string, string>).toString() : '';
  const response = await fetch(url + searchParams, {
    method,
    headers,
    body: data ? JSON.stringify(data) : undefined,
    signal,
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }
  return response.json() as Promise<T>;
};
