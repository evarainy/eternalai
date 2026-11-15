import type { CSSProperties } from 'react';
import { PageFrame } from '../../shared/ui/PageFrame';
import { McpOperationPanel } from './McpOperationPanel';
import { SystemAccountSettings } from './SystemAccountSettings';
import { SystemLight } from './SystemStatus';
import { SYSTEMS } from './systems';
import styles from './Systems.module.css';

/**
 * 设置 › 账号绑定：自己在各个业务系统里的账号，都在这一页连上、断开、核对。
 *
 * 一个系统一段：左边是系统和它的指示灯，右边直接摊开账号设置（和软件中心齿轮里的是同一份）。
 * 最下面一段是在业务平台上没走完的操作，核对后继续或取消。
 * 管理员看全单位绑定情况的那一页仍在「管理」里，不在这里。
 */
export default function AccountBindingsPage() {
  return (
    <PageFrame title="账号绑定">
      <div className={styles.rows}>
        {SYSTEMS.map((system, index) => (
          <section
            aria-label={system.name}
            className={styles.row}
            key={system.key}
            style={{ '--c': system.color, '--i': index } as CSSProperties}
          >
            <div className={styles.rowHead}>
              <span aria-hidden="true" className={styles.mono}>{system.mark}</span>
              <span className={styles.cardText}>
                <b>{system.name}</b>
                <SystemLight system={system} />
              </span>
            </div>
            <SystemAccountSettings system={system} />
          </section>
        ))}
        <div className={styles.wide} style={{ '--i': SYSTEMS.length } as CSSProperties}>
          <McpOperationPanel />
        </div>
      </div>
    </PageFrame>
  );
}
