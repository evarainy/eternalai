/// <reference types="vite/client" />

import { useRef } from 'react';
import type { CSSProperties, FormEvent, KeyboardEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Button, Input, Tag, Typography, theme } from 'antd';
import { ConfirmCard } from '../components/ConfirmCard';
import { RecordsList } from '../components/RecordsList';
import {
  projectResponse,
  type PresentationKind,
  type ProjectedResponse,
} from '../contracts/runtimeProjection';
import { projectRequestError } from '../contracts/runtimeRequestError';
import { userActionOutcomeMessages } from '../contracts/userActionOutcome';
import {
  handleActionApiV1RuntimeActionPost,
  handleApiV1RuntimeHandlePost,
} from '../generated/runtime/runtime';
import type { UIComponentTargetSystem } from '../generated/runtime/runtime.schemas';
import { useAIDockStore } from '../stores/aiDockStore';
import styles from './ChatPage.module.css';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

interface ChatCssVariables extends CSSProperties {
  '--chat-color-bg': string;
  '--chat-color-bg-soft': string;
  '--chat-color-border': string;
  '--chat-color-primary': string;
  '--chat-color-primary-soft': string;
  '--chat-color-on-primary': string;
  '--chat-color-success': string;
  '--chat-color-warning': string;
  '--chat-color-error': string;
  '--chat-color-neutral': string;
  '--chat-color-text': string;
  '--chat-color-text-secondary': string;
  '--chat-shadow': string;
}

const presentationLabels: Record<PresentationKind, string> = {
  completed: '办理完成',
  clarification: '需要补充范围',
  confirmation: '需要确认',
  binding: '需要账号绑定',
  denied: '请求被拒绝',
  unavailable: '暂不可办理',
  failed: '办理失败',
  incompatible: '响应不可用',
  csrf: '安全校验失败',
  session: '会话不可用',
  validation: '请求需调整',
  service: '服务不可用',
  network: '网络异常',
  request_error: '请求失败',
};

const targetSystemLabels: Record<
  Exclude<UIComponentTargetSystem, null>,
  string
> = {
  oa: 'OA',
  u8: 'U8',
  hikvision_ivms: '海康 iVMS',
};

function AssistantDetails({
  entry,
  onConfirm,
}: {
  entry: ProjectedResponse;
  onConfirm: (responseId: string) => Promise<void>;
}) {
  if (entry.presentationKind === 'clarification') {
    return (
      <Text className={styles.guidance}>
        请将原请求与明确范围一起完整重述为一条新请求。
      </Text>
    );
  }
  if (entry.presentationKind === 'binding' && entry.targetSystem) {
    return (
      <Text className={styles.guidance}>
        目标系统：{targetSystemLabels[entry.targetSystem]}
      </Text>
    );
  }
  return (
    <>
      {entry.actionOutcome === null ? null : (
        <Text strong className={styles.outcomeNotice}>
          {userActionOutcomeMessages[entry.actionOutcome]}
        </Text>
      )}
      {entry.confirm === null ? null : (
        <ConfirmCard
          confirm={entry.confirm}
          responseId={entry.responseId}
          onConfirm={onConfirm}
        />
      )}
      {entry.records === null ? null : <RecordsList records={entry.records} />}
    </>
  );
}

export default function ChatPage() {
  const { token } = theme.useToken();
  const draft = useAIDockStore((state) => state.draft);
  const transcript = useAIDockStore((state) => state.transcript);
  const appendTranscript = useAIDockStore((state) => state.appendTranscript);
  const setDraft = useAIDockStore((state) => state.setDraft);
  const requestInFlight = useRef(false);

  const mutation = useMutation({
    mutationFn: async (message: string) => {
      try {
        const sessionId = useAIDockStore.getState().ensureSession();
        return projectResponse(await handleApiV1RuntimeHandlePost({
          channel: 'web',
          session_id: sessionId,
          message,
          client_capabilities: {},
        }));
      } catch (error) {
        const projectedError = projectRequestError(error);
        if (projectedError === null) {
          throw error;
        }
        return projectedError;
      }
    },
    onSuccess: (projectedResponse) => {
      appendTranscript(projectedResponse);
    },
    onSettled: () => {
      requestInFlight.current = false;
    },
  });

  const submit = () => {
    const message = draft.trim();
    if (!message || requestInFlight.current) {
      return;
    }
    requestInFlight.current = true;
    appendTranscript({ role: 'user', text: message });
    setDraft('');
    mutation.mutate(message);
  };

  const submitConfirmation = async (responseId: string) => {
    try {
      const sessionId = useAIDockStore.getState().ensureSession();
      const projectedResponse = projectResponse(
        await handleActionApiV1RuntimeActionPost({
          channel: 'web',
          session_id: sessionId,
          action: {
            action_type: 'confirm',
            response_id: responseId,
            confirmed: true,
          },
        }),
      );
      appendTranscript(projectedResponse);
    } catch (error) {
      const projectedError = projectRequestError(error);
      if (projectedError !== null) {
        appendTranscript(projectedError);
      }
    }
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

  const cssVariables: ChatCssVariables = {
    '--chat-color-bg': token.colorBgContainer,
    '--chat-color-bg-soft': token.colorFillQuaternary,
    '--chat-color-border': token.colorBorderSecondary,
    '--chat-color-primary': token.colorPrimary,
    '--chat-color-primary-soft': token.colorPrimaryBg,
    '--chat-color-on-primary': token.colorTextLightSolid,
    '--chat-color-success': token.colorSuccess,
    '--chat-color-warning': token.colorWarning,
    '--chat-color-error': token.colorError,
    '--chat-color-neutral': token.colorTextQuaternary,
    '--chat-color-text': token.colorText,
    '--chat-color-text-secondary': token.colorTextSecondary,
    '--chat-shadow': token.boxShadowTertiary,
  };

  return (
    <div className={styles.page} style={cssVariables}>
      <header className={styles.pageHeader}>
        <div>
          <Text className={styles.eyebrow}>开始新工作</Text>
          <Title level={1} className={styles.title}>
            把要办的事说清楚
          </Title>
          <Paragraph className={styles.subtitle}>
            写清对象、时间和想得到的结果。系统会告诉你已经完成、还需补什么，或为什么暂时不能办理。
          </Paragraph>
        </div>
        <Tag variant="filled" className={styles.sessionTag}>
          <span aria-hidden="true">●</span> 本次临时对话
        </Tag>
      </header>

      <section className={styles.conversation} aria-label="办理会话">
        <div
          className={styles.transcript}
          aria-live="polite"
          aria-busy={mutation.isPending}
        >
          {transcript.length === 0 ? (
            <div className={styles.emptyState}>
              <span className={styles.emptyMark} aria-hidden="true">
                E
              </span>
              <Title level={3}>从一条明确请求开始</Title>
              <Paragraph>
                例如：查询我的 OA 待办。涉及范围时，请把系统、账套或设备域写进同一条请求。
              </Paragraph>
            </div>
          ) : (
            <ol className={styles.messageList}>
              {transcript.map((entry, index) => (
                <li
                  className={`${styles.messageRow} ${
                    entry.role === 'user' ? styles.userRow : styles.assistantRow
                  }`}
                  key={`${entry.role}-${index}`}
                >
                  <article
                    className={`${styles.message} ${
                      entry.role === 'user'
                        ? styles.userMessage
                        : styles[entry.presentationKind]
                    }`}
                  >
                    <div className={styles.messageMeta}>
                      <Text strong>{entry.role === 'user' ? '你' : 'EternalAI'}</Text>
                      {entry.role === 'assistant' ? (
                        <Text className={styles.statusLabel}>
                          {presentationLabels[entry.presentationKind]}
                        </Text>
                      ) : null}
                    </div>
                    <p className={styles.messageText}>{entry.text}</p>
                    {entry.role === 'assistant' ? (
                      <AssistantDetails
                        entry={entry}
                        onConfirm={submitConfirmation}
                      />
                    ) : null}
                  </article>
                </li>
              ))}
            </ol>
          )}

          {mutation.isPending ? (
            <div className={styles.pendingNotice} role="status">
              <span className={styles.pendingDot} aria-hidden="true" />
              正在办理，请稍候…
            </div>
          ) : null}
        </div>

        <form className={styles.composer} onSubmit={handleSubmit}>
          <div className={styles.composerHeading}>
            <label className={styles.composerLabel} htmlFor="chat-request">
              办理请求
            </label>
            <Text className={styles.composerHint} id="chat-request-hint">
              Enter 发送 · Shift + Enter 换行
            </Text>
          </div>
          <TextArea
            id="chat-request"
            aria-describedby="chat-request-hint"
            aria-label="办理请求"
            autoSize={{ minRows: 2, maxRows: 6 }}
            disabled={mutation.isPending}
            placeholder="请完整描述要办理或查询的事项…"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
          />
          <div className={styles.composerActions}>
            <Text className={styles.safetyHint}>
              页面只展示安全文本，不展示内部追踪信息。
            </Text>
            <Button
              type="primary"
              htmlType="submit"
              loading={mutation.isPending}
              disabled={!draft.trim() || mutation.isPending}
            >
              发送办理请求
            </Button>
          </div>
        </form>
      </section>
    </div>
  );
}
