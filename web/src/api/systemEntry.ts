import { customInstance } from './mutator';

export interface SystemEntryAvailability {
  oa: boolean;
  u8: boolean;
}

export function getSystemEntryAvailability(signal?: AbortSignal): Promise<SystemEntryAvailability> {
  return customInstance({ url: '/api/v1/system-entry', method: 'GET', signal });
}

/** Navigation URLs contain credentials: keep them outside Query and persistent storage. */
export function createSystemEntry(target: 'oa' | 'u8'): Promise<{ url: string }> {
  return customInstance({ url: `/api/v1/system-entry/${target}`, method: 'POST' });
}
