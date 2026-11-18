import { passwordStatus, usePasswordBinding } from './passwordAccounts';
import { serviceStatus, useServiceAccounts } from './serviceAccounts';
import { useZhywBinding } from './zhywBindingState';
import type { AccountStatus, BusinessSystem, PasswordTarget } from './systems';
import styles from './Systems.module.css';

function StatusLine({ status }: { status: AccountStatus }) {
  return (
    <span className={styles.status}>
      <span aria-hidden="true" className={styles.light} data-state={status.light} />
      {status.text}
    </span>
  );
}

function PasswordLight({ target }: { target: PasswordTarget }) {
  return <StatusLine status={passwordStatus(usePasswordBinding(target), target)} />;
}

function ServiceLight() {
  return <StatusLine status={serviceStatus(useServiceAccounts())} />;
}

function IntegratedLight() {
  const binding = useZhywBinding();
  if (binding.isPending) return <StatusLine status={{ light: 'busy', text: '正在读取登录绑定' }} />;
  if (binding.isError) return <StatusLine status={{ light: 'warn', text: '读不到登录绑定' }} />;
  if (!binding.data.configured) return <ServiceLight />;
  return <StatusLine status={binding.data.bound
    ? { light: 'on', text: '已绑定登录账号' }
    : { light: 'off', text: '未绑定登录账号' }} />;
}

/** 一个系统的指示灯加一句状态：绿灯已绑定，红灯要处理，黄灯暂时连不上，空心没绑，虚线还没接入。 */
export function SystemLight({ system }: { system: BusinessSystem }) {
  if (system.account.kind === 'password') return <PasswordLight target={system.account.target} />;
  if (system.account.kind === 'service') return <IntegratedLight />;
  return <StatusLine status={{ light: 'none', text: '待接入' }} />;
}
