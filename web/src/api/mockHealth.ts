export interface HealthResponse {
  status: 'ok';
}

export function mockHealth(): HealthResponse {
  return { status: 'ok' };
}
