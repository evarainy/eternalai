/// <reference types="vite/client" />

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Alert, Button, Checkbox, Space, Typography } from 'antd';
import { ConfirmCard } from '../components/ConfirmCard';
import { RecordsList } from '../components/RecordsList';
import { Desk, DeskSide } from '../features/desk/Desk';
import { DeliveryComposerButton } from '../features/messages/DeliveryComposer';
import { Icon } from '../shared/ui/Icon';
import { ApiError } from '../api/mutator';
import {
  incompatibleResponse,
  projectResponse,
  projectTextResponse,
  isRecord,
  type PresentationKind,
  type ProjectedResponse,
  type DFormCollectionReference,
} from '../contracts/runtimeProjection';
import { projectRequestError } from '../contracts/runtimeRequestError';
import { userActionOutcomeMessages } from '../contracts/userActionOutcome';
import { fieldLabel, targetSystemLabels } from '../contracts/businessLabels';
import { compareBrowserRunUpdate } from '../contracts/browserRunProjection';
import { BrowserRunCard } from '../features/browser/BrowserRunCard';
import { ReportJobCard } from '../features/background-jobs/ReportJobCard';
import { SheetFillPlanCard } from '../features/background-jobs/SheetFillPlanCard';
import {
  forgetPendingSheetFill,
  loadPendingSheetFills,
  type PendingSheetFill,
} from '../features/background-jobs/sheetFill';
import { recoveryScopeOf } from '../features/background-jobs/recoveryStorage';
import {
  forgetReportJob,
  loadReportJobs,
  rememberReportJob,
} from '../features/background-jobs/reportJob';
import {
  BrowserFillStopHistoryNotice,
  BrowserHandoffComposerNotice,
  BrowserHandoffPanel,
  FILL_STOP_HISTORY_STATUS_LABEL,
} from '../features/browser/BrowserHandoffPanel';
import {
  cancelBrowserRun,
  decideBrowserFillStop,
  isActiveHandoff,
  isBrowserUnconfirmed,
  isRuntimeSessionBusy,
  parseBrowserAccepted,
  parseBrowserPending,
  readBrowserRun,
  SESSION_BUSY_NOTICE,
  submitBrowserMessage,
  submitOrdinaryBrowserCapableMessage,
  writePreviewFields,
  type BrowserAccepted,
  type BrowserFillFields,
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
import type { UserAction } from '../generated/runtime/runtime.schemas';
import { COLLECTION_LOST_NOTICE, useAIDockStore } from '../stores/aiDockStore';
import { useAuthStore } from '../stores/authStore';
import { useIdentityQuery } from '../app/identity';
import { returnUnsentMessage } from '../app/reauthRestore';
import { ChatAttachmentBar } from '../features/chat-upload/ChatAttachmentBar';
import {
  attachmentDownloadUrl,
  CHAT_ATTACHMENT_LIMIT,
  chatUploadMessage,
  isChatAttachmentRejection,
} from '../features/chat-upload/chatUploadApi';
import { useChatAttachments } from '../features/chat-upload/useChatAttachments';
import type { ChatAttachment } from '../stores/aiDockStore';
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
  readonly originalSubmission: ChatSubmission | null;
}

interface ChatSubmission {
  /** Frozen when the message is sent; retries never read the attachment bar again. */
  readonly attachments: readonly ChatAttachment[];
  readonly collection: DFormCollectionReference | null;
  readonly boundSessionId: string | null;
  readonly requestId: string;
  readonly owner: BrowserOwnerScope | null;
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
  target, onTerminal, onHandoff, fillStopMode = false, onRecoverOriginal, recovering, recoveryRevision,
}: {
  target: BrowserTarget;
  onTerminal: (terminal: boolean) => void;
  /** Sealed and not terminal (§8.4): the page may release ordinary chat, never D submission. */
  onHandoff: (active: boolean) => void;
  fillStopMode?: boolean;
  onRecoverOriginal: (() => void) | null;
  recovering: boolean;
  recoveryRevision: number;
}) {
  const [read, setRead] = useState<BrowserRunRead | null>(null);
  const [pollError, setPollError] = useState(false);
  const [readFailure, setReadFailure] = useState<{ status: number | null; code: string }>({
    status: null, code: 'browser_read_failed',
  });
  const [refresh, setRefresh] = useState(0);
  const [deciding, setDeciding] = useState(false);
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
      setReadFailure({ status: null, code: 'browser_read_failed' });
      onHandoff(isActiveHandoff(incoming));
      const terminal = incoming.run.result !== null;
      const cleanupPending = fillStopMode && incoming.run.result?.cleanup === 'pending';
      if (terminal && !cleanupPending) onTerminal(true);
      return terminal && !cleanupPending;
    } else if (decision === 'conflict') {
      setRead(null);
      setPollError(true);
      return null;
    }
    return false;
  }, [target, onTerminal, onHandoff, fillStopMode]);

  useEffect(() => {
    live.current = true;
    setReadFailure({ status: null, code: 'browser_read_failed' });
    let attempts = 0;
    let timeouts = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const controller = new AbortController();
    // D: 15-minute confirmation window + 1800-second supervisor and cleanup.
    const startedAt = performance.now();
    let deadline = startedAt + (fillStopMode ? 2_880_000 : 180_000);
    let handoffDeadline: string | null = null;
    let deadlineTimer: ReturnType<typeof setTimeout> | undefined;
    const armDeadline = () => {
      clearTimeout(deadlineTimer);
      deadlineTimer = setTimeout(() => { controller.abort(); setPollError(true); },
        Math.max(0, deadline - performance.now()));
    };
    armDeadline();
    const poll = async () => {
      if (!live.current || controller.signal.aborted) return;
      if (++attempts > Math.ceil((deadline - startedAt) / 2000) || performance.now() >= deadline) {
        clearTimeout(deadlineTimer);
        controller.abort();
        setPollError(true);
        return;
      }
      try {
        const response = await readBrowserRun(
          target.accepted.task_id, target.accepted.run_id,
          target.conversationId, controller.signal,
        );
        if (!live.current || controller.signal.aborted) return;
        timeouts = 0;
        if (applyRead(response) !== false) {
          clearTimeout(deadlineTimer);
          return;
        }
        if (currentRead.current === response && response.handoff != null
            && response.handoff.deadline !== handoffDeadline) {
          // §9.1: once sealed, observe for (deadline - sealed_at) + 180 s of monotonic time. Both
          // instants are server clock, so a fast or slow client clock cannot shorten observation.
          // Stopping observation is never a release.
          handoffDeadline = response.handoff.deadline;
          const sealedMs = Math.max(0, Date.parse(handoffDeadline) - Date.parse(response.handoff.sealed_at));
          deadline = performance.now() + sealedMs + 180_000;
          armDeadline();
        }
        timer = setTimeout(() => { void poll(); }, 2000);
      } catch (error) {
        if (live.current && !controller.signal.aborted && error instanceof Error
            && error.message === 'browser_read_timeout' && ++timeouts <= 3) {
          // A slow read keeps the last state (and an open remote screen) and reads again.
          timer = setTimeout(() => { void poll(); }, 2000);
          return;
        }
        if (live.current && !controller.signal.aborted) {
          clearTimeout(deadlineTimer);
          setRead(null);
          setPollError(true);
          const code = error instanceof ApiError && [
            'browser_write_preparation_invalid', 'browser_run_projection_invalid',
            'browser_chat_unavailable', 'session_not_found', 'browser_run_not_found',
            'authentication_required', 'browser_target_stale',
          ].includes(error.code) ? error.code
            : (error instanceof DOMException && error.name === 'AbortError')
              || (error instanceof Error && error.message === 'browser_read_timeout') ? 'browser_read_aborted'
              : error instanceof Error && error.message === 'browser_run_response_invalid'
                ? 'browser_run_response_invalid' : 'browser_read_failed';
          setReadFailure({ status: error instanceof ApiError ? error.status : null, code });
        }
      }
    };
    void poll();
    return () => {
      clearTimeout(deadlineTimer);
      live.current = false;
      controller.abort();
      cancelRequest.current?.abort();
      if (timer !== null) clearTimeout(timer);
    };
  }, [target, refresh, applyRead, fillStopMode, recoveryRevision]);

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

  const onDecide = async (decision: 'confirmed' | 'rejected') => {
    if (!live.current || deciding || read?.writePreview == null) return;
    setDeciding(true);
    const controller = new AbortController();
    cancelRequest.current = controller;
    try {
      const incoming = await decideBrowserFillStop(target.accepted.task_id, target.accepted.run_id,
        target.conversationId, decision, controller.signal);
      if (live.current && !controller.signal.aborted) {
        applyRead(incoming);
        setRefresh((value) => value + 1);
      }
    } catch {
      if (live.current && !controller.signal.aborted) setPollError(true);
    } finally {
      if (cancelRequest.current === controller) cancelRequest.current = null;
      if (live.current) setDeciding(false);
    }
  };

  if (read === null) {
    return pollError ? (
      <Alert type="warning" showIcon title="无法读取浏览器任务"
        description={`HTTP：${readFailure.status ?? '未取得'}；错误码：${readFailure.code}`}
        action={<Button onClick={() => setRefresh((value) => value + 1)}>重试读取</Button>} />
    ) : <p role="status">正在读取浏览器任务…</p>;
  }
  const handoff = fillStopMode ? read.handoff ?? null : null;
  // §9.4: an M2 D `completed` run means "filled and stopped", not a confirmed submission.
  const fillStopHistory = fillStopMode && read.run.status === 'completed' && handoff === null;
  // §9.1 pre-seal: "running" copy only once the window is really being filled (not while the write
  // confirmation is pending or missing); other pre-seal failures keep the existing card.
  const awaitingStart = handoff === null && read.run.status === 'running'
    && (read.writePreview != null || read.run.progress?.phase === 'queued');
  const panelShown = fillStopMode && !fillStopHistory && !awaitingStart;
  const panelCopy = panelShown && (handoff !== null || read.run.status === 'running'
    || read.run.status === 'cancelled');
  // §9.1: a sealed handoff that ended failed/cancelled — the panel says "协助已结束"; the card only
  // shows a neutral label. Unsealed failures keep the card's original notices.
  const handoffEnded = handoff !== null && read.run.result !== null
    && (read.run.status === 'failed' || read.run.status === 'cancelled');
  return (
    <div>
      {fillStopMode && read.run.progress?.phase === 'queued' && read.run.result === null
        && !read.run.cancel.requested && read.writePreview == null ? (
          <Alert type="warning" showIcon title="尚未取得可用的填写确认"
            description={onRecoverOriginal === null
              ? '当前页面缺少原请求信息，可继续读取或取消原任务。'
              : '可检查原请求并补齐确认准备，继续使用当前任务。'}
            action={<Button disabled={recovering || onRecoverOriginal === null} loading={recovering}
              onClick={() => onRecoverOriginal?.()}>恢复原任务确认</Button>} />
        ) : null}
      {read.writePreview != null ? (
        <Alert type="warning" showIcon title="确认执行操作吗？即将执行工单报修流程"
          description={<FillConfirmationFields fields={writePreviewFields(read)} />}
          action={<Space>
            <Button disabled={deciding} onClick={() => { void onDecide('rejected'); }}>拒绝</Button>
            <Button type="primary" loading={deciding} onClick={() => { void onDecide('confirmed'); }}>确认填写</Button>
          </Space>} />
      ) : null}
      <BrowserRunCard
        parsedView={read.run}
        taskId={target.accepted.task_id}
        runId={target.accepted.run_id}
        requestGeneration={target.requestGeneration}
        currentGeneration={target.requestGeneration}
        resultValue={read.fillStop == null ? read.value : null}
        statusLabel={fillStopHistory ? FILL_STOP_HISTORY_STATUS_LABEL : undefined}
        handoffEnded={handoffEnded}
        // A sealed run ends through the panel's "结束协助" (same endpoint), not the card button.
        onCancel={handoff === null && !(panelShown && read.run.status === 'running') ? onCancel : undefined}
      />
      {panelShown ? (
        // Sealed or pre-seal D card (§9.1); the panel decides what a null handoff shows.
        <BrowserHandoffPanel run={read.run} handoff={handoff}
          screen={{ taskId: target.accepted.task_id, runId: target.accepted.run_id,
            sessionId: target.conversationId }}
          onEndAssist={() => onCancel(target.accepted.task_id, target.accepted.run_id)} />
      ) : null}
      {panelCopy ? null : fillStopHistory ? <BrowserFillStopHistoryNotice /> : read.fillStop != null ? (
        <Alert showIcon type={read.run.status === 'completed' && read.run.result?.verification === 'verified'
          && read.fillStop.verification === 'verified' ? 'success' : 'warning'}
          title={read.run.status === 'completed' && read.run.result?.verification === 'verified' && read.fillStop.verification === 'verified'
            ? '三项已填写并核验，已停止' : '填写任务状态已回传'}
          description={`类别：${read.fillStop.field_checks.category === true ? '匹配' : '未核验通过'}；`
            + `位置：${read.fillStop.field_checks.position === true ? '匹配' : '未核验通过'}；`
            + `描述：${read.fillStop.field_checks.description === true ? '匹配' : '未核验通过'}。`
            + `耗时 ${read.fillStop.seconds.toFixed(3)} 秒；状态码 ${read.fillStop.error_code}。`
            + (read.fillStop.cleanup_confirmed ? '' : ' 执行连接清理仍待核验。')} />
      ) : null}
      {pollError ? (
        <Alert type="warning" showIcon title="浏览器任务更新暂不可用"
          description={`HTTP：${readFailure.status ?? '未取得'}；错误码：${readFailure.code}`}
          action={<Button onClick={() => setRefresh((value) => value + 1)}>重试读取</Button>} />
      ) : null}
    </div>
  );
}

const presentationLabels: Record<PresentationKind, string> = {
  unconfirmed: '提交待确认',
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

function unconfirmedSubmission(): ProjectedResponse {
  return {
    role: 'assistant', text: '提交结果尚未确认，可使用原请求继续检查。',
    presentationKind: 'unconfirmed', responseId: null, confirm: null, records: null, actionOutcome: null,
  };
}

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

const COLLECTION_UPDATED_NOTICE = '原追问已更新，请按最新的追问补充信息。';

/** 快捷指令：没有对话时排在输入框上方，点一下把这句话填进输入框。副说明是悬停提示。 */

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

/** The three values the confirmation fills, under the D manifest's business names. */
function FillConfirmationFields({ fields }: { fields: BrowserFillFields | null }) {
  if (fields === null) return <Text type="secondary">暂时无法显示填写内容。</Text>;
  return (
    <dl className={styles.fillFields}>
      {(['category', 'position', 'description'] as const).map((key) => (
        <div key={key}>
          <dt>{fieldLabel(key)}</dt>
          <dd>{fields[key]}</dd>
        </div>
      ))}
    </dl>
  );
}

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
        {entry.dFormCollection !== undefined
          ? '请按上方提示补充缺少的信息。'
          : '请将原请求与明确范围一起完整重述为一条新请求。'}
      </Text>
    );
  }
  if (entry.presentationKind === 'binding' && entry.targetSystem) {
    return (
      <>
        <Text className={styles.guidance}>
          目标系统：{targetSystemLabels[entry.targetSystem]}
        </Text>
        {entry.mcpAuthorizationPath && <a href={entry.mcpAuthorizationPath}>去授权</a>}
      </>
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
      {entry.dFormCategoryOptions?.length ? (
        <Text type="secondary">可选报修类别：{entry.dFormCategoryOptions.join('、')}</Text>
      ) : null}
    </>
  );
}

export default function ChatPage({ browserSkillId }: ChatPageProps) {
  const draft = useAIDockStore((state) => state.draft);
  const transcript = useAIDockStore((state) => state.transcript);
  const draftAttachments = useAIDockStore((state) => state.draftAttachments);
  const deliveryAttachments = useMemo(() => [...new Map([
    ...transcript.flatMap((entry) => entry.role === 'user' ? entry.attachments ?? [] : []),
    ...draftAttachments,
  ].map((attachment) => [attachment.artifact_id, attachment])).values()], [transcript, draftAttachments]);
  const currentSessionId = useAIDockStore((state) => state.sessionId);
  const appendTranscript = useAIDockStore((state) => state.appendTranscript);
  const setDraft = useAIDockStore((state) => state.setDraft);
  const startNewSession = useAIDockStore((state) => state.startNewSession);
  const confirmationResults = useAIDockStore((state) => state.confirmationResults);
  const confirmationNotice = (responseId: string | null) => {
    const outcome = responseId === null ? undefined : confirmationResults[responseId];
    return outcome === undefined ? null : userActionOutcomeMessages[outcome];
  };
  const identityQuery = useIdentityQuery();
  const { data: me, refetch: refetchMe } = identityQuery;
  const authStatus = useAuthStore((state) => state.status);
  const authGeneration = useAuthStore((state) => state.generation);
  const browserOwner = useMemo(() => parseBrowserOwnerScope(me), [me]);
  const meSkillId = useMemo(() => parseBrowserSkillId(me), [me]);
  const activeSkillId = meSkillId !== null
    && (browserSkillId === undefined || browserSkillId === meSkillId) ? meSkillId : null;
  const browserAvailable = authStatus === 'authenticated'
    && browserOwner !== null && activeSkillId !== null;
  const requestMetadataReady = authStatus === 'authenticated' && identityQuery.isSuccess
    && me?.authenticated === true && (me.browser_skill_id == null || browserAvailable);
  const [browserOptIn, setBrowserOptIn] = useState(false);
  const chatAttachments = useChatAttachments();
  const [attachmentsToggled, setAttachmentsToggled] = useState(false);
  // 对话中右侧的案头可以收起；「历史对话」是输入框底栏打开的小面板。
  const [deskSideOpen, setDeskSideOpen] = useState(true);
  const [historyOpen, setHistoryOpen] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // 案头上的数字、卡片和快捷指令都只做一件事：把一句现成的话填进输入框，由用户自己发出去。
  const askFromDesk = useCallback((question: string) => {
    setDraft(question);
    inputRef.current?.focus();
  }, [setDraft]);
  const [browserTarget, setBrowserTarget] = useState<BrowserTarget | null>(null);
  const [browserTerminal, setBrowserTerminal] = useState(false);
  // Sealed handoff (§8.4): releases ordinary chat only; reset wherever browserTerminal resets.
  const [browserHandoff, setBrowserHandoff] = useState(false);
  const browserHandoffRef = useRef(false);
  browserHandoffRef.current = browserHandoff;
  const browserTargetRef = useRef<BrowserTarget | null>(null);
  browserTargetRef.current = browserTarget;
  const [browserRecoveryRevision, setBrowserRecoveryRevision] = useState(0);
  const [browserStorageError, setBrowserStorageError] = useState(false);
  const [browserIdentityReady, setBrowserIdentityReady] = useState(true);
  const [pendingSubmission, setPendingSubmission] = useState<ChatSubmission | null>(null);
  const [retrySubmission, setRetrySubmission] = useState<ChatSubmission | null>(null);
  // Same-tab refresh recovery (report job ids, unconfirmed sheet fill submissions) is read only
  // under the account scope proven by /me of the current login; another scope is cleared.
  const recoveryScope = authStatus === 'authenticated' && identityQuery.isSuccess
    ? recoveryScopeOf(me) : null;
  const recoveryScopeRef = useRef(recoveryScope);
  recoveryScopeRef.current = recoveryScope;
  const [reportJobIds, setReportJobIds] = useState<string[]>([]);
  // Confirmed sheet fill plans whose acceptance was never seen in this tab (shown with a retry).
  const [pendingSheetFills, setPendingSheetFills] = useState<PendingSheetFill[]>([]);
  useEffect(() => {
    setReportJobIds(recoveryScope === null ? [] : loadReportJobs(recoveryScope));
    setPendingSheetFills(recoveryScope === null ? [] : loadPendingSheetFills(recoveryScope));
  }, [recoveryScope, authGeneration]);
  const transcriptSheetFills = useMemo(() => new Set(transcript.flatMap((entry) =>
    entry.role === 'assistant' && entry.sheetFillPlan !== undefined && entry.responseId !== null
      ? [entry.responseId] : [])),
  [transcript]);
  // Accepted sheet fill plans keep their job on the transcript entry (reportJobId).
  const transcriptReportJobs = useMemo(() => new Set(transcript.flatMap((entry) =>
    entry.role === 'assistant' && entry.reportJobId !== undefined ? [entry.reportJobId] : [])),
  [transcript]);
  const settleSheetFill = useAIDockStore((state) => state.settleSheetFill);
  const acceptSheetFill = (responseId: string, jobId: string) => {
    settleSheetFill(responseId, { jobId });
    const scope = recoveryScopeRef.current;
    setReportJobIds(scope === null ? [] : loadReportJobs(scope));
    setPendingSheetFills(scope === null ? [] : loadPendingSheetFills(scope));
  };
  // Kept in the dock store so a same-tab re-login or reload restores it with the conversation.
  const collection = useAIDockStore((state) => state.collection);
  const setCollection = useAIDockStore((state) => state.setCollection);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const browserGeneration = useRef(0);
  const restoredScope = useRef<string | null>(null);
  const ownerKey = browserOwner === null ? null
    : `${browserOwner.tenant_id}/${browserOwner.user_id}`;
  const ownerKeyRef = useRef<string | null>(ownerKey);
  ownerKeyRef.current = ownerKey;
  const currentCollection = collection !== null && collection.sessionId === currentSessionId
    && collection.authGeneration === authGeneration && collection.ownerKey === ownerKey
    && authStatus === 'authenticated' ? collection.reference : null;
  const skillIdRef = useRef<string | null>(meSkillId);
  skillIdRef.current = meSkillId;
  const identityCheck = useRef(0);
  const requestInFlight = useRef(false);
  const activeSubmission = useRef<ChatSubmission | null>(null);
  // Only a first send may be withdrawn on 401; a retry follows an unconfirmed outcome.
  const freshSubmissions = useRef(new WeakSet<ChatSubmission>());
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
        setBrowserHandoff(false);
        setPendingSubmission(null);
        setRetrySubmission(null);
        setCollection(null);
        setBrowserOptIn(false);
      }
      setBrowserIdentityReady(true);
    }).catch(() => { /* Keep the previous browser result hidden until retry. */ });
  }, [refetchMe, setCollection]);

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
      originalSubmission: null,
    });
    setBrowserTerminal(false);
    setBrowserHandoff(false);
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
      setBrowserHandoff(false);
      setBrowserOptIn(false);
      setPendingSubmission(null);
      setRetrySubmission(null);
    }
  }, [browserTarget, browserAvailable, browserOwner, authGeneration, currentSessionId]);

  useEffect(() => {
    const stillCurrent = (submission: ChatSubmission): boolean =>
      authStatus === 'authenticated' && submission.authGeneration === authGeneration
      && submission.sessionId === currentSessionId
      && (submission.owner === null || ownerKey ===
        `${submission.owner.tenant_id}/${submission.owner.user_id}`);
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
      && (submission.owner === null || ownerKeyRef.current ===
        `${submission.owner.tenant_id}/${submission.owner.user_id}`);
  };

  const sendBrowserSubmission = async (submission: ChatSubmission): Promise<unknown> => {
    const browser = submission.browser;
    const owner = submission.owner;
    if (owner === null) throw new Error('browser_submission_invalid');
    const controller = new AbortController();
    browserSubmitContext.current = {
      controller, ownerKey: `${owner.tenant_id}/${owner.user_id}`,
    };
    const stopOnSessionChange = useAIDockStore.subscribe((state) => {
      if (state.sessionId !== submission.sessionId) controller.abort();
    });
    const stopOnAuthChange = useAuthStore.subscribe((state) => {
      if (state.status !== 'authenticated' || state.generation !== submission.authGeneration) {
        controller.abort();
      }
    });
    const deadlineTimer = setTimeout(() => { controller.abort(); }, 135_000);
    try {
      return browser === null
        ? await submitOrdinaryBrowserCapableMessage(
          submission.message, submission.sessionId, submission.requestId, controller.signal,
          submission.collection, submission.attachments.map((item) => item.artifact_id),
        )
        : await submitBrowserMessage(
          submission.message, submission.sessionId, browser.skillId, browser.requestId,
          controller.signal,
        );
    } finally {
      clearTimeout(deadlineTimer);
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
      // Consumed here: checking the original request reuses this object after an outcome that
      // may already have been accepted, and that attempt must never withdraw the message.
      const firstAttempt = freshSubmissions.current.delete(submission);
      const reference = /^(?:确认|confirm)\s+(\S+)$/i.exec(message)?.[1] ?? null;
      try {
        const response = submission.owner === null
          ? await handleApiV1RuntimeHandlePost({
            channel: 'web', session_id: sessionId, message, client_request_id: submission.requestId,
            client_capabilities: {},
            ...(submission.collection === null ? {} : { d_form_collection: submission.collection }),
            ...(submission.attachments.length === 0 ? {} : {
              attachment_ids: submission.attachments.map((item) => item.artifact_id),
            }),
          })
          : await sendBrowserSubmission(submission);
        const boundSessionId = isRecord(response) && typeof response.session_id === 'string'
          ? response.session_id : '';
        if (submission.boundSessionId !== null && boundSessionId !== submission.boundSessionId) {
          throw new Error('collection_session_mismatch');
        }
        const pending = submission.owner === null ? null : parseBrowserPending(response);
        if (pending !== null) {
          if (submissionIsCurrent(submission)) {
            setPendingSubmission(submission);
            setRetrySubmission(null);
          }
          return null;
        }
        if (submission.owner !== null) {
          const accepted = parseBrowserAccepted(response);
          if (accepted !== null) {
            if (submissionIsCurrent(submission)) {
              const current = browserTargetRef.current;
              if (browserHandoffRef.current && current !== null
                  && current.originalSubmission !== submission
                  && (accepted.task_id !== current.accepted.task_id
                    || accepted.run_id !== current.accepted.run_id)) {
                // §8.4 guard-miss defense: keep the sealed run's card; show a generic failure.
                setPendingSubmission(null);
                setRetrySubmission(null);
                const failure = incompatibleResponse();
                useAIDockStore.getState().applyConfirmationResult(sessionId, reference, failure);
                return failure;
              }
              setCollection(null);
              if (browserTarget?.originalSubmission === submission) {
                if (accepted.task_id !== browserTarget.accepted.task_id
                    || accepted.run_id !== browserTarget.accepted.run_id) {
                  throw new Error('browser_recovery_target_mismatch');
                }
                // Keep the same target, monitor key, generation and terminal state.
                setBrowserRecoveryRevision((value) => value + 1);
              } else {
                setBrowserTarget({
                  accepted, conversationId: sessionId, owner: submission.owner,
                  authGeneration: submission.authGeneration,
                  requestGeneration: ++browserGeneration.current,
                  originalSubmission: activeSkillId === 'd_form_fill_stop' ? submission : null,
                });
                setBrowserTerminal(false);
                setBrowserHandoff(false);
                setBrowserStorageError(!saveBrowserAccepted(
                  submission.owner, sessionId, accepted,
                ));
              }
              setPendingSubmission(null);
              setRetrySubmission(null);
            }
            return null;
          }
        }
        const uncertain = isBrowserUnconfirmed(response, submission.requestId);
        const result = uncertain ? unconfirmedSubmission() : projectResponse(response);
        if (result.dFormCollection !== undefined
            && !/^sid_v1\.[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{43}$/.test(boundSessionId)) {
          throw new Error('collection_session_mismatch');
        }
        if (submission.owner !== null && submissionIsCurrent(submission)) {
          setPendingSubmission(null);
          setRetrySubmission(uncertain ? submission : null);
        }
        if (!submissionIsCurrent(submission)) return null;
        if (!uncertain && result.presentationKind !== 'unconfirmed') {
          setCollection(result.dFormCollection === undefined ? null : {
            reference: result.dFormCollection, sessionId,
            serverSessionId: boundSessionId,
            authGeneration: submission.authGeneration, ownerKey: ownerKeyRef.current,
          });
        } else {
          setRetrySubmission(submission);
        }
        useAIDockStore.getState().applyConfirmationResult(sessionId, reference, result);
        if (result.reportJobId !== undefined) {
          const scope = recoveryScopeRef.current;
          rememberReportJob(scope, result.reportJobId);
          if (scope !== null) setReportJobIds(loadReportJobs(scope));
        }
        return result;
      } catch (error) {
        if (isChatAttachmentRejection(error)) {
          // Refused before any work: the text goes back; the attachments are what was refused.
          if (submission.owner !== null && submissionIsCurrent(submission)) {
            setPendingSubmission(null);
            setRetrySubmission(null);
          }
          if (!submissionIsCurrent(submission)) return null;
          const store = useAIDockStore.getState();
          store.setDraft(store.draft.length > 0 ? [message, store.draft].join('\n') : message);
          const notice = projectTextResponse(chatUploadMessage(error), 'validation');
          store.applyConfirmationResult(sessionId, reference, notice);
          return notice;
        }
        if (isRuntimeSessionBusy(error)) {
          // Refused before any work: nothing was sent, so the input goes back to the box.
          if (submission.owner !== null && submissionIsCurrent(submission)) {
            setPendingSubmission(null);
            setRetrySubmission(null);
          }
          if (!submissionIsCurrent(submission)) return null;
          const store = useAIDockStore.getState();
          store.setDraft(store.draft.length > 0 ? [message, store.draft].join('\n') : message);
          if (submission.attachments.length > 0) {
            store.setDraftAttachments(
              [...submission.attachments, ...store.draftAttachments].slice(0, CHAT_ATTACHMENT_LIMIT),
            );
          }
          const notice = projectTextResponse(SESSION_BUSY_NOTICE, 'request_error');
          store.applyConfirmationResult(sessionId, reference, notice);
          return notice;
        }
        if (submission.collection !== null && error instanceof ApiError
            && (error.status === 404 || error.status === 409)) {
          // The backend refused the reference itself (gone/expired or a newer version):
          // stop continuing it; never resend the reply as a new request.
          if (submission.owner !== null && submissionIsCurrent(submission)) {
            setPendingSubmission(null);
            setRetrySubmission(null);
          }
          if (!submissionIsCurrent(submission)) return null;
          setCollection(null);
          const notice = projectTextResponse(
            error.status === 404 ? COLLECTION_LOST_NOTICE : COLLECTION_UPDATED_NOTICE, 'session',
          );
          useAIDockStore.getState().applyConfirmationResult(sessionId, reference, notice);
          return notice;
        }
        const projectedError = projectRequestError(error);
        // Ordinary runtime handling authenticates before any work, and the browser request shell
        // turns later browser failures into an unconfirmed reply rather than an HTTP 401, so a 401
        // on the first attempt means this send never happened. It only returns to the input and
        // nothing is resent. Explicit browser-skill sends stay unconfirmed.
        if (projectedError === null && submission.browser === null
            && error instanceof ApiError && error.status === 401 && firstAttempt) {
          returnUnsentMessage({
            generation: submission.authGeneration, sessionId, requestId: submission.requestId,
          }, message, submission.attachments);
        }
        if (projectedError === null && submission.owner === null) throw error;
        const result = submission.owner !== null ? unconfirmedSubmission()
          : projectedError ?? projectResponse(null);
        if (submission.owner !== null && submissionIsCurrent(submission)) {
          setPendingSubmission(null);
          setRetrySubmission(submission);
        }
        if (!submissionIsCurrent(submission)) return null;
        useAIDockStore.getState().applyConfirmationResult(sessionId, reference, result);
        return result;
      }
    },
    onSettled: () => {
      activeSubmission.current = null;
      requestInFlight.current = false;
    },
  });

  // 新内容到达时滚到对话区底部；用户已向上翻看（离底部超过阈值）则不拉回。没有对话时这一格放的是案头，不滚。
  useEffect(() => {
    const element = transcriptRef.current;
    const hasThread = transcript.length > 0 || mutation.isPending || browserTarget !== null
      || pendingSubmission !== null || retrySubmission !== null;
    if (element !== null && hasThread && stickToBottom.current) element.scrollTop = element.scrollHeight;
  }, [transcript, mutation.isPending, browserTarget, pendingSubmission, retrySubmission]);

  const submit = (retry?: ChatSubmission) => {
    const message = retry?.message ?? draft.trim();
    if (!message || requestInFlight.current) {
      return;
    }
    const currentAuth = useAuthStore.getState();
    if (!requestMetadataReady || currentAuth.status !== 'authenticated'
        || currentAuth.generation !== authGeneration || browserAvailable && !browserIdentityReady) return;
    if (retry !== undefined && !submissionIsCurrent(retry)) return;
    if (retry?.browser !== null && retry?.browser !== undefined && !browserIdentityReady) return;
    if (retry === undefined && browserOptIn && !browserAvailable) return;
    if (retry === undefined && browserOptIn && !browserIdentityReady) return;
    if (activeSkillId === 'd_form_fill_stop' && browserTarget !== null && !browserTerminal
        && !browserHandoff
        && (retry === undefined || retry !== browserTarget.originalSubmission)) return;
    if (retry === undefined && (browserOptIn || activeSkillId === 'd_form_fill_stop')
        && ((browserTarget !== null && !browserTerminal && !browserHandoff)
          || pendingSubmission !== null)) return;
    const sessionId = retry?.sessionId ?? useAIDockStore.getState().ensureSession();
    const browser = retry?.browser ?? (currentCollection === null && browserOptIn && browserOwner !== null
      && activeSkillId !== null ? {
        skillId: activeSkillId, requestId: crypto.randomUUID(), owner: browserOwner,
      } : null);
    const submission: ChatSubmission = retry ?? {
      attachments: useAIDockStore.getState().draftAttachments,
      message, sessionId, authGeneration: useAuthStore.getState().generation, browser,
      requestId: browser?.requestId ?? crypto.randomUUID(),
      owner: browserAvailable ? browserOwner : null,
      collection: currentCollection === null ? null : { ...currentCollection },
      boundSessionId: currentCollection !== null && collection !== null
        ? collection.serverSessionId : null,
    };
    requestInFlight.current = true;
    stickToBottom.current = true;
    if (retry === undefined) {
      setRetrySubmission(null);
      freshSubmissions.current.add(submission);
      appendTranscript({
        role: 'user', text: message, requestId: submission.requestId,
        ...(submission.attachments.length === 0 ? {} : { attachments: submission.attachments }),
      });
      setDraft('');
      useAIDockStore.getState().setDraftAttachments([]);
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
    setCollection(null);
    clearBrowserAccepted();
    setBrowserTarget(null);
    setBrowserTerminal(false);
    setBrowserHandoff(false);
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
  const browserSubmissionBlocked = (browserOptIn || activeSkillId === 'd_form_fill_stop')
    && (!browserIdentityReady || (browserTarget !== null && !browserTerminal && !browserHandoff)
      || pendingSubmission !== null);

  // Browser-skill sends and collection replies cannot carry attachments (the backend answers 422).
  const attachmentsAllowed = !browserOptIn && currentCollection === null;
  const attachmentsOpen = (attachmentsToggled && attachmentsAllowed)
    || chatAttachments.attachments.length > 0 || chatAttachments.uploads.length > 0;
  const canSend = Boolean(draft.trim()) && !mutation.isPending
    && !browserSubmissionBlocked && requestMetadataReady && !chatAttachments.busy
    && (attachmentsAllowed || chatAttachments.attachments.length === 0);

  /** Enter 发送，Shift/Ctrl/Alt/Meta+Enter 换行；输入法组字（`isComposing` / `keyCode 229`）时不发送。 */
  const handleRequestKeyDown = (event: KeyboardEvent) => {
    if (event.key !== 'Enter') return;
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return;
    event.preventDefault();
    if (canSend) submit();
  };

  const lastEntry = transcript.at(-1);
  const lastEntryFailed =
    lastEntry !== undefined &&
    lastEntry.role === 'assistant' &&
    UNSUCCESSFUL_KINDS.has(lastEntry.presentationKind);

  // 线程里只要有任何要给用户看的东西（消息、进行中的任务、待恢复的作业），就是「对话中」；否则案头铺满。
  const inConversation = transcript.length > 0 || mutation.isPending
    || visibleBrowserTarget !== null || pendingSubmission !== null || retrySubmission !== null
    || reportJobIds.length > 0 || pendingSheetFills.length > 0
    || (!browserIdentityReady && browserTarget !== null);
  const composerOpen = draft.trim() !== '' || attachmentsOpen || historyOpen;

  return (
    <div
      className={styles.page}
      data-mode={inConversation ? 'chat' : 'desk'}
      data-side={deskSideOpen ? 'true' : 'false'}
    >
      {/* 所在页由左侧窄栏高亮表明，标题只留给读屏软件定位，不占版面。 */}
      <h1 className={styles.pageTitle}>AI 助手</h1>

      {inConversation ? (
        <div className={styles.pageTools}>
          <button
            aria-pressed={deskSideOpen}
            className="wb-btn wb-btn-quiet wb-btn-sm"
            onClick={() => setDeskSideOpen((open) => !open)}
            title="显示或收起右侧案头"
            type="button"
          >
            <Icon name="panel" size={17} />
            案头
          </button>
        </div>
      ) : null}

      <div
        className={styles.scroll}
        ref={transcriptRef}
        onScroll={(event) => {
          const { scrollTop, scrollHeight, clientHeight } = event.currentTarget;
          stickToBottom.current = scrollHeight - scrollTop - clientHeight < 80;
        }}
      >
        {!inConversation ? (
          <div className={styles.deskWrap}>
            <Desk onAsk={askFromDesk} />
          </div>
        ) : (
          <div className={styles.chatWrap} data-side={deskSideOpen ? 'true' : 'false'}>
            <section
              aria-busy={mutation.isPending}
              aria-label="办理会话"
              aria-live="polite"
              className={styles.thread}
            >
              {transcript.length === 0 ? null : (
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
                        {entry.role === 'assistant' ? (
                          <span aria-hidden="true" className={styles.aiMark}>
                            <Icon name="sparkle" size={15} strokeWidth={2} />
                          </span>
                        ) : null}
                        <div className={styles.messageMeta}>
                          <span className={styles.speaker}>{entry.role === 'user' ? '你' : 'EternalAI'}</span>
                          {entry.role === 'assistant' ? (
                            <span className={styles.statusLabel}>
                              {presentationLabels[entry.presentationKind]}
                            </span>
                          ) : null}
                        </div>
                        {entry.role === 'user' && entry.attachments?.length ? (
                          <ul aria-label="附件" className={styles.messageAttachments}>
                            {entry.attachments.map((attachment) => (
                              <li key={attachment.artifact_id}>
                                <a href={attachmentDownloadUrl(attachment.artifact_id)} download>
                                  {attachment.filename}
                                </a>
                              </li>
                            ))}
                          </ul>
                        ) : null}
                        <p className={styles.messageText}>{entry.text}</p>
                        {entry.role === 'assistant' && entry.reportJobId !== undefined ? (
                          <ReportJobCard jobId={entry.reportJobId} />
                        ) : null}
                        {entry.role === 'assistant' && entry.sheetFillPlan !== undefined
                          && entry.reportJobId === undefined && entry.responseId !== null ? (
                          <SheetFillPlanCard
                            key={`${authGeneration}/${entry.responseId}`}
                            plan={entry.sheetFillPlan}
                            responseId={entry.responseId}
                            sessionId={currentSessionId}
                            recoveryScope={recoveryScope}
                            cancelled={entry.sheetFillCancelled === true}
                            onAccepted={(jobId) => acceptSheetFill(entry.responseId!, jobId)}
                            onCancel={() => settleSheetFill(entry.responseId!, 'cancelled')}
                          />
                        ) : null}
                        {entry.role === 'assistant' && entry.presentationKind === 'completed'
                          && entry.reportJobId === undefined && entry.sheetFillPlan === undefined
                          && entry.text.trim() ? (
                          <DeliveryComposerButton key={`${authGeneration}/${currentSessionId}/${index}`}
                            text={entry.text} attachments={deliveryAttachments} />
                        ) : null}
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

              {reportJobIds.filter((jobId) => !transcriptReportJobs.has(jobId)).map((jobId) => (
                <ReportJobCard key={jobId} jobId={jobId} onDismiss={() => {
                  forgetReportJob(recoveryScope, jobId);
                  setReportJobIds(recoveryScope === null ? [] : loadReportJobs(recoveryScope));
                }} />
              ))}
              {pendingSheetFills.filter((item) => !transcriptSheetFills.has(item.responseId))
                .map((item) => (
                  <SheetFillPlanCard
                    key={`${authGeneration}/${item.responseId}`}
                    plan={item.plan}
                    responseId={item.responseId}
                    sessionId={item.sessionId}
                    recoveryScope={recoveryScope}
                    onAccepted={(jobId) => acceptSheetFill(item.responseId, jobId)}
                    onDismiss={() => {
                      forgetPendingSheetFill(recoveryScope, item.responseId);
                      setPendingSheetFills(recoveryScope === null ? []
                        : loadPendingSheetFills(recoveryScope));
                    }}
                  />
                ))}
              {visibleBrowserTarget === null ? null : (
                <BrowserRunMonitor
                  key={`${visibleBrowserTarget.requestGeneration}/${visibleBrowserTarget.accepted.run_id}`}
                  target={visibleBrowserTarget}
                  onTerminal={setBrowserTerminal}
                  onHandoff={setBrowserHandoff}
                  fillStopMode={activeSkillId === 'd_form_fill_stop'}
                  onRecoverOriginal={visibleBrowserTarget.originalSubmission === null ? null : () => {
                    const original = visibleBrowserTarget.originalSubmission;
                    if (original !== null) submit(original);
                  }}
                  recovering={mutation.isPending}
                  recoveryRevision={browserRecoveryRevision}
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
                <Alert type="warning" showIcon title={browserTarget?.originalSubmission === retrySubmission
                  ? '原任务恢复结果尚未确认，可重试原请求' : '提交结果尚未确认，可使用原请求继续检查'}
                  action={<Button disabled={mutation.isPending || !browserIdentityReady}
                    onClick={() => submit(retrySubmission)}>检查原请求</Button>} />
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
            </section>
            {deskSideOpen ? (
              <div className={styles.sideSlot}>
                <DeskSide onAsk={askFromDesk} />
              </div>
            ) : null}
          </div>
        )}
      </div>

      {/*
        输入框浮在页面底部正中：平时收成一行，点进去或有内容时展开。外面那圈缓慢旋转的彩色细边
        是「对 AI 说话」的统一记号。快捷指令在没有对话时排在它上方，一直看得到。
      */}
      <div className={styles.composerDock}>
        <div className={styles.composerNotices}>
          {authStatus === 'authenticated' && !requestMetadataReady ? (
            <Alert type="info" showIcon title="正在核对当前会话信息，核对完成后可发送"
              action={<Button onClick={() => { void refetchMe(); }}>重新核对</Button>} />
          ) : null}
          {browserHandoff && visibleBrowserTarget !== null ? <BrowserHandoffComposerNotice /> : null}
        </div>
        {inConversation ? null : (
          <div aria-label="可以这样问我" className={styles.chips} role="group">
            {STARTER_PROMPTS.map((prompt) => (
              <button key={prompt.key} onClick={() => askFromDesk(prompt.label)} title={prompt.description} type="button">
                {prompt.label}
              </button>
            ))}
          </div>
        )}
        {historyOpen ? (
          /*
            「我问过的」。会话持久化整体归后续阶段，这里只列本次临时会话，并如实写明历史存不起来，
            不做刷新即失效却看起来像历史的列表。
          */
          <aside aria-label="我问过的" className={styles.history}>
            <h2>我问过的</h2>
            {transcript.length === 0 ? null : <ul aria-label="当前对话"><li>本次对话</li></ul>}
            <p>以前问过的存不起来，刷新就没了。</p>
          </aside>
        ) : null}
        <form
          className={`${styles.composer} wb-ring`}
          data-focus-ring="host"
          data-open={composerOpen ? 'true' : 'false'}
          onClick={(event) => {
            if (event.target === event.currentTarget) inputRef.current?.focus();
          }}
          onSubmit={handleSubmit}
        >
          <span aria-hidden="true" className="wb-ringfx"><i /></span>
          {attachmentsOpen ? (
            <div className={styles.attachments}>
              <ChatAttachmentBar controller={chatAttachments} allowed={attachmentsAllowed} />
              {deliveryAttachments.length > 0 ? <DeliveryComposerButton
                key={`${authGeneration}/${currentSessionId}/attachments`}
                text="" attachments={deliveryAttachments} /> : null}
            </div>
          ) : null}
          <textarea
            aria-label="办理请求"
            className={styles.input}
            disabled={mutation.isPending}
            id="chat-request"
            onChange={(event) => {
              const value = event.target.value;
              setDraft(value);
              if (retrySubmission !== null && value.trim() !== retrySubmission.message) {
                setRetrySubmission(null);
              }
            }}
            onKeyDown={handleRequestKeyDown}
            placeholder="交代一件事，或者问个问题"
            ref={inputRef}
            rows={1}
            value={draft}
          />
          <div className={styles.composerBar}>
            <button
              aria-label="添加表格"
              aria-pressed={attachmentsOpen}
              className={`wb-btn wb-btn-quiet wb-btn-sm ${styles.clip}`}
              disabled={!attachmentsAllowed || mutation.isPending}
              onClick={() => { setAttachmentsToggled((open) => !open); }}
              title="添加表格"
              type="button"
            >
              <Icon name="paperclip" size={17} />
              <span>添加表格</span>
            </button>
            <button
              aria-expanded={historyOpen}
              className={`wb-btn wb-btn-quiet wb-btn-sm ${styles.barExtra}`}
              onClick={() => setHistoryOpen((open) => !open)}
              type="button"
            >
              <Icon name="history" size={17} />
              历史对话
            </button>
            {inConversation ? (
              <button
                className={`wb-btn wb-btn-quiet wb-btn-sm ${styles.barExtra}`}
                onClick={startFreshSession}
                type="button"
              >
                <Icon name="plus" size={17} />
                新对话
              </button>
            ) : null}
            {browserAvailable && browserIdentityReady && activeSkillId !== 'd_form_fill_stop' ? (
              <span className={styles.barExtra}>
                <Checkbox checked={browserOptIn} onChange={(event) => {
                  setBrowserOptIn(event.target.checked);
                }}>
                  使用浏览器只读技能
                </Checkbox>
              </span>
            ) : null}
            <span className={styles.hint}>Enter 发送</span>
            <button
              aria-label="发送"
              className={`wb-btn wb-btn-primary ${styles.send}`}
              data-loading={mutation.isPending ? 'true' : 'false'}
              disabled={!canSend}
              title="发送"
              type="submit"
            >
              <Icon name={mutation.isPending ? 'retry' : 'up'} size={18} strokeWidth={2} />
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
