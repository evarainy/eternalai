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
    // Everything else in <body> goes inert, including what mounts while the overlay is up.
    const held = new Set<Element>();
    const hold = (element: Element) => {
      if (element === host.current || element.hasAttribute('inert')) return;
      element.setAttribute('inert', '');
      held.add(element);
    };
    Array.from(document.body.children).forEach(hold);
    const observer = new MutationObserver((records) => {
      for (const record of records) record.addedNodes.forEach((node) => {
        if (node instanceof Element) hold(node);
      });
    });
    observer.observe(document.body, { childList: true });
    // No key reaches the page behind (Esc closing a dialog, Ctrl+K opening search). Tab always
    // returns to the retry button; only a plain Enter/Space on that button acts.
    const swallow = (event: KeyboardEvent) => {
      const retry = host.current?.querySelector('button');
      const plain = !event.ctrlKey && !event.metaKey && !event.altKey;
      if (plain && event.target === retry && (event.key === 'Enter' || event.key === ' ')) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (event.key === 'Tab') retry?.focus();
    };
    window.addEventListener('keydown', swallow, true);
    host.current?.querySelector('button')?.focus();
    return () => {
      observer.disconnect();
      window.removeEventListener('keydown', swallow, true);
      for (const element of held) element.removeAttribute('inert');
    };
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
