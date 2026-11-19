import { McpConnectionsPanel } from './McpConnectionsPanel';
import { PasswordBinding, PendingBinding } from './PasswordBinding';
import { ZhywAccountSettings } from './ZhywBinding';
import type { BusinessSystem } from './systems';

/** 一个系统的账号设置：账号密码、业务平台授权，或者还没接入时的占位。软件中心和账号绑定页用的是同一份。 */
export function SystemAccountSettings({ system }: { system: BusinessSystem }) {
  if (system.account.kind === 'password') return <PasswordBinding name={system.name} target={system.account.target} />;
  if (system.account.kind === 'service') {
    return <><ZhywAccountSettings /><McpConnectionsPanel /></>;
  }
  return <PendingBinding />;
}
