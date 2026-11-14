import { useState } from 'react';
import type { CSSProperties } from 'react';
import { oaWorkbenchNavigation } from '../../contracts/runtimeProjection';
import { Icon } from '../../shared/ui/Icon';
import { PageFrame } from '../../shared/ui/PageFrame';
import { NewSoftwareDialog } from './NewSoftwareDialog';
import styles from './AppsPage.module.css';

/**
 * 软件中心：纸面上一排方印，一个系统一枚，点一下就进去。
 *
 * 每个系统在 `SYSTEMS` 里占一行：单字、名称、颜色。接进来的系统由 `openSystem` 负责打开；
 * 还没接进来的只占位置，点不动。以后接入一个系统，就是在 `openSystem` 里给它加一条打开方式。
 */
interface BusinessSystem {
  key: 'oa' | 'finance' | 'integrated' | 'data' | 'enforcement';
  mark: string;
  name: string;
  color: string;
}

const SYSTEMS: readonly BusinessSystem[] = [
  { key: 'oa', mark: '文', name: 'OA 办公系统', color: '#2450c8' },
  { key: 'finance', mark: '财', name: '财务软件', color: '#b06a12' },
  { key: 'integrated', mark: '综', name: '综合业务平台', color: '#0f7b7b' },
  { key: 'data', mark: '数', name: '数据中台', color: '#3b3fae' },
  { key: 'enforcement', mark: '法', name: '执法办案平台', color: '#8a4b2a' },
];

/** 打开一个业务系统的地址；还没接进来、或地址没配好时是 `null`。 */
function systemHref(key: BusinessSystem['key']): string | null {
  if (key === 'oa') {
    const navigation = oaWorkbenchNavigation();
    return navigation.kind === 'allowed' ? navigation.href : null;
  }
  return null;
}

/** 点方印进入系统。单点登录接入后只改这一处。 */
function openSystem(href: string) {
  window.open(href, '_blank', 'noopener,noreferrer');
}

export default function AppsPage() {
  const [dialogOpen, setDialogOpen] = useState(false);

  return (
    <PageFrame title="软件中心">
      <div className={styles.page}>
        <h2 className={styles.sec}>业务系统</h2>
        <div className={styles.tiles}>
          {SYSTEMS.map((system) => {
            const href = systemHref(system.key);
            return (
              <button
                className={styles.tile}
                disabled={href === null}
                key={system.key}
                onClick={href === null ? undefined : () => openSystem(href)}
                style={{ '--c': system.color } as CSSProperties}
                title={href === null ? undefined : `打开${system.name}`}
                type="button"
              >
                <span aria-hidden="true" className={styles.mono}>{system.mark}</span>
                <b>{system.name}</b>
                {href === null ? (
                  <small>{system.key === 'oa' ? '地址未配置' : '待接入'}</small>
                ) : null}
              </button>
            );
          })}
          <button
            className={`${styles.tile} ${styles.tileNew}`}
            onClick={() => setDialogOpen(true)}
            type="button"
          >
            <span aria-hidden="true" className={styles.mono}>
              <Icon name="plus" size={22} />
            </span>
            <b>添加软件</b>
          </button>
        </div>
      </div>

      <NewSoftwareDialog onClose={() => setDialogOpen(false)} open={dialogOpen} />
    </PageFrame>
  );
}
