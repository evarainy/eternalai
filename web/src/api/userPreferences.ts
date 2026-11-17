import { customInstance } from './mutator';

/**
 * 本人的外观与首页摆法，整份读、整份替换。两个接口都不带任何用户参数：身份只来自会话。
 * 两项内容由前端定义，读回后交给各自 store 的校验函数，这里不解释。
 */
export interface UserPreferencesDocument {
  appearance: Record<string, unknown> | null;
  home_layout: Record<string, unknown> | null;
}

const PATH = '/api/v1/preferences';

export function getUserPreferences(): Promise<UserPreferencesDocument> {
  return customInstance({ url: PATH, method: 'GET' });
}

export function putUserPreferences(document: UserPreferencesDocument): Promise<null> {
  return customInstance({ url: PATH, method: 'PUT', data: document });
}
