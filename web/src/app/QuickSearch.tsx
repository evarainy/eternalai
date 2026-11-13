import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Icon } from '../shared/ui/Icon';
import styles from './QuickSearch.module.css';

/**
 * Ctrl K 搜索面板：浮在页面上方的一块毛玻璃，输入后回车去工作事项搜索结果页；
 * 也可以把这句话直接交给 AI（助手页自己就是对话，不显示这一项）。
 * 关着的时候不在 DOM 里，不参与绘制。
 */
export function QuickSearch({
  canAskAssistant,
  onAskAssistant,
  onClose,
  onSubmit,
  open,
}: {
  canAskAssistant: boolean;
  onAskAssistant: (value: string) => void;
  onClose: () => void;
  onSubmit: (value: string) => void;
  open: boolean;
}) {
  const [value, setValue] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    setValue('');
    inputRef.current?.focus();
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', closeOnEscape, true);
    return () => document.removeEventListener('keydown', closeOnEscape, true);
  }, [onClose, open]);

  if (!open) return null;

  const term = value.trim();
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSubmit(value);
  };

  return (
    <>
      <div aria-hidden="true" className={styles.scrim} onClick={onClose} />
      <div
        aria-label="快速搜索"
        aria-modal="true"
        className={`${styles.palette} wb-glass`}
        data-testid="quick-search"
        role="dialog"
      >
        <form className={styles.inputRow} data-focus-ring="host" onSubmit={handleSubmit} role="search">
          <Icon name="search" size={19} />
          <input
            aria-label="搜索工作事项"
            onChange={(event) => setValue(event.target.value)}
            placeholder="搜索工作事项、文件编号、责任人"
            ref={inputRef}
            value={value}
          />
          <kbd className="wb-kbd">Esc</kbd>
        </form>
        <div className={styles.group}>工作事项</div>
        <button className={styles.item} data-primary="true" onClick={() => onSubmit(value)} type="button">
          <Icon name="todo" size={18} />
          <span>{term.length === 0 ? '打开搜索页' : `搜索「${term}」`}</span>
          <em>Enter</em>
        </button>
        {canAskAssistant && term.length > 0 ? (
          <>
            <div className={styles.group}>交给 AI</div>
            <button className={styles.item} onClick={() => onAskAssistant(term)} type="button">
              <Icon name="sparkle" size={18} />
              <span>{`问 AI：「${term}」`}</span>
            </button>
          </>
        ) : null}
      </div>
    </>
  );
}
