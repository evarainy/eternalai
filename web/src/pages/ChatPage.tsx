/// <reference types="vite/client" />

import { forwardRef, useRef } from 'react';
import type { ComponentProps, FormEvent } from 'react';
import { Conversations, Prompts, Sender, Welcome } from '@ant-design/x';
import { useMutation } from '@tanstack/react-query';
import { Button, Input, Typography } from 'antd';
import type { TextAreaRef } from 'antd/es/input/TextArea';
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

/** 没有办成的那几类；用于提醒用户「这不是没有数据，是这一条没办成」。 */
const UNSUCCESSFUL_KINDS: ReadonlySet<PresentationKind> = new Set<PresentationKind>([
  'denied',
  'unavailable',
  'failed',
  'incompatible',
  'csrf',
  'session',
  'validation',
  'service',
  'network',
  'request_error',
]);

const targetSystemLabels: Record<
  Exclude<UIComponentTargetSystem, null>,
  string
> = {
  oa: 'OA',
  u8: 'U8',
  hikvision_ivms: '海康 iVMS',
};

/**
 * 提示词卡。2026-08-27「低数字素养用户的界面硬约束」§二 的缓解模式要求给可见示例，不能只留一个空
 * 白输入框；这里的四条与 `Chat.dc.html` 定稿一致。
 */
const STARTER_PROMPTS = [
  {
    key: 'today',
    label: '我今天有什么要办的？',
    description: '查 OA 待办，按截止时间排',
  },
  {
    key: 'due-soon',
    label: '有没有快到期的事？',
    description: '只看这两天到期的',
  },
  {
    key: 'messages',
    label: '最近有什么系统消息？',
    description: '查 OA 系统消息',
  },
  {
    key: 'capabilities',
    label: '你能帮我做什么？',
    description: '看看现在会哪些事',
  },
] as const;

type RequestTextAreaProps = ComponentProps<typeof Input.TextArea>;

/**
 * `Sender` 会把传给它的 `id` / `aria-*` 同时贴到外层容器和内部 textarea 上，于是一个可见标签会指向
 * 两个元素。这里改为只在真正的输入框上挂标签与说明，容器保持干净：可见标签仍然存在（不用 placeholder
 * 代替标签），而 `getByLabelText` 也只会命中输入框本身。
 */
const RequestTextArea = forwardRef<TextAreaRef, RequestTextAreaProps>(
  function RequestTextArea(props, ref) {
    return (
      <Input.TextArea
        {...props}
        aria-describedby="chat-request-hint"
        aria-label="办理请求"
        id="chat-request"
        ref={ref}
      />
    );
  },
);

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
  const draft = useAIDockStore((state) => state.draft);
  const transcript = useAIDockStore((state) => state.transcript);
  const appendTranscript = useAIDockStore((state) => state.appendTranscript);
  const setDraft = useAIDockStore((state) => state.setDraft);
  const startNewSession = useAIDockStore((state) => state.startNewSession);
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

  const lastEntry = transcript.at(-1);
  const lastEntryFailed =
    lastEntry !== undefined &&
    lastEntry.role === 'assistant' &&
    UNSUCCESSFUL_KINDS.has(lastEntry.presentationKind);

  return (
    <div className={styles.page}>
      {/*
        左栏「我问过的」。2026-09-02 裁决把它从工作事项页迁到这里，但 2026-08-27 §一/§六 同时裁定
        会话持久化整体归 P3——P2 不扩 sessions 表、不建会话 API。因此这里只列**本次**临时会话，并
        如实写明历史存不起来，不做刷新即失效却看起来像历史的列表。
      */}
      <aside aria-label="我问过的" className={styles.sessionRail}>
        <Button block className={styles.newSessionButton} onClick={startNewSession}>
          新对话
        </Button>
        <h2 className={styles.railTitle}>我问过的</h2>
        {transcript.length === 0 ? (
          <p className={styles.railText}>现在没有正在进行的对话。</p>
        ) : (
          <Conversations
            activeKey="current"
            aria-label="当前对话"
            items={[{ key: 'current', label: '本次对话' }]}
          />
        )}
        <p className={styles.railText}>
          以前问过的还存不起来。刷新页面或者关掉浏览器，上面这一条就没有了。
        </p>
        <p className={styles.railText}>
          能把问过的存下来要等以后的版本。现在要留档的事，请到「工作事项」里办，那里的记录是存住的。
        </p>
      </aside>

      <div className={styles.main}>
        <header className={styles.pageHeader}>
          <Title level={1} className={styles.title}>
            把要办的事说清楚
          </Title>
          <Paragraph className={styles.subtitle}>
            写清对象、时间和想得到的结果。系统会告诉你已经完成、还需补什么，或为什么暂时不能办理。
          </Paragraph>
        </header>

        <section className={styles.conversation} aria-label="办理会话">
          <div
            className={styles.transcript}
            aria-live="polite"
            aria-busy={mutation.isPending}
          >
            {transcript.length === 0 ? (
              <div className={styles.emptyState}>
                <Welcome
                  className={styles.welcome}
                  variant="borderless"
                  title="这里现在是空的，因为你还没有问过。"
                  description="我能查 OA 待办和 OA 系统消息，说人话就行。不会办的事我会直接说不会，不瞎编。"
                />
                <Prompts
                  className={styles.prompts}
                  items={STARTER_PROMPTS.map((prompt) => ({ ...prompt }))}
                  onItemClick={(info) => {
                    const label = info.data.label;
                    setDraft(typeof label === 'string' ? label : '');
                  }}
                  title="可以这样问我"
                  vertical
                />
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

            {lastEntryFailed && !mutation.isPending ? (
              <p className={styles.failureNotice}>
                上面这一条没有办成。这里显示的是原因，不是「你没有要办的事」。
              </p>
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
            <Sender
              autoSize={{ minRows: 2, maxRows: 6 }}
              className={styles.sender}
              components={{ input: RequestTextArea }}
              disabled={mutation.isPending}
              footer={
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
              }
              onChange={(value) => setDraft(value)}
              onSubmit={submit}
              placeholder="请完整描述要办理或查询的事项…"
              suffix={false}
              value={draft}
            />
          </form>
        </section>
      </div>
    </div>
  );
}
