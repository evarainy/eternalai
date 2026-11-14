import OACredentialBindingCard from '../../components/OACredentialBindingCard';
import { PageFrame } from '../../shared/ui/PageFrame';
import { McpConnectionsPanel } from './McpConnectionsPanel';
import { McpOperationPanel } from './McpOperationPanel';
import styles from './AccountBindingsPage.module.css';

/**
 * 设置 › 账号绑定：自己在各个业务系统里的账号，都在这一页连上、断开、核对。
 *
 * 三段：OA 账号；单位提供的业务服务连接；在业务平台上没走完的操作（核对后继续或取消）。
 * 管理员看全单位绑定情况的那一页仍在「管理」里，不在这里。
 */
export default function AccountBindingsPage() {
  return (
    <PageFrame title="账号绑定">
      <div className={styles.page}>
        <section aria-label="OA 办公系统" className={styles.block}>
          <h2 className={styles.blockTitle}>OA 办公系统</h2>
          <OACredentialBindingCard />
        </section>
        <McpConnectionsPanel />
        <McpOperationPanel />
      </div>
    </PageFrame>
  );
}
