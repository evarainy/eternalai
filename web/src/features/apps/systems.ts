import { oaWorkbenchNavigation } from '../../contracts/runtimeProjection';

/**
 * 软件中心和账号绑定页共用的业务系统清单。
 *
 * 每个系统说清三件事：长什么样（单字、名称、颜色）、账号怎么绑（`account`）、从哪里打开（`systemHref`）。
 * 接入一个新系统：在这里把它的 `account` 从 `none` 换成真实的绑定方式，再给 `systemHref` 加一条地址。
 */
export type PasswordTarget = 'oa' | 'u8';

export type SystemAccount =
  /** 账号加密码，交给凭证绑定接口保存。 */
  | { kind: 'password'; target: PasswordTarget }
  /** 到业务平台本人授权（业务服务连接）。 */
  | { kind: 'service' }
  /** 还没有接入，绑定入口只占位置。 */
  | { kind: 'none' };

export interface BusinessSystem {
  key: 'oa' | 'finance' | 'integrated' | 'data' | 'enforcement';
  mark: string;
  name: string;
  color: string;
  account: SystemAccount;
}

export const SYSTEMS: readonly BusinessSystem[] = [
  { key: 'oa', mark: '文', name: 'OA 办公系统', color: '#2450c8', account: { kind: 'password', target: 'oa' } },
  { key: 'finance', mark: '财', name: '财务软件', color: '#b06a12', account: { kind: 'password', target: 'u8' } },
  { key: 'integrated', mark: '综', name: '综合业务平台', color: '#0f7b7b', account: { kind: 'service' } },
  { key: 'data', mark: '数', name: '数据中台', color: '#3b3fae', account: { kind: 'none' } },
  { key: 'enforcement', mark: '法', name: '执法办案平台', color: '#8a4b2a', account: { kind: 'none' } },
];

/** 打开一个业务系统的地址；还没接进来、或地址没配好时是 `null`。 */
export function systemHref(key: BusinessSystem['key']): string | null {
  if (key === 'oa') {
    const navigation = oaWorkbenchNavigation();
    return navigation.kind === 'allowed' ? navigation.href : null;
  }
  return null;
}

/** 进入系统。单点登录接入后只改这一处。 */
export function openSystem(href: string): void {
  window.open(href, '_blank', 'noopener,noreferrer');
}

/** 指示灯：亮（绿）、要处理（红）、暂时连不上（黄）、没绑（灰）、读取中、没接入。 */
export type LightState = 'on' | 'alert' | 'warn' | 'off' | 'busy' | 'none';

export interface AccountStatus {
  light: LightState;
  text: string;
}
