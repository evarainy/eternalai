/// <reference types="vite/client" />

import { forwardRef, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentProps, FormEvent } from 'react';
import { Conversations, Prompts, Sender, Welcome } from '@ant-design/x';
import { useMutation } from '@tanstack/react-query';
import { Alert, Button, Checkbox, Input, Typography } from 'antd';
import type { TextAreaRef } from 'antd/es/input/TextArea';
import { ConfirmCard } from '../components/ConfirmCard';
import { RecordsList } from '../components/RecordsList';
import { Icon } from '../shared/ui/Icon';
import {
  projectResponse,
  type PresentationKind,
  type ProjectedResponse,
} from '../contracts/runtimeProjection';
import { projectRequestError } from '../contracts/runtimeRequestError';
import { userActionOutcomeMessages } from '../contracts/userActionOutcome';
import { compareBrowserRunUpdate } from '../contracts/browserRunProjection';
import { BrowserRunCard } from '../features/browser/BrowserRunCard';
import {
  cancelBrowserRun,
  parseBrowserAccepted,
  parseBrowserFailed,
  parseBrowserPending,
  readBrowserRun,
  submitBrowserMessage,
  type BrowserAccepted,
  type BrowserRunRead,
} from '../features/browser/api';
import {
  clearBrowserAccepted,
  loadBrowserAccepted,
  parseBrowserOwnerScope,
  parseBrowserSkillId,
  saveBrowserAccepted,
  type BrowserOwnerScope,
} from '../features/browser/acceptedStore';
import {
  handleActionApiV1RuntimeActionPost,
  handleApiV1RuntimeHandlePost,
} from '../generated/runtime/runtime';
import type { UIComponentTargetSystem, UserAction } from '../generated/runtime/runtime.schemas';
import { useAIDockStore } from '../stores/aiDockStore';
import { useAuthStore } from '../stores/authStore';
import { useCurrentIdentity, useIdentityQuery } from '../app/identity';
import { greetingWithName } from './chatGreeting';
import styles from './ChatPage.module.css';

const { Text } = Typography;

interface ChatPageProps {
  /** A trusted server configuration must supply the one published skill ID. */
  browserSkillId?: string;
}

interface BrowserTarget {
  readonly accepted: BrowserAccepted;
  readonly conversationId: string;
  readonly owner: BrowserOwnerScope;
  readonly authGeneration: number;
  readonly requestGeneration: number;
}

interface ChatSubmission {
  readonly message: string;
  readonly sessionId: string;
  readonly authGeneration: number;
  readonly browser: null | {
    readonly skillId: string;
    readonly requestId: string;
    readonly owner: BrowserOwnerScope;
  };
}

function BrowserRunMonitor({
  target, onTerminal,
}: { target: BrowserTarget; onTerminal: (terminal: boolean) => void }) {
  const [read, setRead] = useState<BrowserRunRead | null>(null);
  const [pollError, setPollError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const currentRead = useRef<BrowserRunRead | null>(null);
  const cancelRequest = useRef<AbortController | null>(null);
  const live = useRef(true);

  const applyRead = useCallback((incoming: BrowserRunRead): boolean | null => {
    if (!live.current) return null;
    const decision = compareBrowserRunUpdate(currentRead.current?.run ?? null, incoming.run, {
      taskId: target.accepted.task_id,
      runId: target.accepted.run_id,
      requestGeneration: target.requestGeneration,
      currentGeneration: target.requestGeneration,
    });
    if (decision === 'apply' || decision === 'noop') {
      currentRead.current = incoming;
      setRead(incoming);
      setPollError(false);
      if (incoming.run.result !== null) onTerminal(true);
      return incoming.run.result !== null;
    } else if (decision === 'conflict') {
      setRead(null);
      setPollError(true);
      return null;
    }
    return false;
  }, [target, onTerminal]);

  useEffect(() => {
    live.current = true;
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const controller = new AbortController();
    const poll = async () => {
      if (!live.current || controller.signal.aborted) return;
      if (++attempts > 90) {
        setPollError(true);
        return;
      }
      try {
        const response = await readBrowserRun(
          target.accepted.task_id, target.accepted.run_id,
          target.conversationId, controller.signal,
        );
        if (!live.current || controller.signal.aborted) return;
        if (applyRead(response) !== false) return;
        timer = setTimeout(() => { void poll(); }, 2000);
      } catch {
        if (live.current && !controller.signal.aborted) {
          setRead(null);
          setPollError(true);
        }
      }
    };
    void poll();
    return () => {
      live.current = false;
      controller.abort();
      cancelRequest.current?.abort();
      if (timer !== null) clearTimeout(timer);
    };
  }, [target, refresh, applyRead]);

  const onCancel = async (taskId: string, runId: string) => {
    if (!live.current || taskId !== target.accepted.task_id
        || runId !== target.accepted.run_id) return;
    const controller = new AbortController();
    cancelRequest.current = controller;
    try {
      const response = await cancelBrowserRun(
        taskId, runId, target.conversationId, controller.signal,
      );
      if (live.current && !controller.signal.aborted && applyRead(response) === null) {
        throw new Error('browser_run_update_conflict');
      }
    } finally {
      if (cancelRequest.current === controller) cancelRequest.current = null;
    }
  };

  if (read === null) {
    return pollError ? (
      <Alert type="warning" showIcon title="无法读取浏览器任务"
        action={<Button onClick={() => setRefresh((value) => value + 1)}>重试读取</Button>} />
    ) : <p role="status">正在读取浏览器任务…</p>;
  }
  return (
    <div>
      <BrowserRunCard
        parsedView={read.run}
        taskId={target.accepted.task_id}
        runId={target.accepted.run_id}
        requestGeneration={target.requestGeneration}
        currentGeneration={target.requestGeneration}
        resultValue={read.value}
        onCancel={onCancel}
      />
      {pollError ? (
        <Alert type="warning" showIcon title="浏览器任务更新暂不可用"
          action={<Button onClick={() => setRefresh((value) => value + 1)}>重试读取</Button>} />
      ) : null}
    </div>
  );
}

const presentationLabels: Record<PresentationKind, string> = {
  completed: '办理完成',
  cancelled: '已取消',
  confirmation_invalidated: '确认已失效',
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
  business_platform: '业务平台',
};

/**
 * 提示词卡。2026-08-27「低数字素养用户的界面硬约束」§二 的缓解模式要求给可见示例，不能只留一个空
 * 白输入框；这里的四条主问句与副说明都逐字照 `Chat.dc.html` 定稿。
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
    description: '只看 48 小时内到期的',
  },
  {
    key: 'messages',
    label: '最近有什么系统消息？',
    description: '查 OA 系统消息',
  },
  {
    key: 'capabilities',
    label: '你能帮我做什么？',
    description: '看看我现在会哪些事',
  },
] as const;

type RequestTextAreaProps = ComponentProps<typeof Input.TextArea>;

/**
 * `Sender` 会把传给它的 `id` / `aria-*` 同时贴到外层容器和内部 textarea 上，于是一个标签会指向两个
 * 元素。这里只在真正的输入框上挂名称，容器保持干净，`getByLabelText` 也只会命中输入框本身。
 *
 * 2026-09-04：可见标签「办理请求」按返修要求删掉（画板 `Chat.dc.html` 上没有这一行），无障碍名称
 * 改由 `aria-label` 承担，读屏软件听到的仍是「办理请求」；`aria-describedby` 指向的那行底注同轮删除，
 * 属性一并去掉，避免指向一个不存在的 id。
 */
const RequestTextArea = forwardRef<TextAreaRef, RequestTextAreaProps>(
  function RequestTextArea(props, ref) {
    return (
      <Input.TextArea
        {...props}
        aria-label="办理请求"
        id="chat-request"
        ref={ref}
      />
    );
  },
);

function AssistantDetails({
  entry,
  onAction,
  terminalNotice,
}: {
  entry: ProjectedResponse;
  onAction: (action: UserAction) => Promise<void>;
  terminalNotice: string | null;
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
      {entry.mcpRecoveryPath && <a href={entry.mcpRecoveryPath}>查看原操作并恢复</a>}
      {entry.actionOutcome === null ? null : (
        <Text strong className={styles.outcomeNotice}>
          {userActionOutcomeMessages[entry.actionOutcome]}
        </Text>
      )}
      {entry.confirm === null ? null : (
        <ConfirmCard
          confirm={entry.confirm}
          responseId={entry.responseId}
          onAction={onAction}
          terminalNotice={terminalNotice}
        />
      )}
      {entry.records === null ? null : <RecordsList records={entry.records} />}
    </>
  );
}

export default function ChatPage({ browserSkillId }: ChatPageProps) {
  const draft = useAIDockStore((state) => state.draft);
  const transcript = useAIDockStore((state) => state.transcript);
  const currentSessionId = useAIDockStore((state) => state.sessionId);
  const appendTranscript = useAIDockStore((state) => state.appendTranscript);
  const setDraft = useAIDockStore((state) => state.setDraft);
  const startNewSession = useAIDockStore((state) => state.startNewSession);
  const confirmationResults = useAIDockStore((state) => state.confirmationResults);
  const confirmationNotice = (responseId: string | null) => {
    const outcome = responseId === null ? undefined : confirmationResults[responseId];
    return outcome === undefined ? null : userActionOutcomeMessages[outcome];
  };
  const identity = useCurrentIdentity();
  const { data: me, refetch: refetchMe } = useIdentityQuery();
  const authStatus = useAuthStore((state) => state.status);
  const authGeneration = useAuthStore((state) => state.generation);
  const browserOwner = useMemo(() => parseBrowserOwnerScope(me), [me]);
  const meSkillId = useMemo(() => parseBrowserSkillId(me), [me]);
  const activeSkillId = meSkillId !== null
    && (browserSkillId === undefined || browserSkillId === meSkillId) ? meSkillId : null;
  const browserAvailable = authStatus === 'authenticated'
    && browserOwner !== null && activeSkillId !== null;
  const [browserOptIn, setBrowserOptIn] = useState(false);
  const [browserTarget, setBrowserTarget] = useState<BrowserTarget | null>(null);
  const [browserTerminal, setBrowserTerminal] = useState(false);
  const [browserStorageError, setBrowserStorageError] = useState(false);
  const [browserIdentityReady, setBrowserIdentityReady] = useState(true);
  const [pendingSubmission, setPendingSubmission] = useState<ChatSubmission | null>(null);
  const [retrySubmission, setRetrySubmission] = useState<ChatSubmission | null>(null);
  const browserGeneration = useRef(0);
  const restoredScope = useRef<string | null>(null);
  const ownerKey = browserOwner === null ? null
    : `${browserOwner.tenant_id}/${browserOwner.user_id}`;
  const ownerKeyRef = useRef<string | null>(ownerKey);
  ownerKeyRef.current = ownerKey;
  const skillIdRef = useRef<string | null>(meSkillId);
  skillIdRef.current = meSkillId;
  const identityCheck = useRef(0);
  const requestInFlight = useRef(false);
  const activeSubmission = useRef<ChatSubmission | null>(null);
  const browserSubmitContext = useRef<{
    controller: AbortController; ownerKey: string;
  } | null>(null);

  useEffect(() => () => {
    browserSubmitContext.current?.controller.abort();
    activeSubmission.current = null;
  }, []);
  useEffect(() => { setBrowserIdentityReady(true); }, [authGeneration]);
  useEffect(() => {
    if (browserSubmitContext.current !== null
        && browserSubmitContext.current.ownerKey !== ownerKey) {
      browserSubmitContext.current.controller.abort();
    }
  }, [ownerKey]);

  const recheckBrowserIdentity = useCallback(() => {
    const auth = useAuthStore.getState();
    if (auth.status !== 'authenticated') return;
    const sequence = ++identityCheck.current;
    const previousOwner = ownerKeyRef.current;
    const previousSkill = skillIdRef.current;
    setBrowserIdentityReady(false);
    void refetchMe().then((result) => {
      if (identityCheck.current !== sequence
          || useAuthStore.getState().generation !== auth.generation) return;
      if (!result.isSuccess) return;
      const nextOwner = parseBrowserOwnerScope(result.data);
      const nextSkill = parseBrowserSkillId(result.data);
      const nextKey = nextOwner === null ? null
        : `${nextOwner.tenant_id}/${nextOwner.user_id}`;
      if (nextKey !== previousOwner || nextSkill !== previousSkill) {
        clearBrowserAccepted();
        browserSubmitContext.current?.controller.abort();
        setBrowserTarget(null);
        setBrowserTerminal(false);
        setPendingSubmission(null);
        setRetrySubmission(null);
        setBrowserOptIn(false);
      }
      setBrowserIdentityReady(true);
    }).catch(() => { /* Keep the previous browser result hidden until retry. */ });
  }, [refetchMe]);

  useEffect(() => {
    if (authStatus !== 'authenticated') return;
    let channel: BroadcastChannel | null = null;
    const checkSequence = identityCheck;
    const onFocus = () => recheckBrowserIdentity();
    const onVisible = () => {
      if (document.visibilityState === 'visible') recheckBrowserIdentity();
    };
    try {
      if (typeof BroadcastChannel !== 'undefined') {
        channel = new BroadcastChannel('eternalai-auth');
        channel.onmessage = (event: MessageEvent<unknown>) => {
          if (event.data === 'recheck-identity') recheckBrowserIdentity();
        };
      }
    } catch { /* Focus still checks current server identity. */ }
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      ++checkSequence.current;
      channel?.close();
      window.removeEventListener('focus', onFocus);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [authStatus, authGeneration, recheckBrowserIdentity]);

  useEffect(() => {
    if (!browserAvailable || browserOwner === null) return;
    const scope = `${authGeneration}/${browserOwner.tenant_id}/${browserOwner.user_id}`;
    if (restoredScope.current === scope) return;
    restoredScope.current = scope;
    const stored = loadBrowserAccepted(browserOwner);
    if (stored === null) return;
    const state = useAIDockStore.getState();
    if (state.sessionId !== null && state.sessionId !== stored.conversation_id) {
      clearBrowserAccepted();
      return;
    }
    if (state.sessionId === null) {
      if (state.transcript.length > 0 || state.draft.trim()) {
        clearBrowserAccepted();
        return;
      }
      useAIDockStore.setState({ sessionId: stored.conversation_id });
    }
    setBrowserTarget({
      accepted: stored.accepted, conversationId: stored.conversation_id,
      owner: stored.owner, authGeneration,
      requestGeneration: ++browserGeneration.current,
    });
    setBrowserTerminal(false);
  }, [browserAvailable, browserOwner, authGeneration]);

  useEffect(() => {
    if (browserTarget === null) return;
    if (!browserAvailable || browserOwner === null
        || browserTarget.authGeneration !== authGeneration
        || browserTarget.owner.tenant_id !== browserOwner.tenant_id
        || browserTarget.owner.user_id !== browserOwner.user_id
        || useAIDockStore.getState().sessionId !== browserTarget.conversationId) {
      clearBrowserAccepted();
      setBrowserTarget(null);
      setBrowserTerminal(false);
      setBrowserOptIn(false);
      setPendingSubmission(null);
      setRetrySubmission(null);
    }
  }, [browserTarget, browserAvailable, browserOwner, authGeneration, currentSessionId]);

  useEffect(() => {
    const stillCurrent = (submission: ChatSubmission): boolean =>
      authStatus === 'authenticated' && submission.authGeneration === authGeneration
      && submission.sessionId === currentSessionId
      && (submission.browser === null || ownerKey ===
        `${submission.browser.owner.tenant_id}/${submission.browser.owner.user_id}`);
    if (pendingSubmission !== null && !stillCurrent(pendingSubmission)) {
      setPendingSubmission(null);
    }
    if (retrySubmission !== null && !stillCurrent(retrySubmission)) {
      setRetrySubmission(null);
    }
    if (!browserAvailable && browserOptIn) setBrowserOptIn(false);
  }, [currentSessionId, authStatus, authGeneration, ownerKey, browserAvailable,
    browserOptIn, pendingSubmission, retrySubmission]);

  const submissionIsCurrent = (submission: ChatSubmission): boolean => {
    const auth = useAuthStore.getState();
    return auth.status === 'authenticated' && auth.generation === submission.authGeneration
      && useAIDockStore.getState().sessionId === submission.sessionId
      && (submission.browser === null || ownerKeyRef.current ===
        `${submission.browser.owner.tenant_id}/${submission.browser.owner.user_id}`);
  };

  const sendBrowserSubmission = async (submission: ChatSubmission): Promise<unknown> => {
    const browser = submission.browser;
    if (browser === null) throw new Error('browser_submission_invalid');
    const controller = new AbortController();
    browserSubmitContext.current = {
      controller, ownerKey: `${browser.owner.tenant_id}/${browser.owner.user_id}`,
    };
    const stopOnSessionChange = useAIDockStore.subscribe((state) => {
      if (state.sessionId !== submission.sessionId) controller.abort();
    });
    const stopOnAuthChange = useAuthStore.subscribe((state) => {
      if (state.status !== 'authenticated' || state.generation !== submission.authGeneration) {
        controller.abort();
      }
    });
    try {
      return await submitBrowserMessage(
        submission.message, submission.sessionId, browser.skillId, browser.requestId,
        controller.signal,
      );
    } finally {
      stopOnSessionChange();
      stopOnAuthChange();
      if (browserSubmitContext.current?.controller === controller) {
        browserSubmitContext.current = null;
      }
    }
  };

  const mutation = useMutation({
    mutationFn: async (message: string) => {
      const submission = activeSubmission.current;
      if (submission === null || submission.message !== message) {
        throw new Error('chat_submission_invalid');
      }
      const { sessionId } = submission;
      const reference = /^(?:确认|confirm)\s+(\S+)$/i.exec(message)?.[1] ?? null;
      try {
        const response = submission.browser === null
          ? await handleApiV1RuntimeHandlePost({
            channel: 'web', session_id: sessionId, message, client_capabilities: {},
          })
          : await sendBrowserSubmission(submission);
        const pending = submission.browser === null ? null : parseBrowserPending(response);
        if (pending !== null) {
          if (submissionIsCurrent(submission)) {
            setPendingSubmission(submission);
            setRetrySubmission(null);
          }
          return null;
        }
        if (submission.browser !== null) {
          const accepted = parseBrowserAccepted(response);
          if (accepted !== null) {
            if (submissionIsCurrent(submission)) {
              setBrowserTarget({
                accepted, conversationId: sessionId, owner: submission.browser.owner,
                authGeneration: submission.authGeneration,
                requestGeneration: ++browserGeneration.current,
              });
              setBrowserTerminal(false);
              setPendingSubmission(null);
              setRetrySubmission(null);
              setBrowserStorageError(!saveBrowserAccepted(
                submission.browser.owner, sessionId, accepted,
              ));
            }
            return null;
          }
        }
        const result = projectResponse(response);
        if (submission.browser !== null && submissionIsCurrent(submission)) {
          setPendingSubmission(null);
          setRetrySubmission(parseBrowserFailed(response) === null ? submission : null);
        }
        useAIDockStore.getState().applyConfirmationResult(sessionId, reference, result);
        return result;
      } catch (error) {
        const projectedError = projectRequestError(error);
        if (projectedError === null && submission.browser === null) {
          throw error;
        }
        const result = projectedError ?? projectResponse(null);
        if (submission.browser !== null && submissionIsCurrent(submission)) {
          setPendingSubmission(null);
          setRetrySubmission(submission);
        }
        useAIDockStore.getState().applyConfirmationResult(sessionId, reference, result);
        return result;
      }
    },
    onSettled: () => {
      activeSubmission.current = null;
      requestInFlight.current = false;
    },
  });

  const submit = (retry?: ChatSubmission) => {
    const message = retry?.message ?? draft.trim();
    if (!message || requestInFlight.current) {
      return;
    }
    if (retry !== undefined && !submissionIsCurrent(retry)) return;
    if (retry?.browser !== null && retry?.browser !== undefined && !browserIdentityReady) return;
    if (retry === undefined && browserOptIn && !browserAvailable) return;
    if (retry === undefined && browserOptIn && !browserIdentityReady) return;
    if (retry === undefined && browserOptIn
        && ((browserTarget !== null && !browserTerminal) || pendingSubmission !== null)) return;
    const sessionId = retry?.sessionId ?? useAIDockStore.getState().ensureSession();
    const browser = retry?.browser ?? (browserOptIn && browserOwner !== null
      && activeSkillId !== null ? {
        skillId: activeSkillId, requestId: crypto.randomUUID(), owner: browserOwner,
      } : null);
    const submission: ChatSubmission = retry ?? {
      message, sessionId, authGeneration: useAuthStore.getState().generation, browser,
    };
    requestInFlight.current = true;
    if (retry === undefined) {
      setRetrySubmission(null);
      appendTranscript({ role: 'user', text: message });
      setDraft('');
    }
    activeSubmission.current = submission;
    mutation.mutate(message);
  };

  const submitConfirmation = async (action: UserAction) => {
    const store = useAIDockStore.getState();
    const sessionId = store.ensureSession();
    if (store.confirmationResults[action.response_id] !== undefined) return;
    try {
      const projectedResponse = projectResponse(
        await handleActionApiV1RuntimeActionPost({
          channel: 'web', session_id: sessionId, action,
        }),
      );
      useAIDockStore.getState().applyConfirmationResult(sessionId, action.response_id, projectedResponse);
    } catch (error) {
      const projectedError = projectRequestError(error);
      if (projectedError !== null) {
        useAIDockStore.getState().applyConfirmationResult(sessionId, action.response_id, projectedError);
      }
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submit();
  };

  const startFreshSession = () => {
    clearBrowserAccepted();
    setBrowserTarget(null);
    setBrowserTerminal(false);
    setPendingSubmission(null);
    setRetrySubmission(null);
    setBrowserStorageError(false);
    setBrowserOptIn(false);
    startNewSession();
  };

  const visibleBrowserTarget = browserAvailable && browserIdentityReady
    && browserOwner !== null
    && browserTarget !== null && currentSessionId === browserTarget.conversationId
    && browserTarget.authGeneration === authGeneration
    && browserTarget.owner.tenant_id === browserOwner.tenant_id
    && browserTarget.owner.user_id === browserOwner.user_id ? browserTarget : null;
  const browserSubmissionBlocked = browserOptIn
    && (!browserIdentityReady || (browserTarget !== null && !browserTerminal)
      || pendingSubmission !== null);

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
        <Button block className={styles.newSessionButton} onClick={startFreshSession}>
          新对话
        </Button>
        <h2 className={styles.railTitle}>我问过的</h2>
        {transcript.length === 0 ? null : (
          <Conversations
            activeKey="current"
            aria-label="当前对话"
            items={[{ key: 'current', label: '本次对话' }]}
          />
        )}
        {/*
          2026-09-04 返修：左栏原来三行说明（「现在没有正在进行的对话。」「以前问过的还存不起来，
          刷新就没了。」「要留档请到「工作事项」里办。」）砍成**一行**。留下的是那条真限制——历史
          存不起来；「现在没有对话」由空列表本身表明，不用再写一句。
        */}
        <p className={styles.railText}>以前问过的存不起来，刷新就没了。</p>
      </aside>

      <div className={styles.main}>
        {/*
          2026-09-03：删掉「把要办的事说清楚 / 写清对象、时间和想要的结果」这一组说明；所在页由
          左导航高亮表明，标题只留给读屏软件定位，不占版面。
        */}
        <h1 className={styles.pageTitle}>AI 助手</h1>

        <section className={styles.conversation} aria-label="办理会话">
          <div
            className={styles.transcript}
            aria-live="polite"
            aria-busy={mutation.isPending}
          >
            {transcript.length === 0 ? (
              <div className={styles.emptyState}>
                {/*
                  画板是「王主任，早上好」。姓名现在有数据源（`GET /api/v1/me` 的 display_name，
                  来自服务端签名的会话票据），所以称呼落地；「主任」是职务，OA 没有这个字段，不编。
                  取不到姓名时退回不带称呼的问候。版式（60px 图标 + 大字标题 + 一句 17px 说明）
                  照画板。原来标题位放的是「这里现在是空的，因为你还没有问过。」——把一句说明做成了
                  全页最大字号的标题。那句实话没有删，收进了下面这行说明里。
                */}
                <Welcome
                  className={styles.welcome}
                  variant="borderless"
                  icon={<Icon name="spark" size={26} strokeWidth={1.9} />}
                  title={greetingWithName(identity.displayName)}
                  description="这里还没有对话。我能帮你查 OA 里的待办和系统消息，说人话就行。"
                />
                <Prompts
                  className={styles.prompts}
                  items={STARTER_PROMPTS.map((prompt) => ({ ...prompt }))}
                  onItemClick={(info) => {
                    const label = info.data.label;
                    setDraft(typeof label === 'string' ? label : '');
                  }}
                  title="可以这样问我"
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
                          onAction={submitConfirmation}
                          terminalNotice={confirmationNotice(entry.responseId)}
                        />
                      ) : null}
                    </article>
                  </li>
                ))}
              </ol>
            )}

            {visibleBrowserTarget === null ? null : (
              <BrowserRunMonitor
                key={`${visibleBrowserTarget.requestGeneration}/${visibleBrowserTarget.accepted.run_id}`}
                target={visibleBrowserTarget}
                onTerminal={setBrowserTerminal}
              />
            )}
            {!browserIdentityReady && browserTarget !== null ? (
              <Alert type="warning" showIcon title="正在核对浏览器任务身份"
                action={<Button onClick={recheckBrowserIdentity}>重试核对</Button>} />
            ) : null}
            {browserStorageError && visibleBrowserTarget !== null ? (
              <Alert type="warning" showIcon title="刷新后可能无法恢复此浏览器任务" />
            ) : null}
            {pendingSubmission !== null && submissionIsCurrent(pendingSubmission) ? (
              <Alert type="info" showIcon title="浏览器任务正在准备，尚未生成运行记录"
                action={<Button disabled={mutation.isPending || !browserIdentityReady}
                  onClick={() => submit(pendingSubmission)}>继续检查</Button>} />
            ) : null}
            {retrySubmission !== null && submissionIsCurrent(retrySubmission) ? (
              <Alert type="warning" showIcon title="浏览器请求未完成，可使用原请求重试"
                action={<Button disabled={mutation.isPending || !browserIdentityReady}
                  onClick={() => submit(retrySubmission)}>重试原请求</Button>} />
            ) : null}

            {mutation.isPending ? (
              <div className={styles.pendingNotice} role="status">
                <span className={styles.pendingDot} aria-hidden="true" />
                正在办理，请稍候…
              </div>
            ) : null}

            {lastEntryFailed && !mutation.isPending
              && visibleBrowserTarget === null && pendingSubmission === null ? (
              <p className={styles.failureNotice}>
                上面这一条没有办成。这里显示的是原因，不是「你没有要办的事」。
              </p>
            ) : null}
          </div>

          {/*
            2026-09-04 返修第 4 条：删掉底注「回答基于 OA 实时数据，正式办理以 OA 为准」，也删掉输入框
            上方那个可见标签「办理请求」（画板 `Chat.dc.html` 上没有这一行）。无障碍名称没有丢，改由
            输入框自己的 `aria-label` 承担。输入框同轮放大：起始 1 行改 3 行，边界改成可辨边界。

            「一屏内看得见输入框」仍不靠估算高度，靠结构：`.transcript` 是唯一可伸缩可滚动的一格，
            `.composer` 是 `flex:none`，所以输入框放大只会挤压对话区，不会被顶出视口。
          */}
          <form
            className={styles.composer}
            data-focus-ring="host"
            onSubmit={handleSubmit}
          >
            <Sender
              autoSize={{ minRows: 3, maxRows: 6 }}
              className={styles.sender}
              components={{ input: RequestTextArea }}
              disabled={mutation.isPending}
              footer={
                <div className={styles.composerActions}>
                  {browserAvailable && browserIdentityReady ? (
                    <Checkbox checked={browserOptIn} onChange={(event) => {
                      setBrowserOptIn(event.target.checked);
                    }}>
                      使用浏览器只读技能
                    </Checkbox>
                  ) : null}
                  <Button
                    type="primary"
                    htmlType="submit"
                    loading={mutation.isPending}
                    disabled={!draft.trim() || mutation.isPending || browserSubmissionBlocked}
                  >
                    发送
                  </Button>
                </div>
              }
              onChange={(value) => {
                setDraft(value);
                if (retrySubmission !== null && value.trim() !== retrySubmission.message) {
                  setRetrySubmission(null);
                }
              }}
              onSubmit={() => submit()}
              placeholder="问点什么，比如：我今天有什么要办的"
              suffix={false}
              value={draft}
            />
          </form>
        </section>
      </div>
    </div>
  );
}
