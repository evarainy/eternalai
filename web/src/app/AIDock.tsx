import { useRef } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Button, Input } from 'antd';
import { projectResponse } from '../contracts/runtimeProjection';
import { projectRequestError } from '../contracts/runtimeRequestError';
import { handleApiV1RuntimeHandlePost } from '../generated/runtime/runtime';
import { useAIDockStore } from '../stores/aiDockStore';
import styles from './AIDock.module.css';

const { TextArea } = Input;

interface AIDockProps {
  contextLabel: string;
  suppressed?: boolean;
}

export function AIDock({ contextLabel, suppressed = false }: AIDockProps) {
  const draft = useAIDockStore((state) => state.draft);
  const mode = useAIDockStore((state) => state.mode);
  const transcript = useAIDockStore((state) => state.transcript);
  const appendTranscript = useAIDockStore((state) => state.appendTranscript);
  const closeDock = useAIDockStore((state) => state.closeDock);
  const setDraft = useAIDockStore((state) => state.setDraft);
  const startNewSession = useAIDockStore((state) => state.startNewSession);
  const requestInFlight = useRef(false);

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

  const hidden = suppressed || mode === 'closed';
  const dockClassName = `${styles.dock} ${
    mode === 'pinned' ? styles.pinned : styles.drawer
  }`;

  return (
    <aside
      aria-label="AI 助手"
      className={hidden ? undefined : dockClassName}
      data-mode={mode}
      data-testid="ai-dock"
      hidden={hidden}
    >
      <div className={styles.inner}>
        <header className={styles.header}>
          <div className={styles.headerLine}>
            <h2 className={styles.title}>AI 助手</h2>
            <Button onClick={closeDock}>关闭 AI 助手</Button>
          </div>
          <p className={styles.context} role="status">
            <span aria-hidden="true" className={styles.stateIcon}>●</span>
            <span>正在协助：{contextLabel}</span>
          </p>
          <div className={styles.headerActions}>
            <span className={styles.hint}>当前对话只在本次打开应用期间保留。</span>
            <Button onClick={startNewSession}>新建通用对话</Button>
          </div>
        </header>

        <div
          aria-busy={mutation.isPending}
          aria-live="polite"
          className={styles.transcript}
        >
          {transcript.length === 0 ? (
            <div className={styles.emptyState}>
              <strong>还没有对话，因为你尚未向 AI 提问。</strong>
              <p>下一步：在下方写清要处理的事项，再选择“发送”。</p>
            </div>
          ) : (
            <ol className={styles.messageList}>
              {transcript.map((entry, index) => (
                <li
                  className={`${styles.message} ${
                    entry.role === 'user' ? styles.userMessage : ''
                  }`}
                  key={`${entry.role}-${index}`}
                >
                  <div className={styles.messageMeta}>
                    <span aria-hidden="true">{entry.role === 'user' ? '●' : '◆'}</span>
                    <strong>{entry.role === 'user' ? '你' : 'AI 回复'}</strong>
                  </div>
                  <p className={styles.messageText}>{entry.text}</p>
                </li>
              ))}
            </ol>
          )}
        </div>

        <form className={styles.composer} onSubmit={handleSubmit}>
          <div className={styles.composerHeading}>
            <label className={styles.label} htmlFor="ai-dock-request">
              要 AI 帮什么
            </label>
            <span className={styles.hint} id="ai-dock-request-hint">
              写清对象、时间和要得到的结果
            </span>
          </div>
          <TextArea
            aria-describedby="ai-dock-request-hint"
            id="ai-dock-request"
            placeholder="例如：帮我梳理这页事项中今天必须完成的工作"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
          />
          <div className={styles.composerActions}>
            <span className={styles.hint}>Enter 发送，Shift + Enter 换行</span>
            <Button
              disabled={draft.trim().length === 0 || mutation.isPending}
              htmlType="submit"
              loading={mutation.isPending}
              type="primary"
            >
              发送
            </Button>
          </div>
        </form>
      </div>
    </aside>
  );
}
