import { useEffect, useRef, useState } from 'react';
import type {
  CSSProperties,
  FormEvent,
  KeyboardEvent,
  MouseEvent as ReactMouseEvent,
} from 'react';
import { useMutation } from '@tanstack/react-query';
import { Button, Input } from 'antd';
import type { PageContextDeclaration } from '../contracts/pageContext';
import { projectResponse } from '../contracts/runtimeProjection';
import { projectRequestError } from '../contracts/runtimeRequestError';
import { handleApiV1RuntimeHandlePost } from '../generated/runtime/runtime';
import { Icon } from '../shared/ui/Icon';
import { useAIDockStore } from '../stores/aiDockStore';
import styles from './AIDock.module.css';

const { TextArea } = Input;

/** 键盘移动浮动面板的步长；提供给不能使用鼠标拖动的用户。 */
export const DOCK_KEYBOARD_STEP = 24;

interface AIDockProps {
  suppressed?: boolean;
}

interface DockPosition {
  left: number;
  top: number;
}

interface DragOrigin extends DockPosition {
  height: number;
  pointerLeft: number;
  pointerTop: number;
  width: number;
}

const KEYBOARD_MOVES: Record<string, readonly [number, number] | undefined> = {
  ArrowDown: [0, DOCK_KEYBOARD_STEP],
  ArrowLeft: [-DOCK_KEYBOARD_STEP, 0],
  ArrowRight: [DOCK_KEYBOARD_STEP, 0],
  ArrowUp: [0, -DOCK_KEYBOARD_STEP],
};

const surfaceLabels: Record<string, string> = {
  'work-object-search': '工作事项搜索结果',
  'work-objects': '工作事项',
};

function pageContextLabel(context: PageContextDeclaration | null): string {
  if (context === null) {
    return '未绑定页面上下文';
  }
  return surfaceLabels[context.surface_id] ?? context.surface_id;
}

function clampToViewport(
  left: number,
  top: number,
  width: number,
  height: number,
): DockPosition {
  const maxLeft = Math.max(0, window.innerWidth - width);
  const maxTop = Math.max(0, window.innerHeight - height);
  return {
    left: Math.min(Math.max(left, 0), maxLeft),
    top: Math.min(Math.max(top, 0), maxTop),
  };
}

export function AIDock({ suppressed = false }: AIDockProps) {
  const contextNotice = useAIDockStore((state) => state.contextNotice);
  const draft = useAIDockStore((state) => state.draft);
  const mode = useAIDockStore((state) => state.mode);
  const pageContextDeclaration = useAIDockStore(
    (state) => state.pageContextDeclaration,
  );
  const transcript = useAIDockStore((state) => state.transcript);
  const appendTranscript = useAIDockStore((state) => state.appendTranscript);
  const closeDock = useAIDockStore((state) => state.closeDock);
  const dismissContextNotice = useAIDockStore(
    (state) => state.dismissContextNotice,
  );
  const setDraft = useAIDockStore((state) => state.setDraft);
  const setMode = useAIDockStore((state) => state.setMode);
  const startNewSession = useAIDockStore((state) => state.startNewSession);
  const requestInFlight = useRef(false);
  const dockRef = useRef<HTMLElement | null>(null);
  const dragOrigin = useRef<DragOrigin | null>(null);
  const [position, setPosition] = useState<DockPosition | null>(null);
  const [dragging, setDragging] = useState(false);

  const mutation = useMutation({
    mutationFn: async (message: string) => {
      try {
        const sessionId = useAIDockStore.getState().ensureSession();
        return projectResponse(
          await handleApiV1RuntimeHandlePost({
            channel: 'web',
            session_id: sessionId,
            message,
            client_capabilities: {},
          }),
        );
      } catch (error) {
        const projectedError = projectRequestError(error);
        if (projectedError === null) {
          throw error;
        }
        return projectedError;
      }
    },
    onSuccess: (response) => appendTranscript(response),
    onSettled: () => {
      requestInFlight.current = false;
    },
  });

  useEffect(() => {
    if (!dragging) {
      return;
    }
    const move = (event: MouseEvent) => {
      const origin = dragOrigin.current;
      if (origin === null) {
        return;
      }
      setPosition(
        clampToViewport(
          origin.left + event.clientX - origin.pointerLeft,
          origin.top + event.clientY - origin.pointerTop,
          origin.width,
          origin.height,
        ),
      );
    };
    const stop = () => {
      dragOrigin.current = null;
      setDragging(false);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', stop);
    return () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', stop);
    };
  }, [dragging]);

  const submit = () => {
    const message = draft.trim();
    if (message.length === 0 || requestInFlight.current) {
      return;
    }
    requestInFlight.current = true;
    appendTranscript({ role: 'user', text: message });
    setDraft('');
    mutation.mutate(message);
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submit();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === 'Enter' &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submit();
    }
  };

  const currentRect = () => dockRef.current?.getBoundingClientRect() ?? null;

  const startDrag = (event: ReactMouseEvent<HTMLButtonElement>) => {
    const rect = currentRect();
    if (rect === null) {
      return;
    }
    event.preventDefault();
    dragOrigin.current = {
      height: rect.height,
      left: position?.left ?? rect.left,
      pointerLeft: event.clientX,
      pointerTop: event.clientY,
      top: position?.top ?? rect.top,
      width: rect.width,
    };
    setDragging(true);
  };

  const moveBy = (horizontal: number, vertical: number) => {
    const rect = currentRect();
    setPosition((current) =>
      clampToViewport(
        (current?.left ?? rect?.left ?? 0) + horizontal,
        (current?.top ?? rect?.top ?? 0) + vertical,
        rect?.width ?? 0,
        rect?.height ?? 0,
      ),
    );
  };

  const handleHandleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const move = KEYBOARD_MOVES[event.key];
    if (move === undefined) {
      return;
    }
    event.preventDefault();
    moveBy(move[0], move[1]);
  };

  const hidden = suppressed || mode === 'closed';
  const contextLabel = pageContextLabel(pageContextDeclaration);
  const dockClassName = `${styles.dock} ${
    mode === 'pinned' ? styles.pinned : styles.drawer
  }`;
  const positionStyle: CSSProperties | undefined =
    position === null
      ? undefined
      : {
          bottom: 'auto',
          left: `${position.left}px`,
          right: 'auto',
          top: `${position.top}px`,
        };

  return (
    <aside
      aria-label="AI 助手"
      className={hidden ? undefined : dockClassName}
      data-floating="true"
      data-mode={mode}
      data-positioned={position === null ? 'false' : 'true'}
      data-testid="ai-dock"
      hidden={hidden}
      ref={dockRef}
      style={positionStyle}
    >
      <div className={styles.inner}>
        {/*
          画板 `FloatingAI.dc.html` 的 `.fhead`：58px 一行，左边图标 + 标题，右边一排 36px 纯图标按钮。
          原来那一排带文字的 antd 按钮（复位 / 放大 / 关闭 AI 助手 / 新建通用会话）与两段说明一起被
          雨爷 2026-09-04 判为「说明文字太多、界面过大」，这里按画板收成图标。功能一个不少。
        */}
        <header className={styles.header}>
          <span className={styles.headerIcon}>
            <Icon name="chat" size={18} strokeWidth={1.9} />
          </span>
          <h2 className={styles.title}>AI 助手</h2>
          <div className={styles.windowControls}>
            <button
              aria-label="新建通用会话"
              className={styles.windowButton}
              onClick={startNewSession}
              title="新建通用会话"
              type="button"
            >
              <Icon name="plus" size={18} strokeWidth={1.9} />
            </button>
            <button
              aria-label="移动 AI 助手：可用鼠标拖动，也可先聚焦本按钮再按方向键移动"
              className={styles.windowButton}
              onKeyDown={handleHandleKeyDown}
              onMouseDown={startDrag}
              title="拖动或按方向键移动"
              type="button"
            >
              <Icon name="maximize" size={18} strokeWidth={1.9} />
            </button>
            {position === null ? null : (
              <button
                aria-label="复位"
                className={styles.windowButton}
                onClick={() => setPosition(null)}
                title="复位"
                type="button"
              >
                <Icon name="expandnav" size={18} strokeWidth={1.9} />
              </button>
            )}
            <button
              aria-label={mode === 'pinned' ? '还原大小' : '放大'}
              className={styles.windowButton}
              onClick={() => setMode(mode === 'pinned' ? 'drawer' : 'pinned')}
              title={mode === 'pinned' ? '还原大小' : '放大'}
              type="button"
            >
              <Icon name={mode === 'pinned' ? 'minus' : 'grid'} size={18} strokeWidth={1.9} />
            </button>
            <button
              aria-label="关闭 AI 助手"
              className={styles.windowButton}
              onClick={closeDock}
              title="关闭 AI 助手"
              type="button"
            >
              <Icon name="close" size={18} strokeWidth={1.9} />
            </button>
          </div>
        </header>

        <div
          aria-busy={mutation.isPending}
          aria-live="polite"
          className={styles.transcript}
        >
          {/* 一行说明当前绑着哪个页面的上下文。这是真实状态，不是提示语，所以留；但只占一行 14px。 */}
          <p className={styles.context} role="status">
            正在协助：{contextLabel}
          </p>
          {contextNotice === null ? null : (
            <div className={styles.notice} role="status">
              <span>{contextNotice}</span>
              <button
                className={styles.noticeButton}
                onClick={dismissContextNotice}
                type="button"
              >
                知道了
              </button>
            </div>
          )}
          {transcript.length === 0 ? (
            <p className={styles.emptyState}>还没有对话。</p>
          ) : (
            <ol className={styles.messageList}>
              {transcript.map((entry, index) => (
                <li
                  className={`${styles.message} ${
                    entry.role === 'user' ? styles.userMessage : styles.assistantMessage
                  }`}
                  key={`${entry.role}-${index}`}
                >
                  {/* 说话人靠气泡的左右对齐区分（画板做法）；读屏软件另有这一段文字。 */}
                  <span className={styles.speaker}>
                    {entry.role === 'user' ? '你' : 'AI 回复'}
                  </span>
                  <p className={styles.messageText}>{entry.text}</p>
                </li>
              ))}
            </ol>
          )}
        </div>

        <form className={styles.composer} onSubmit={handleSubmit}>
          <div className={styles.sender}>
            <TextArea
              aria-label="要 AI 帮什么"
              autoSize={{ maxRows: 5, minRows: 1 }}
              id="ai-dock-request"
              placeholder="接着问……"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleKeyDown}
            />
            <div className={styles.senderBar}>
              <span className={styles.hint}>可拖动 · 可收起</span>
              <Button
                className={styles.sendButton}
                disabled={draft.trim().length === 0 || mutation.isPending}
                htmlType="submit"
                loading={mutation.isPending}
                type="primary"
              >
                发送
              </Button>
            </div>
          </div>
        </form>
      </div>
    </aside>
  );
}
