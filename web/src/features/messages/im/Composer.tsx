import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';
import { ApiError } from '../../../api/mutator';
import { Icon } from '../../../shared/ui/Icon';
import type { IconName } from '../../../shared/ui/Icon';
import { COMPOSER_HEIGHT, clampComposerHeight, readImPrefs, writeImPrefs } from '../zulip/imPrefs';
import { sendMessage } from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';
import styles from './Composer.module.css';

/** 未发出的文字只在本页内存里按会话保留；服务端草稿（/drafts）在 lane 2 接入。 */
const unsent = new Map<ConversationKey, string>();

const TOOLS: { icon: IconName; label: string }[] = [
  { icon: 'smile', label: '表情' }, { icon: 'atsign', label: '提及成员' }, { icon: 'image', label: '图片' },
  { icon: 'folder', label: '文件' }, { icon: 'type', label: '格式' },
];

function sendFailure(error: unknown): string {
  if (error instanceof ApiError && error.code === 'im_mapping_required') return '你的消息账号还没有绑定，暂时不能发送。';
  return '发送结果未确认，请查看消息记录后再决定是否重发。';
}

/**
 * 输入框：Enter 发送、Shift+Enter 换行（按用户设置 `enter_sends`），输入法组字时按 Enter 不发送。
 * 顶边可拖动调高并记住高度。表情、@、附件、格式工具在 lane 2 接入。
 */
export function Composer({ conversationKey, enterSends, placeholder }: {
  conversationKey: ConversationKey;
  enterSends: boolean;
  placeholder: string;
}) {
  const [text, setText] = useState(() => unsent.get(conversationKey) ?? '');
  const [height, setHeight] = useState(() => readImPrefs().composerHeight);
  const [dragging, setDragging] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const start = useRef<{ y: number; height: number } | null>(null);

  // 由 ChatPane 按会话 key 重新挂载，切换会话时读回该会话未发出的文字。
  useEffect(() => {
    input.current?.focus();
  }, []);

  const update = (value: string) => {
    setText(value);
    if (value) unsent.set(conversationKey, value);
    else unsent.delete(conversationKey);
  };

  const submit = async () => {
    const content = text.trim();
    if (!content || sending) return;
    setSending(true);
    setError(null);
    try {
      await sendMessage(conversationKey, content);
      if (unsent.get(conversationKey) === text) unsent.delete(conversationKey);
      setText((current) => (current === text ? '' : current));
    } catch (failure) {
      setError(sendFailure(failure));
    } finally {
      setSending(false);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.nativeEvent.isComposing || event.keyCode === 229) return;
    const wantsSend = enterSends ? !event.shiftKey && !event.ctrlKey : event.ctrlKey || event.metaKey;
    if (!wantsSend) return;
    event.preventDefault();
    void submit();
  };

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    start.current = { y: event.clientY, height };
    setDragging(true);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (start.current) setHeight(clampComposerHeight(start.current.height + start.current.y - event.clientY));
  };
  const finish = () => {
    if (!start.current) return;
    start.current = null;
    setDragging(false);
    writeImPrefs({ composerHeight: height });
  };
  const onGripKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.key === 'ArrowUp' ? 16 : event.key === 'ArrowDown' ? -16 : 0;
    if (step === 0) return;
    event.preventDefault();
    setHeight(writeImPrefs({ composerHeight: height + step }).composerHeight);
  };

  return (
    <div className={styles.composer} data-testid="im-composer">
      <div
        aria-label="调整输入框高度"
        aria-orientation="horizontal"
        aria-valuemax={COMPOSER_HEIGHT.max}
        aria-valuemin={COMPOSER_HEIGHT.min}
        aria-valuenow={height}
        className={styles.hgrip}
        data-dragging={dragging ? 'true' : 'false'}
        onKeyDown={onGripKey}
        onLostPointerCapture={finish}
        onPointerCancel={finish}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={finish}
        role="separator"
        tabIndex={0}
      />
      <div className={styles.tools}>
        {TOOLS.map((tool) => (
          <button aria-label={tool.label} className={styles.tool} disabled key={tool.icon} title={`${tool.label}（后续接入）`} type="button">
            <Icon name={tool.icon} size={19} />
          </button>
        ))}
        <button aria-label="聊天记录" className={`${styles.tool} ${styles.toolEnd}`} disabled title="聊天记录（后续接入）" type="button">
          <Icon name="history" size={19} />
        </button>
      </div>
      <textarea
        aria-label="输入消息"
        className={styles.input}
        onChange={(event) => update(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        ref={input}
        style={{ height }}
        value={text}
      />
      <div className={styles.foot}>
        {error ? <span className={styles.error} role="alert">{error}</span> : null}
        <span className={styles.hint}>{enterSends ? 'Enter 发送 · Shift + Enter 换行' : 'Ctrl + Enter 发送 · Enter 换行'}</span>
        <button className={styles.send} disabled={sending || text.trim() === ''} onClick={() => void submit()} type="button">
          {sending ? '发送中' : '发送'}
        </button>
      </div>
    </div>
  );
}
