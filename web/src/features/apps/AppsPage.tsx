import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError } from '../../api/mutator';
import { createSystemEntry, getSystemEntryAvailability, systemEntryErrorMessage } from '../../api/systemEntry';
import type { SystemEntryAvailability, U8CaptchaAnswer } from '../../api/systemEntry';
import { useAuthStore } from '../../stores/authStore';
import { Icon } from '../../shared/ui/Icon';
import { PageFrame } from '../../shared/ui/PageFrame';
import { NewSoftwareDialog } from './NewSoftwareDialog';
import { U8EntryCaptcha } from './U8Captcha';
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
  availability: SystemEntryAvailability | undefined;
  availabilityFailed: boolean;
  onClose: () => void;
  onToggle: () => void;
  open: boolean;
  system: BusinessSystem;
}

/**
 * 一张系统卡片：左边点一下进入系统，名称下面一盏灯说明账号绑没绑；右边的齿轮摊开一张小设置卡片，
 * 在里面输账号密码绑定。点卡片外面或按 Esc 收起。
 */
function SystemCard({ index, availability, availabilityFailed, onClose, onToggle, open, system }: SystemCardProps) {
  const card = useRef<HTMLElement>(null);
  const gear = useRef<HTMLButtonElement>(null);
  const href = systemHref(system.key);
  const generation = useAuthStore((state) => state.generation);
  const [busy, setBusy] = useState(false);
  const [captchaOpen, setCaptchaOpen] = useState(false);
  const [captchaRevision, setCaptchaRevision] = useState(0);
  const [entryError, setEntryError] = useState<string | null>(null);
  const target = system.account.kind === 'password' ? system.account.target : null;
  const sso = target !== null && availability?.[target] === true;
  const canEnter = sso || (target === 'oa' && availability !== undefined && href !== null);
  const hint = target === null ? null
    : availabilityFailed ? '读不到免登入口状态，请刷新重试'
    : availability === undefined ? '正在读取入口'
    : sso ? null : target === 'oa' && href !== null
      ? '免登入口未配置，打开原系统' : '免登入口未配置';

  const enter = async (captcha?: U8CaptchaAnswer) => {
    if (!sso) {
      if (href !== null) openSystem(href);
      return;
    }
    if (target === 'u8' && captcha === undefined) {
      setEntryError(null);
      setCaptchaOpen(true);
      return;
    }
    const popup = window.open('about:blank', '_blank');
    if (popup === null) {
      setEntryError('浏览器拦截了新窗口，请允许本站弹窗后重试。');
      return;
    }
    popup.opener = null;
    setBusy(true);
    setEntryError(null);
    const current = () => useAuthStore.getState().generation === generation;
    try {
      const entry = await createSystemEntry(target!, captcha);
      if (!current()) { popup.close(); return; }
      if (!popup.closed) popup.location.replace(entry.url);
      setCaptchaOpen(false);
    } catch (error) {
      popup.close();
      if (!current()) return;
      setEntryError(target === 'u8' ? systemEntryErrorMessage(error) : error instanceof ApiError
        ? error.message : '暂时无法进入系统，请稍后再试。');
    } finally {
      if (current()) {
        setBusy(false);
        if (target === 'u8') setCaptchaRevision((value) => value + 1);
      }
    }
  };

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
        aria-busy={busy}
        disabled={!canEnter || busy}
        onClick={() => { void enter(); }}
        title={canEnter ? `打开${system.name}` : undefined}
        type="button"
      >
        <span aria-hidden="true" className={styles.mono}>{system.mark}</span>
        <span className={styles.cardText}>
          <b>{system.name}</b>
          <span className={styles.cardMeta}>
            <SystemLight system={system} />
            {busy ? <small role="status">正在登录…</small> : null}
            {entryError !== null ? <small role="alert">{entryError}</small>
              : hint !== null ? <small>{hint}</small> : null}
          </span>
        </span>
        {!canEnter ? null : <Icon className={styles.enterMark} name="open" size={18} />}
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
      {captchaOpen ? (
        <U8EntryCaptcha busy={busy} error={entryError} onCancel={() => setCaptchaOpen(false)}
          onSubmit={(answer) => { void enter(answer); }} revision={captchaRevision} />
      ) : null}
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
  const generation = useAuthStore((state) => state.generation);
  const entries = useQuery({
    queryKey: ['system-entry-availability', generation],
    queryFn: ({ signal }) => getSystemEntryAvailability(signal),
    retry: false,
  });

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
              availability={entries.data}
              availabilityFailed={entries.isError}
              index={index}
              key={`${generation}/${system.key}`}
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
