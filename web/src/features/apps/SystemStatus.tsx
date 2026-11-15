import { passwordStatus, usePasswordBinding } from './passwordAccounts';
import { serviceStatus, useServiceAccounts } from './serviceAccounts';
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

/** 一个系统的指示灯加一句状态：绿灯已绑定，红灯要处理，黄灯暂时连不上，空心没绑，虚线还没接入。 */
export function SystemLight({ system }: { system: BusinessSystem }) {
  if (system.account.kind === 'password') return <PasswordLight target={system.account.target} />;
  if (system.account.kind === 'service') return <ServiceLight />;
  return <StatusLine status={{ light: 'none', text: '待接入' }} />;
}
