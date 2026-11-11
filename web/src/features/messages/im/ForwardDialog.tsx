import { useEffect, useMemo, useState } from 'react';
import { Alert, App as AntApp, Checkbox, Input, Modal } from 'antd';
import { ApiError } from '../../../api/mutator';
import { MERGED_TITLE, mergedForward, messageLength, singleForward } from '../zulip/forward';
import type { ForwardSource } from '../zulip/forward';
import { UnconfirmedResult, readRawMessages, sendMessage, useImStore } from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';
import { Avatar } from './Avatar';
import { conversationAvatar, conversationRows, conversationTitle, separatorTime, userName } from './viewModel';
import styles from './ForwardDialog.module.css';

export type ForwardMode = 'single' | 'merged';

const TARGET_LIMIT = 20;

/**
 * 转发对话框（FE_PLAN §11）：先取所选消息的原文、拼好要发的正文并按服务端长度上限预检，选目标后由本人确认
 * 才发送。按目标、按条依次发送；任何一条失败或结果不明确就停下并说明已发出多少，不自动重发。
 */
export function ForwardDialog({ conversationKey, messageIds, mode, onClose, onSent }: {
  conversationKey: ConversationKey;
  messageIds: number[];
  mode: ForwardMode;
  onClose: () => void;
  onSent: () => void;
}) {
  const { message: toast } = AntApp.useApp();
  const state = useImStore();
  const [contents, setContents] = useState<string[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [targets, setTargets] = useState<ConversationKey[]>([]);
  const [query, setQuery] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    readRawMessages(conversationKey, messageIds).then((messages) => {
      if (!live) return;
      const current = useImStore.getState();
      const sources: ForwardSource[] = messages.map((message) => ({
        senderName: userName(current, message.sender_id), time: separatorTime(message.timestamp), raw: message.content,
      }));
      const conversation = current.conversations[conversationKey];
      const title = conversation ? conversationTitle(current, conversation) : '';
      setContents(mode === 'merged' ? [mergedForward(title, sources)] : sources.map(singleForward));
    }, (error: unknown) => {
      if (live) setLoadError(error instanceof ApiError ? error.message : '读取原消息失败，请重试。');
    });
    return () => {
      live = false;
    };
  }, [conversationKey, messageIds, mode]);

  const tooLong = contents?.some((content) => messageLength(content) > state.maxMessageLength) ?? false;
  const rows = useMemo(() => conversationRows(state), [state]);
  const visible = rows.filter((row) => !query.trim() || row.title.toLowerCase().includes(query.trim().toLowerCase()));

  const toggleTarget = (key: ConversationKey) => {
    setTargets((current) => (current.includes(key)
      ? current.filter((item) => item !== key)
      : current.length < TARGET_LIMIT ? [...current, key] : current));
  };

  const send = async () => {
    if (!contents) return;
    setSending(true);
    setSendError(null);
    let sent = 0;
    const total = contents.length * targets.length;
    try {
      for (const target of targets) {
        for (const content of contents) {
          await sendMessage(target, content);
          sent += 1;
        }
      }
      void toast.success(`已转发到 ${targets.length} 个会话`);
      onSent();
    } catch (error) {
      const progress = `已发出 ${sent} / ${total} 条`;
      setSendError(error instanceof UnconfirmedResult
        ? `${progress}，下一条结果待确认，请查看目标会话后再决定是否重试。`
        : `${progress}，其余没有发出${error instanceof ApiError && error.message ? `：${error.message}` : '。'}`);
    } finally {
      setSending(false);
    }
  };

  const modeText = mode === 'merged' ? `合并为一条「${MERGED_TITLE}」` : `逐条转发 ${messageIds.length} 条`;
  return (
    <Modal
      confirmLoading={sending}
      okButtonProps={{ disabled: !contents || tooLong || targets.length === 0 || sendError !== null }}
      okText={targets.length > 0 ? `发送到 ${targets.length} 个会话` : '发送'}
      onCancel={sending ? undefined : onClose}
      onOk={() => void send()}
      open
      title={mode === 'merged' ? '合并转发' : '逐条转发'}
    >
      <p className={styles.summary}>
        {messageIds.length} 条消息，{modeText}。附件按收件会话的权限可见；原消息里的 @ 不会再次提醒对方。
      </p>
      {loadError ? <Alert showIcon title={loadError} type="error" /> : null}
      {!contents && !loadError ? <p className={styles.summary} role="status">正在读取原消息…</p> : null}
      {tooLong ? (
        <Alert
          showIcon
          title={`内容超过单条消息上限 ${state.maxMessageLength} 字，请减少所选条数${mode === 'merged' ? '或改用逐条转发' : ''}。`}
          type="warning"
        />
      ) : null}
      {sendError ? <Alert showIcon title={sendError} type="error" /> : null}
      <Input
        allowClear
        aria-label="搜索要转发到的会话"
        className={styles.search}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="搜索联系人或群"
        value={query}
      />
      <ul aria-label="选择转发目标" className={styles.targets}>
        {visible.map((row) => {
          const conversation = state.conversations[row.key];
          if (!conversation) return null;
          return (
            <li key={row.key}>
              <Checkbox checked={targets.includes(row.key)} disabled={sending} onChange={() => toggleTarget(row.key)}>
                <span className={styles.target}>
                  <Avatar avatar={conversationAvatar(state, conversation)} size={28} />
                  {row.title}
                </span>
              </Checkbox>
            </li>
          );
        })}
      </ul>
    </Modal>
  );
}
