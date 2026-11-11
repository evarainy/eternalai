import { useEffect, useMemo, useState } from 'react';
import { Alert, App as AntApp, Checkbox, Input, Modal } from 'antd';
import { ApiError } from '../../../api/mutator';
import { MERGED_TITLE, mergedForward, messageLength, singleForward } from '../zulip/forward';
import type { ForwardSource } from '../zulip/forward';
import { UnconfirmedResult, ensureDirect, readRawMessages, sendMessage, useImStore } from '../zulip/imStore';
import { dmConversation } from '../zulip/narrow';
import type { ConversationKey } from '../zulip/narrow';
import { Avatar } from './Avatar';
import {
  absoluteTime, conversationAvatar, conversationRows, conversationTitle, personAvatar, userName,
} from './viewModel';
import type { ConversationAvatar } from './viewModel';
import styles from './ForwardDialog.module.css';

export type ForwardMode = 'single' | 'merged';

/** 转发目标：已有会话，或还没有单聊的联系人（选中发送时才在列表里建一行）。 */
interface Target {
  key: ConversationKey;
  title: string;
  avatar: ConversationAvatar;
  userId: number | null;
}

/** 确认框里每条的摘要：纯文字，附件只列文件名。 */
function digest(raw: string): { text: string; files: string[] } {
  const files = [...raw.matchAll(/\[([^\]]+)\]\(\/user_uploads\/[^)]+\)/g)].map((match) => match[1]!);
  const text = raw.replace(/\[([^\]]+)\]\(\/user_uploads\/[^)]+\)/g, '').replace(/\s+/g, ' ').trim();
  return { text: text.length > 60 ? `${text.slice(0, 60)}…` : text, files };
}

/**
 * 转发对话框（FE_PLAN §11）：取所选消息的原文，拼好要发的正文，按服务端长度上限预检，并显示将发出的内容
 * 摘要；选目标（会话或联系人）后由本人确认才发送。所选消息变化时重新读取，旧正文立即作废。按目标、按条
 * 依次发送；任何一条失败或结果不明确就停下并说明已发出多少，不自动重发。
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
  const [prepared, setPrepared] = useState<{ ids: string; sources: ForwardSource[]; contents: string[] } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [targets, setTargets] = useState<Target[]>([]);
  const [query, setQuery] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const idsKey = messageIds.join(',');

  useEffect(() => {
    let live = true;
    setPrepared(null);
    setLoadError(null);
    if (!idsKey) return () => undefined;
    const ids = idsKey.split(',').map(Number);
    readRawMessages(conversationKey, ids).then((messages) => {
      if (!live) return;
      const current = useImStore.getState();
      const sources: ForwardSource[] = messages.map((message) => ({
        senderName: userName(current, message.sender_id), time: absoluteTime(message.timestamp), raw: message.content,
      }));
      const conversation = current.conversations[conversationKey];
      const title = conversation ? conversationTitle(current, conversation) : '';
      setPrepared({
        ids: idsKey, sources, contents: mode === 'merged' ? [mergedForward(title, sources)] : sources.map(singleForward),
      });
    }, (error: unknown) => {
      if (live) setLoadError(error instanceof ApiError ? error.message : '读取原消息失败，请重试。');
    });
    return () => {
      live = false;
    };
  }, [conversationKey, idsKey, mode]);

  // 只用与当前所选完全一致的那份正文。
  const contents = prepared?.ids === idsKey ? prepared.contents : null;
  const tooLong = contents?.some((content) => messageLength(content) > state.maxMessageLength) ?? false;

  const options = useMemo<Target[]>(() => {
    const keyword = query.trim().toLowerCase();
    const rows = conversationRows(state)
      .filter((row) => !keyword || row.title.toLowerCase().includes(keyword))
      .map((row) => ({
        key: row.key, title: row.title, userId: null,
        avatar: conversationAvatar(state, state.conversations[row.key]!),
      }));
    if (!keyword) return rows;
    const known = new Set(rows.map((row) => row.key));
    const contacts = Object.values(state.users)
      .filter((user) => user.is_active && !user.is_bot && user.user_id !== state.selfId
        && user.full_name.toLowerCase().includes(keyword))
      .map((user) => ({
        key: dmConversation([user.user_id], state.selfId).key, title: user.full_name, userId: user.user_id,
        avatar: personAvatar(state, user.user_id),
      }))
      .filter((contact) => !known.has(contact.key) && !state.conversations[contact.key]);
    return [...rows, ...contacts];
  }, [state, query]);

  const toggleTarget = (target: Target) => {
    setTargets((current) => (current.some((item) => item.key === target.key)
      ? current.filter((item) => item.key !== target.key)
      : [...current, target]));
  };

  const send = async () => {
    if (!contents) return;
    setSending(true);
    setSendError(null);
    let sent = 0;
    const total = contents.length * targets.length;
    try {
      for (const target of targets) {
        const key = target.userId === null ? target.key : ensureDirect([target.userId]);
        for (const content of contents) {
          await sendMessage(key, content);
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
      okButtonProps={{ disabled: !contents || tooLong || loadError !== null || targets.length === 0 || sendError !== null }}
      okText={targets.length > 0 ? `发送到 ${targets.length} 个会话` : '发送'}
      onCancel={sending ? undefined : onClose}
      onOk={() => void send()}
      open
      title={mode === 'merged' ? '合并转发' : '逐条转发'}
    >
      <p className={styles.summary}>
        {messageIds.length} 条消息，{modeText}。附件按收件会话的权限可见；引用里的 @ 不会再次提醒对方。
      </p>
      {loadError ? <Alert showIcon title={loadError} type="error" /> : null}
      {!contents && !loadError ? <p className={styles.summary} role="status">正在读取原消息…</p> : null}
      {contents && prepared ? (
        <ol aria-label="将要发出的内容" className={styles.preview}>
          {prepared.sources.map((source, index) => {
            const { text, files } = digest(source.raw);
            return (
              <li key={index}>
                <b>{source.senderName}</b> <span>{source.time}</span>
                {text ? <p>{text}</p> : null}
                {files.length > 0 ? <p className={styles.files}>附件：{files.join('、')}</p> : null}
              </li>
            );
          })}
        </ol>
      ) : null}
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
        aria-label="搜索要转发到的会话或联系人"
        className={styles.search}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="搜索联系人或群"
        value={query}
      />
      <ul aria-label="选择转发目标" className={styles.targets}>
        {options.map((target) => (
          <li key={target.key}>
            <Checkbox
              checked={targets.some((item) => item.key === target.key)}
              disabled={sending}
              onChange={() => toggleTarget(target)}
            >
              <span className={styles.target}>
                <Avatar avatar={target.avatar} size={28} />
                {target.title}
              </span>
            </Checkbox>
          </li>
        ))}
      </ul>
    </Modal>
  );
}
