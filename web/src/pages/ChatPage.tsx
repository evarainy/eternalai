/// <reference types="vite/client" />

import { useRef, useState } from 'react';
import type { CSSProperties, FormEvent, KeyboardEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Button, Input, Tag, Typography, theme } from 'antd';
import { ApiError } from '../api/mutator';
import { handleApiV1RuntimeHandlePost } from '../generated/runtime/runtime';
import type { ResponseEnvelopeStatus } from '../generated/runtime/runtime.schemas';
import styles from './ChatPage.module.css';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

const SUPPORTED_SCHEMA_VERSION = 'phase0.sdui.v1';
const SAFE_INCOMPATIBLE_TEXT = '当前响应无法安全显示，请稍后重试。';

const responseStatuses = new Set<ResponseEnvelopeStatus>([
  'completed',
  'blocked',
  'waiting_user',
  'failed',
  'no_capability_found',
]);
const responseActions = new Set([
  'confirm',
  'bind_required',
  'clarify_scope',
  'none',
  null,
]);
const targetSystems = new Set(['oa', 'u8', 'hikvision_ivms']);

type TargetSystem = 'oa' | 'u8' | 'hikvision_ivms';
type PresentationKind =
  | 'completed'
  | 'clarification'
  | 'confirmation'
  | 'binding'
  | 'denied'
  | 'unavailable'
  | 'failed'
  | 'incompatible'
  | 'csrf'
  | 'session'
  | 'validation'
  | 'service'
  | 'network'
  | 'request_error';

type TranscriptEntry =
  | {
      role: 'user';
      text: string;
    }
  | {
      role: 'assistant';
      text: string;
      status?: ResponseEnvelopeStatus;
      presentationKind: PresentationKind;
      targetSystem?: TargetSystem;
    };

interface ProjectedResponse {
  role: 'assistant';
  text: string;
  status?: ResponseEnvelopeStatus;
  presentationKind: PresentationKind;
  targetSystem?: TargetSystem;
}

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function incompatibleResponse(): ProjectedResponse {
  return {
    role: 'assistant',
    text: SAFE_INCOMPATIBLE_TEXT,
    presentationKind: 'incompatible',
  };
}

function projectResponse(value: unknown): ProjectedResponse {
  if (!isRecord(value) || value.schema_version !== SUPPORTED_SCHEMA_VERSION) {
    return incompatibleResponse();
  }
  if (
    typeof value.status !== 'string' ||
    !responseStatuses.has(value.status as ResponseEnvelopeStatus) ||
    typeof value.message !== 'string' ||
    typeof value.fallback_text !== 'string' ||
    !isRecord(value.ui)
  ) {
    return incompatibleResponse();
  }

  const status = value.status as ResponseEnvelopeStatus;
  const action = value.ui.action;
  const targetSystem = value.ui.target_system;
  if (
    !responseActions.has(action as string | null) ||
    !(
      targetSystem === undefined ||
      targetSystem === null ||
      (typeof targetSystem === 'string' && targetSystems.has(targetSystem))
    )
  ) {
    return incompatibleResponse();
  }

  const text = value.message.trim() || value.fallback_text.trim();
  if (!text) {
    return incompatibleResponse();
  }

  if (status === 'completed' && (action === 'none' || action === null)) {
    return { role: 'assistant', text, status, presentationKind: 'completed' };
  }
  if (status === 'blocked' && action === 'clarify_scope') {
    return { role: 'assistant', text, status, presentationKind: 'clarification' };
  }
  if (status === 'waiting_user' && action === 'confirm') {
    return { role: 'assistant', text, status, presentationKind: 'confirmation' };
  }
  if (status === 'blocked' && action === 'bind_required') {
    return {
      role: 'assistant',
      text,
      status,
      presentationKind: 'binding',
      ...(typeof targetSystem === 'string'
        ? { targetSystem: targetSystem as TargetSystem }
        : {}),
    };
  }
  if (status === 'blocked' && (action === 'none' || action === null)) {
    return { role: 'assistant', text, status, presentationKind: 'denied' };
  }
  if (
    status === 'no_capability_found' &&
    (action === 'none' || action === null)
  ) {
    return { role: 'assistant', text, status, presentationKind: 'unavailable' };
  }
  if (status === 'failed' && (action === 'none' || action === null)) {
    return { role: 'assistant', text, status, presentationKind: 'failed' };
  }
  return incompatibleResponse();
}

function projectRequestError(error: unknown): ProjectedResponse | null {
  if (error instanceof SyntaxError) {
    return incompatibleResponse();
  }
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return null;
    }
    if (error.status === 403 && error.code === 'csrf_validation_failed') {
      return {
        role: 'assistant',
        text: '当前请求来源未通过安全校验，请联系管理员检查部署配置。',
        presentationKind: 'csrf',
      };
    }
    if (error.status === 404) {
      return {
        role: 'assistant',
        text: '当前会话不可用，请刷新页面后重试。',
        presentationKind: 'session',
      };
    }
    if (error.status === 422) {
      return {
        role: 'assistant',
        text: '请求格式未通过校验，请重新输入后再试。',
        presentationKind: 'validation',
      };
    }
    if (error.status === 503) {
      return {
        role: 'assistant',
        text: '办理服务暂时不可用，请稍后再试。',
        presentationKind: 'service',
      };
    }
    return {
      role: 'assistant',
      text: '请求未能完成，请稍后再试。',
      presentationKind: 'request_error',
    };
  }
  return {
    role: 'assistant',
    text: '网络连接异常，请稍后再试。',
    presentationKind: 'network',
  };
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

const targetSystemLabels: Record<TargetSystem, string> = {
  oa: 'OA',
  u8: 'U8',
  hikvision_ivms: '海康 iVMS',
};

function AssistantDetails({ entry }: { entry: Extract<TranscriptEntry, { role: 'assistant' }> }) {
  if (entry.presentationKind === 'clarification') {
    return (
      <Text className={styles.guidance}>
        请将原请求与明确范围一起完整重述为一条新请求。
      </Text>
    );
  }
  if (entry.presentationKind === 'confirmation') {
    return (
      <Text strong className={styles.confirmationNotice}>
        当前入口暂不能继续确认。
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
  return null;
}

export default function ChatPage() {
  const { token } = theme.useToken();
  const [sessionId] = useState(() => crypto.randomUUID());
  const [draft, setDraft] = useState('');
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const requestInFlight = useRef(false);

  const mutation = useMutation({
    mutationFn: async (message: string) => {
      try {
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
      setTranscript((current) => [...current, projectedResponse]);
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
    setTranscript((current) => [...current, { role: 'user', text: message }]);
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
    <main className={styles.page} style={cssVariables}>
      <header className={styles.pageHeader}>
        <div>
          <Text className={styles.eyebrow}>ETERNALAI · RUNTIME</Text>
          <Title level={1} className={styles.title}>
            自然语言办理入口
          </Title>
          <Paragraph className={styles.subtitle}>
            用一条完整请求查询当前已接入的业务能力。系统会明确反馈办理结果、需要补充的范围或不可办理原因。
          </Paragraph>
        </div>
        <Tag variant="filled" className={styles.sessionTag}>
          当前页面会话
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
                      <AssistantDetails entry={entry} />
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
    </main>
  );
}
