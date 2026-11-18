import { ApiError, customInstance } from './mutator';

export interface SystemEntryAvailability {
  oa: boolean;
  u8: boolean;
}

export function getSystemEntryAvailability(signal?: AbortSignal): Promise<SystemEntryAvailability> {
  return customInstance({ url: '/api/v1/system-entry', method: 'GET', signal });
}

/** Navigation URLs contain credentials: keep them outside Query and persistent storage. */
export function createSystemEntry(target: 'oa' | 'u8' | 'zhyw', captcha?: U8CaptchaAnswer): Promise<{ url: string }> {
  return customInstance({ url: `/api/v1/system-entry/${target}`, method: 'POST', data: captcha });
}

export interface ZhywBinding {
  configured: boolean;
  bound: boolean;
}

const ZHYW_BINDING_URL = '/api/v1/system-entry/zhyw/binding';

export function getZhywBinding(signal?: AbortSignal): Promise<ZhywBinding> {
  return customInstance({ url: ZHYW_BINDING_URL, method: 'GET', signal });
}

export function bindZhyw(loginId: string): Promise<ZhywBinding> {
  return customInstance({ url: ZHYW_BINDING_URL, method: 'PUT', data: { login_id: loginId } });
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

/** Keep business refusals distinct from browser-to-workbench transport errors. */
export function systemEntryErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code.startsWith('entry_')) return error.message;
    if (error.status === 401) return '工作台登录已失效，请重新登录。';
    if (error.code === 'identity_changed') return '登录身份已变化，请重新操作。';
    if (error.code === 'credential_binding_unavailable') return '账号绑定服务尚未就绪，请联系管理员。';
    if (error.code === 'credential_binding_failed') return '账号绑定未能完成，具体原因尚未确认。';
    return `工作台请求失败（HTTP ${error.status}），请稍后重试。`;
  }
  if (error instanceof TypeError && error.message === 'Failed to fetch') {
    return '无法连接工作台服务，请检查网络后重试。';
  }
  if (error instanceof SyntaxError) return '工作台响应无法解析，请稍后重试。';
  return '工作台请求未能完成，具体原因尚未确认。';
}
