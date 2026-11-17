import { customInstance } from './mutator';

export interface SystemEntryAvailability {
  oa: boolean;
  u8: boolean;
}

export function getSystemEntryAvailability(signal?: AbortSignal): Promise<SystemEntryAvailability> {
  return customInstance({ url: '/api/v1/system-entry', method: 'GET', signal });
}

/** Navigation URLs contain credentials: keep them outside Query and persistent storage. */
export function createSystemEntry(target: 'oa' | 'u8', captcha?: U8CaptchaAnswer): Promise<{ url: string }> {
  return customInstance({ url: `/api/v1/system-entry/${target}`, method: 'POST', data: captcha });
}

export interface U8CaptchaAnswer {
  guid: string;
  code: string;
}

export interface U8CaptchaView {
  guid: string;
  image: string;
}

/** Challenge data lives only in the open form, outside Query and persistent storage. */
export function getU8Captcha(signal?: AbortSignal): Promise<U8CaptchaView> {
  return customInstance({ url: '/api/v1/system-entry/u8/captcha', method: 'POST', signal });
}

export function readU8Captcha(data: FormData): U8CaptchaAnswer | null {
  const guid = String(data.get('u8_guid') ?? '');
  const code = String(data.get('u8_code') ?? '').trim();
  return guid && code ? { guid, code } : null;
}
