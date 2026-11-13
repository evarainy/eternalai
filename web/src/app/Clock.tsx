import { memo, useEffect, useState } from 'react';
import { useAppearanceStore } from '../stores/appearanceStore';
import styles from './Clock.module.css';

const WEEKDAYS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'] as const;

const pad = (value: number) => (value < 10 ? `0${value}` : String(value));

/** 表盘刻度与 1–12 的数字：只画一次，不随每秒重渲染。 */
const DialFace = memo(function DialFace() {
  const minuteTicks = [];
  for (let i = 0; i < 60; i += 1) {
    if (i % 5 !== 0) {
      minuteTicks.push(
        <line className={styles.tick} key={i} transform={`rotate(${i * 6} 50 50)`} x1="50" x2="50" y1="3.6" y2="5.4" />,
      );
    }
  }
  const hours = [];
  for (let n = 1; n <= 12; n += 1) {
    const angle = (n * 30 * Math.PI) / 180;
    hours.push(
      <line
        className={`${styles.tick} ${styles.tickBold}`}
        key={`t${n}`}
        transform={`rotate(${n * 30} 50 50)`}
        x1="50"
        x2="50"
        y1="3.2"
        y2="7.6"
      />,
      <text
        className={n % 3 === 0 ? styles.numeralBold : undefined}
        key={`n${n}`}
        x={(50 + 35 * Math.sin(angle)).toFixed(1)}
        y={(50.5 - 35 * Math.cos(angle)).toFixed(1)}
      >
        {n}
      </text>,
    );
  }
  return (
    <>
      <circle cx="50" cy="50" r="49" />
      {minuteTicks}
      {hours}
    </>
  );
});

function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    let timer: number;
    const schedule = () => {
      // 对齐到下一个整秒，避免长时间运行后秒针与系统时间错开。
      timer = window.setTimeout(() => {
        setNow(new Date());
        schedule();
      }, 1000 - (Date.now() % 1000));
    };
    schedule();
    return () => window.clearTimeout(timer);
  }, []);
  return now;
}

/**
 * 页面顶部正中的时钟（2026-11-13 雨爷决定：除消息页外每页都有，日期和星期在时间下面）。
 * 两种样式：数字（默认）和表盘；点时钟本身或在设置里切换。只有这个组件每秒重渲染一次。
 */
export function Clock({ hideWhenNarrow = false }: { hideWhenNarrow?: boolean }) {
  const clock = useAppearanceStore((state) => state.clock);
  const toggleClock = useAppearanceStore((state) => state.toggleClock);
  const now = useNow();
  const hours = now.getHours();
  const minutes = now.getMinutes();
  const seconds = now.getSeconds();
  const day = `${now.getMonth() + 1} 月 ${now.getDate()} 日`;
  const weekday = WEEKDAYS[now.getDay()];
  const time = `${pad(hours)}:${pad(minutes)}`;

  return (
    <button
      aria-label={`现在 ${time}，${day} ${weekday}。点一下切换数字或表盘`}
      className={styles.clock}
      data-narrow={hideWhenNarrow ? 'hide' : 'keep'}
      data-style={clock}
      data-testid="shell-clock"
      onClick={toggleClock}
      title="点一下切换数字 / 表盘"
      type="button"
    >
      {clock === 'digit' ? (
        <span aria-hidden="true" className={styles.digits}>
          <b>
            {pad(hours)}
            <span className={styles.colon}>:</span>
            {pad(minutes)}
          </b>
          <i>{pad(seconds)}</i>
        </span>
      ) : (
        <svg aria-hidden="true" className={styles.dial} viewBox="0 0 100 100">
          <DialFace />
          <line
            className={styles.hourHand}
            transform={`rotate(${(hours % 12) * 30 + minutes / 2} 50 50)`}
            x1="50"
            x2="50"
            y1="54"
            y2="29"
          />
          <line
            className={styles.minuteHand}
            transform={`rotate(${minutes * 6 + seconds / 10} 50 50)`}
            x1="50"
            x2="50"
            y1="56"
            y2="15"
          />
          <line
            className={styles.secondHand}
            transform={`rotate(${seconds * 6} 50 50)`}
            x1="50"
            x2="50"
            y1="61"
            y2="11"
          />
          <circle className={styles.hub} cx="50" cy="50" r="3" />
        </svg>
      )}
      <span aria-hidden="true" className={styles.date}>
        {day}
        <span className={styles.weekday}>{weekday}</span>
      </span>
    </button>
  );
}
