import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { Icon } from '../../shared/ui/Icon';
import { PageFrame } from '../../shared/ui/PageFrame';
import { NewSoftwareDialog } from './NewSoftwareDialog';
import { SystemAccountSettings } from './SystemAccountSettings';
import { SystemLight } from './SystemStatus';
import { SYSTEMS, openSystem, systemHref } from './systems';
import type { BusinessSystem } from './systems';
import styles from './Systems.module.css';

const LEGEND = [
  ['on', '已绑定'], ['warn', '等待中'], ['alert', '要处理'], ['off', '未绑定'], ['none', '待接入'],
] as const;

interface SystemCardProps {
  index: number;
  onClose: () => void;
  onToggle: () => void;
  open: boolean;
  system: BusinessSystem;
}

/**
 * 一张系统卡片：左边点一下进入系统，名称下面一盏灯说明账号绑没绑；右边的齿轮摊开一张小设置卡片，
 * 在里面输账号密码绑定。点卡片外面或按 Esc 收起。
 */
function SystemCard({ index, onClose, onToggle, open, system }: SystemCardProps) {
  const card = useRef<HTMLElement>(null);
  const gear = useRef<HTMLButtonElement>(null);
  const href = systemHref(system.key);

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (event: MouseEvent) => {
      if (event.target instanceof Node && !card.current?.contains(event.target)) onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      onClose();
      gear.current?.focus();
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose, open]);

  return (
    <article
      className={styles.card}
      data-open={open ? 'true' : 'false'}
      data-pending={system.account.kind === 'none' ? 'true' : 'false'}
      ref={card}
      style={{ '--c': system.color, '--i': index } as CSSProperties}
    >
      <button
        className={styles.enter}
        disabled={href === null}
        onClick={href === null ? undefined : () => openSystem(href)}
        title={href === null ? undefined : `打开${system.name}`}
        type="button"
      >
        <span aria-hidden="true" className={styles.mono}>{system.mark}</span>
        <span className={styles.cardText}>
          <b>{system.name}</b>
          <span className={styles.cardMeta}>
            <SystemLight system={system} />
            {system.key === 'oa' && href === null ? <small>入口地址未配置</small> : null}
          </span>
        </span>
        {href === null ? null : <Icon className={styles.enterMark} name="open" size={18} />}
      </button>
      <button
        aria-expanded={open}
        aria-label={`${system.name}的账号设置`}
        className={styles.gear}
        onClick={onToggle}
        ref={gear}
        title="账号设置"
        type="button"
      >
        <Icon name="gear" size={18} />
      </button>
      {open ? (
        <div aria-label={`${system.name}的账号设置`} className={styles.settings} role="group">
          <h3>
            账号设置
            <SystemLight system={system} />
          </h3>
          <SystemAccountSettings system={system} />
        </div>
      ) : null}
    </article>
  );
}

/**
 * 软件中心：一个业务系统一张卡片。看灯就知道哪个系统绑了账号、哪个要处理；点卡片进入系统，
 * 点齿轮绑定账号。系统清单在 `systems.ts`。
 */
export default function AppsPage() {
  const [openKey, setOpenKey] = useState<BusinessSystem['key'] | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const close = useCallback(() => setOpenKey(null), []);

  return (
    <PageFrame title="软件中心">
      <div className={styles.page}>
        <div className={styles.secRow}>
          <h2 className={styles.sec}>业务系统</h2>
          <ul aria-label="指示灯图例" className={styles.legend}>
            {LEGEND.map(([state, text]) => (
              <li key={state}>
                <span aria-hidden="true" className={styles.light} data-state={state} />
                {text}
              </li>
            ))}
          </ul>
        </div>
        <div className={styles.grid}>
          {SYSTEMS.map((system, index) => (
            <SystemCard
              index={index}
              key={system.key}
              onClose={close}
              onToggle={() => setOpenKey((key) => (key === system.key ? null : system.key))}
              open={openKey === system.key}
              system={system}
            />
          ))}
          <button
            className={styles.addCard}
            onClick={() => setDialogOpen(true)}
            style={{ '--i': SYSTEMS.length } as CSSProperties}
            type="button"
          >
            <span aria-hidden="true" className={styles.addMark}>
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
