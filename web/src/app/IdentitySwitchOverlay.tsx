import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { IDENTITY_SWITCHING_NOTICE, requestIdentityRecheck, useIdentityPending } from './identity';
import { useAuthStore } from '../stores/authStore';
import styles from './IdentitySwitchOverlay.module.css';

/**
 * 别的标签页正在登录或退出时，整页盖住并让背后不可操作。只盖不卸：同一个人确认后原样恢复
 * （草稿、弹窗里填了一半的内容都还在）；换了人则按新代次重建。
 */
export function IdentitySwitchOverlay() {
  const switching = useIdentityPending() === 'switching';
  const authenticated = useAuthStore((state) => state.status === 'authenticated');
  const host = useRef<HTMLDivElement>(null);
  const shown = switching && authenticated;

  useEffect(() => {
    if (!shown) return undefined;
    const behind = Array.from(document.body.children)
      .filter((element) => element !== host.current && !element.hasAttribute('inert'));
    for (const element of behind) element.setAttribute('inert', '');
    return () => { for (const element of behind) element.removeAttribute('inert'); };
  }, [shown]);

  if (!shown) return null;
  return createPortal(
    <div aria-modal="true" className={styles.overlay} ref={host} role="alertdialog"
      aria-label="正在确认登录身份">
      <p className={styles.line}>{IDENTITY_SWITCHING_NOTICE}</p>
      <button className={styles.retry} onClick={requestIdentityRecheck} type="button">重新核对</button>
    </div>,
    document.body,
  );
}
