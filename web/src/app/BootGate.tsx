import { useIdentityQuery } from './identity';
import { BACKEND_UNREACHABLE_LINE, BACKEND_UNREACHABLE_RETRY } from './shellLayout';
import styles from './BootGate.module.css';

/**
 * 启动确认还没有答复时渲染的东西：**既不放行受保护内容，也不跳登录页**。
 *
 * 直接跳登录页是原来的 bug（刷新就掉登录态）；直接放行则是在没有服务端确认的情况下自称已登录。
 * 两个都不做，等后端答复。
 */
export function BootGate() {
  const { isError, refetch } = useIdentityQuery();

  if (isError) {
    return (
      <div className={styles.unreachable} data-testid="boot-gate-unreachable" role="alert">
        <span className={styles.unreachableLine}>{BACKEND_UNREACHABLE_LINE}</span>
        <button className={styles.retry} onClick={() => void refetch()} type="button">
          {BACKEND_UNREACHABLE_RETRY}
        </button>
      </div>
    );
  }

  return (
    <div aria-busy="true" className={styles.gate} data-testid="boot-gate">
      <div aria-hidden="true" className={styles.sidebarFrame} />
      <div aria-hidden="true" className={styles.topbarFrame} />
      <div aria-hidden="true" className={styles.stageFrame} />
    </div>
  );
}
