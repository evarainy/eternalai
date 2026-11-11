import { useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent, ReactNode } from 'react';
import { LIST_WIDTH, clampListWidth, readImPrefs, writeImPrefs } from '../zulip/imPrefs';
import styles from './ImLayout.module.css';

/**
 * IM 三栏骨架：会话列表（可拖动调宽并记住）+ 聊天区。抽屉由聊天区自己叠在右侧（lane 4）。
 * 输入框高度的拖动柄在 Composer 内。
 */
export function ImLayout({ chat, list }: { chat: ReactNode; list: ReactNode }) {
  const [width, setWidth] = useState(() => readImPrefs().listWidth);
  const [dragging, setDragging] = useState(false);
  const start = useRef<{ x: number; width: number } | null>(null);

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    start.current = { x: event.clientX, width };
    setDragging(true);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!start.current) return;
    setWidth(clampListWidth(start.current.width + event.clientX - start.current.x));
  };
  const finish = () => {
    if (!start.current) return;
    start.current = null;
    setDragging(false);
    writeImPrefs({ listWidth: width });
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.key === 'ArrowLeft' ? -16 : event.key === 'ArrowRight' ? 16 : 0;
    if (step === 0) return;
    event.preventDefault();
    setWidth(writeImPrefs({ listWidth: width + step }).listWidth);
  };

  return (
    <div className={`${styles.im}${dragging ? ` ${styles.dragging}` : ''}`} data-testid="im-root">
      <aside aria-label="会话列表" className={styles.list} style={{ width }}>
        {list}
        <div
          aria-label="调整会话列表宽度"
          aria-orientation="vertical"
          aria-valuemax={LIST_WIDTH.max}
          aria-valuemin={LIST_WIDTH.min}
          aria-valuenow={width}
          className={styles.vgrip}
          data-dragging={dragging ? 'true' : 'false'}
          onKeyDown={onKeyDown}
          onLostPointerCapture={finish}
          onPointerCancel={finish}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={finish}
          role="separator"
          tabIndex={0}
        />
      </aside>
      <section aria-label="聊天" className={styles.chat}>{chat}</section>
    </div>
  );
}
