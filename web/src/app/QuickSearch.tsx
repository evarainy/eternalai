import { useEffect, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import { Icon } from '../shared/ui/Icon';
import styles from './QuickSearch.module.css';

interface QuickSearchProps {
  canAskAssistant: boolean;
  onAskAssistant: (value: string) => void;
  onClose: () => void;
  onSubmit: (value: string) => void;
  open: boolean;
}

/**
 * Ctrl K 搜索面板：浮在页面上方的一块毛玻璃，输入后回车去工作事项搜索结果页；
 * 也可以把这句话直接交给 AI（助手页自己就是对话，不显示这一项）。
 * 关着的时候不在 DOM 里，不参与绘制。
 */
export function QuickSearch(props: QuickSearchProps) {
  // 面板只在打开时挂载：每次打开都是空的输入框，关闭时把焦点还给打开前的元素。
  return props.open ? <QuickSearchPanel {...props} /> : null;
}

function QuickSearchPanel({ canAskAssistant, onAskAssistant, onClose, onSubmit }: QuickSearchProps) {
  const [value, setValue] = useState('');
  const panelRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const opener = document.activeElement;
    inputRef.current?.focus();
    // 面板开着的时候 Esc 一律关面板：焦点被约束在面板里，不会截走别处的 Esc。
    const handleEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') closeRef.current();
    };
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('keydown', handleEscape);
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);

  /* 这是一个模态面板：焦点留在面板里，Tab 在面板内循环。 */
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Tab') return;
    const focusable = panelRef.current?.querySelectorAll<HTMLElement>('input, button');
    if (focusable === undefined || focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  };

  const term = value.trim();
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSubmit(value);
  };

  return (
    <>
      <div
        aria-hidden="true"
        className={styles.scrim}
        onMouseDown={(event) => {
          // 点遮罩关闭；阻止默认行为，焦点不落到遮罩后面的页面上。
          event.preventDefault();
          onClose();
        }}
      />
      <div
        aria-label="快速搜索"
        aria-modal="true"
        className={`${styles.palette} wb-glass`}
        data-testid="quick-search"
        onKeyDown={handleKeyDown}
        ref={panelRef}
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
