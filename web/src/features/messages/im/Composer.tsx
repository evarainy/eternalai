import { useEffect, useMemo, useRef, useState } from 'react';
import type { ClipboardEvent, DragEvent, KeyboardEvent, PointerEvent } from 'react';
import { Dropdown, Popover } from 'antd';
import { ApiError } from '../../../api/mutator';
import { Icon } from '../../../shared/ui/Icon';
import { COMPOSER_HEIGHT, clampComposerHeight, readImPrefs, writeImPrefs } from '../zulip/imPrefs';
import { currentDraftSlot, draftBusy, requestDraftSave, switchDraftSlot } from '../zulip/draftSync';
import type { DraftSlot } from '../zulip/draftSync';
import {
  UnconfirmedResult, conversationDrafts, editMessage, onImReset, readRawContent, sendMessage, uploadAttachment, useImStore,
} from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';
import { useComposerBridge } from './composerStore';
import { COMMON_EMOJI, emojiChar } from './emoji';
import { userName } from './viewModel';
import styles from './Composer.module.css';

/** 未发出的文字在本页内存里按会话保留；跨刷新只恢复服务端已确认保存的草稿（FE_PLAN §5.2）。 */
const unsent = new Map<ConversationKey, string>();
onImReset(() => unsent.clear());

const DRAFT_IDLE_MS = 1500;

const FORMATS = [
  { key: 'bold', label: '粗体', before: '**', after: '**', placeholder: '文字' },
  { key: 'quote', label: '引用', before: '> ', after: '', placeholder: '引用内容' },
  { key: 'code', label: '行内代码', before: '`', after: '`', placeholder: '代码' },
  { key: 'list', label: '列表', before: '- ', after: '', placeholder: '列表项' },
] as const;

interface Attachment {
  id: number;
  file: File;
  preview: string | null;
  progress: number;
}

type DraftStatus = 'saving' | 'saved' | 'unsaved' | null;
const DRAFT_LABEL: Record<Exclude<DraftStatus, null>, string> = { saving: '保存中', saved: '已保存', unsaved: '未保存' };

let nextAttachment = 1;

function failureText(error: unknown, action: string): string {
  if (error instanceof ApiError && error.code === 'im_mapping_required') return '你的消息账号还没有绑定，暂时不能发送。';
  if (error instanceof UnconfirmedResult) return `${action}结果待确认，请查看消息记录后再决定是否重试。`;
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
    return `${action}没有成功：${error.message || '请检查内容后重试'}`;
  }
  return `${action}没有成功，请稍后重试。`;
}

/** 输入框中光标前正在输入的 @ 词。 */
function mentionQuery(text: string, caret: number): { start: number; query: string } | null {
  const match = /(^|\s)@([^\s@*]{0,20})$/.exec(text.slice(0, caret));
  return match ? { start: caret - match[2]!.length - 1, query: match[2]! } : null;
}

/**
 * 输入框（FE_PLAN §3）：Enter 发送、Shift+Enter 换行（按用户设置 `enter_sends`），输入法组字时按 Enter
 * 不发送；@ 候选、表情、格式片段、附件托盘（粘贴截图、拖入文件、预览、移除，点发送时才上传）；
 * 草稿按停顿 1.5 秒、切换会话、失焦保存并显示状态；↑ 编辑上一条。顶边可拖动调高并记住高度。
 */
export function Composer({ conversationKey, enterSends, placeholder, topic }: {
  conversationKey: ConversationKey;
  enterSends: boolean;
  placeholder: string;
  /** 多话题群：发送到的话题，必须填写；其余会话不传。 */
  topic?: string;
}) {
  const state = useImStore();
  const drafts = useMemo(() => conversationDrafts(state, conversationKey), [state, conversationKey]);
  // 当前选中的草稿位（见 draftSync）：同一会话重新挂载时接着用，在途保存不丢。
  const slot = useRef<DraftSlot>(currentDraftSlot(conversationKey));
  const [text, setText] = useState(() => unsent.get(conversationKey) ?? slot.current.saved);
  const [height, setHeight] = useState(() => readImPrefs().composerHeight);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftStatus, setDraftStatus] = useState<DraftStatus>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [mention, setMention] = useState<{ start: number; query: string; index: number } | null>(null);
  const [dropping, setDropping] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const filePicker = useRef<HTMLInputElement>(null);
  const imagePicker = useRef<HTMLInputElement>(null);
  const start = useRef<{ y: number; height: number } | null>(null);
  const latestText = useRef(text);
  const idleTimer = useRef<number | undefined>(undefined);
  const abort = useRef<AbortController | null>(null);
  const bridgeEditing = useComposerBridge((bridge) => bridge.editing);
  const editing = bridgeEditing?.key === conversationKey ? bridgeEditing : null;
  const insert = useComposerBridge((bridge) => bridge.insert);
  const beforeEdit = useRef<string | null>(null);

  const conversation = state.conversations[conversationKey];
  const members = useMemo(() => {
    if (!conversation) return [];
    const ids = conversation.kind === 'stream'
      ? state.subscriptions[conversation.streamId]?.subscribers ?? Object.keys(state.users).map(Number)
      : conversation.userIds;
    return ids.filter((id) => id !== state.selfId && state.users[id]?.is_active !== false);
  }, [conversation, state.subscriptions, state.users, state.selfId]);
  const candidates = mention
    ? members.filter((id) => userName(state, id).toLowerCase().includes(mention.query.toLowerCase())).slice(0, 8)
    : [];

  /* ---------- 草稿 ---------- */
  /** 草稿该存的文字：编辑模式下输入框里是编辑缓冲，草稿仍是进入编辑前的文字。 */
  const draftText = () => (beforeEdit.current ?? latestText.current);

  const refreshStatus = () => {
    const current = slot.current;
    if (draftBusy(current)) setDraftStatus('saving');
    else if (current.failed) setDraftStatus('unsaved');
    else if (draftText() === current.saved) setDraftStatus(current.id === null ? null : 'saved');
    else setDraftStatus('unsaved');
  };
  const refreshRef = useRef(refreshStatus);
  refreshRef.current = refreshStatus;

  /** 让草稿位的状态变化与删除失败提示回到当前这个输入框。 */
  const attach = (target: DraftSlot) => {
    target.onChange = () => {
      if (slot.current === target) refreshRef.current();
    };
    target.onError = (message) => {
      if (slot.current === target) setError(message);
    };
  };

  const persistDraft = () => {
    window.clearTimeout(idleTimer.current);
    const content = draftText();
    if (content === slot.current.saved && slot.current.pending === null) return;
    requestDraftSave(slot.current, content);
  };
  const persistRef = useRef(persistDraft);
  persistRef.current = persistDraft;

  useEffect(() => {
    input.current?.focus();
    const current = slot.current;
    attach(current);
    refreshRef.current();
    // 每次 setup 新建 controller：开发期 StrictMode 会先 cleanup 再 setup 一次。
    const controller = new AbortController();
    abort.current = controller;
    return () => {
      // 切换会话（本组件按会话 key 重新挂载）时保存草稿，并中止未完成的上传。
      persistRef.current();
      controller.abort();
      if (current.onChange) current.onChange = null;
      current.onError = null;
    };
  }, []);

  // 服务端草稿变化（另一端修改或删除当前草稿，见 drafts 事件）：本地没改动时采纳新内容；当前草稿被删时
  // 解除旧 id，没发出的文字作为新草稿待存，不再显示「已保存」、也不再改已删除的草稿。
  useEffect(() => {
    const current = slot.current;
    if (current.id === null || draftBusy(current) || editing) return;
    const server = state.drafts[current.id];
    if (!server) {
      current.id = null;
      current.saved = '';
      refreshRef.current();
      return;
    }
    if (server.content === current.saved) return;
    const clean = latestText.current === current.saved;
    current.saved = server.content;
    if (clean) {
      setText(server.content);
      latestText.current = server.content;
      if (server.content) unsent.set(conversationKey, server.content);
      else unsent.delete(conversationKey);
    }
    refreshRef.current();
  }, [state.drafts, editing, conversationKey]);

  // 预览图的对象 URL 在移除、发出或离开会话时释放。
  const attachmentsRef = useRef(attachments);
  attachmentsRef.current = attachments;
  useEffect(() => () => {
    for (const item of attachmentsRef.current) if (item.preview) URL.revokeObjectURL(item.preview);
  }, []);
  const releaseAll = () => {
    for (const item of attachmentsRef.current) if (item.preview) URL.revokeObjectURL(item.preview);
    setAttachments([]);
  };

  const update = (value: string, caret?: number) => {
    setText(value);
    latestText.current = value;
    const query = mentionQuery(value, caret ?? value.length);
    setMention(query && members.length > 0 ? { ...query, index: 0 } : null);
    // 编辑模式下文字只是编辑缓冲，不写进未发送缓存和草稿。
    if (editing) return;
    if (value) unsent.set(conversationKey, value);
    else unsent.delete(conversationKey);
    refreshStatus();
    window.clearTimeout(idleTimer.current);
    idleTimer.current = window.setTimeout(() => persistRef.current(), DRAFT_IDLE_MS);
  };

  /** 在光标处插入文字；`select` 为插入后要选中的片段（格式占位文字）。 */
  const insertAtCaret = (snippet: string, select?: { from: number; to: number }) => {
    const element = input.current;
    const from = element?.selectionStart ?? text.length;
    const to = element?.selectionEnd ?? text.length;
    const value = text.slice(0, from) + snippet + text.slice(to);
    update(value, from + snippet.length);
    requestAnimationFrame(() => {
      if (!element) return;
      element.focus();
      if (select) element.setSelectionRange(from + select.from, from + select.to);
      else element.setSelectionRange(from + snippet.length, from + snippet.length);
    });
  };

  // 右键菜单「回复」「引用」。
  useEffect(() => {
    if (insert) insertAtCaret(insert.text);
    // 只响应新的插入请求。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [insert?.nonce]);

  // 「编辑」或 ↑：输入框切到编辑模式，Esc 取消后恢复原来的文字。
  useEffect(() => {
    if (!editing) return;
    beforeEdit.current = latestText.current;
    setText(editing.raw);
    latestText.current = editing.raw;
    releaseAll();
    requestAnimationFrame(() => input.current?.focus());
  }, [editing]);

  /** 退出编辑模式（保存成功或取消），恢复进入编辑前的输入。 */
  const leaveEditing = () => {
    const restore = beforeEdit.current ?? '';
    beforeEdit.current = null;
    useComposerBridge.getState().stopEditing();
    setText(restore);
    latestText.current = restore;
  };
  const cancelEditing = leaveEditing;

  const chooseMention = (userId: number) => {
    if (!mention) return;
    const caret = input.current?.selectionStart ?? text.length;
    const snippet = `@**${userName(state, userId)}|${userId}** `;
    const value = text.slice(0, mention.start) + snippet + text.slice(caret);
    update(value, mention.start + snippet.length);
    setMention(null);
    requestAnimationFrame(() => input.current?.setSelectionRange(mention.start + snippet.length, mention.start + snippet.length));
  };

  /* ---------- 附件 ---------- */
  const addFiles = (files: FileList | File[]) => {
    const limit = state.maxUploadMib * 1024 * 1024;
    const accepted: Attachment[] = [];
    for (const file of Array.from(files)) {
      if (file.size > limit) {
        setError(`「${file.name}」超过 ${state.maxUploadMib} MB，不能发送。`);
        continue;
      }
      accepted.push({
        id: nextAttachment++, file, progress: 0,
        preview: file.type.startsWith('image/') ? URL.createObjectURL(file) : null,
      });
    }
    if (accepted.length > 0) setAttachments((current) => [...current, ...accepted]);
  };

  const removeAttachment = (id: number) => {
    const removed = attachmentsRef.current.find((item) => item.id === id);
    if (removed?.preview) URL.revokeObjectURL(removed.preview);
    setAttachments((current) => current.filter((item) => item.id !== id));
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    if (editing || event.clipboardData.files.length === 0) return;
    event.preventDefault();
    addFiles(event.clipboardData.files);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    setDropping(false);
    if (editing || event.dataTransfer.files.length === 0) return;
    event.preventDefault();
    addFiles(event.dataTransfer.files);
  };

  /* ---------- 发送与编辑 ---------- */
  const submit = async () => {
    const content = text.trim();
    if (busy || (!content && attachments.length === 0)) return;
    if (!editing && topic !== undefined && !topic.trim()) {
      setError('这是多话题群，请先在上方选择或填写话题。');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (editing) {
        await editMessage(editing.messageId, content);
        leaveEditing();
        return;
      }
      // 只处理点发送这一刻的附件和文字；发送期间新加的附件、新输入的文字都保留。
      const sending = attachments;
      const sentText = text;
      const signal = abort.current?.signal ?? AbortSignal.abort();
      const links: string[] = [];
      for (const item of sending) {
        const result = await uploadAttachment(item.file, (ratio) => {
          setAttachments((current) => current.map((entry) => (entry.id === item.id ? { ...entry, progress: ratio } : entry)));
        }, signal);
        links.push(`[${result.filename}](${result.url})`);
      }
      await sendMessage(conversationKey, [content, ...links].filter(Boolean).join('\n'), topic?.trim());
      const sent = new Set(sending.map((item) => item.id));
      for (const item of sending) if (item.preview) URL.revokeObjectURL(item.preview);
      setAttachments((current) => current.filter((item) => !sent.has(item.id)));
      if (latestText.current === sentText) {
        window.clearTimeout(idleTimer.current);
        setText('');
        latestText.current = '';
        unsent.delete(conversationKey);
        // 草稿里存的就是刚发出的内容（或首次保存还在途），排在已有保存之后删掉；失败时明确提示，不重发消息。
        const target = slot.current;
        if (target.id !== null || draftBusy(target)) {
          requestDraftSave(target, '', '消息已发出，但草稿没有删掉；离开输入框时会再删一次。');
        }
        refreshStatus();
      }
    } catch (failure) {
      if (!(failure instanceof DOMException && failure.name === 'AbortError')) {
        setError(failureText(failure, editing ? '编辑' : '发送'));
      }
    } finally {
      setBusy(false);
    }
  };

  const editLastOwn = () => {
    const messages = state.windows[conversationKey]?.messages ?? [];
    const last = [...messages].reverse().find((message) => message.sender_id === state.selfId);
    if (!last) return;
    readRawContent(last.id).then(
      (raw) => useComposerBridge.getState().startEditing(conversationKey, state.session, last.id, raw),
      () => setError('读取原文失败，暂时不能编辑。'),
    );
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const composing = event.nativeEvent.isComposing || event.keyCode === 229;
    if (mention && candidates.length > 0 && !composing) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const step = event.key === 'ArrowDown' ? 1 : -1;
        setMention({ ...mention, index: (mention.index + step + candidates.length) % candidates.length });
        return;
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault();
        chooseMention(candidates[mention.index]!);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        setMention(null);
        return;
      }
    }
    if (event.key === 'Escape' && editing) {
      event.preventDefault();
      cancelEditing();
      return;
    }
    if (event.key === 'ArrowUp' && !composing && text === '' && attachments.length === 0 && !editing) {
      event.preventDefault();
      editLastOwn();
      return;
    }
    if (event.key !== 'Enter' || composing) return;
    const wantsSend = enterSends ? !event.shiftKey && !event.ctrlKey : event.ctrlKey || event.metaKey;
    if (!wantsSend) return;
    event.preventDefault();
    void submit();
  };

  /* ---------- 高度拖动 ---------- */
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

  const emojiPanel = (
    <div aria-label="选择表情" className={styles.emojiGrid} role="group">
      {COMMON_EMOJI.map((emoji) => (
        <button aria-label={emoji.name} key={emoji.code} onClick={() => insertAtCaret(emojiChar(emoji.code))} type="button">
          {emojiChar(emoji.code)}
        </button>
      ))}
      {state.realmEmoji.map((emoji) => (
        <button aria-label={emoji.name} key={emoji.id} onClick={() => insertAtCaret(`:${emoji.name}: `)} type="button">
          :{emoji.name}:
        </button>
      ))}
    </div>
  );

  const otherDrafts = drafts.filter((draft) => draft.id !== slot.current.id);

  return (
    <div
      className={styles.composer}
      data-dropping={dropping ? 'true' : 'false'}
      data-testid="im-composer"
      onDragLeave={() => setDropping(false)}
      onDragOver={(event) => {
        if (editing || !event.dataTransfer.types.includes('Files')) return;
        event.preventDefault();
        setDropping(true);
      }}
      onDrop={onDrop}
    >
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
      {editing ? (
        <div className={styles.editing} role="status">
          <Icon name="pencil" size={14} />正在编辑消息
          <button onClick={cancelEditing} type="button">取消（Esc）</button>
        </div>
      ) : null}
      <div className={styles.tools}>
        <Popover content={emojiPanel} placement="topLeft" trigger="click">
          <button aria-label="表情" className={styles.tool} title="表情" type="button"><Icon name="smile" size={19} /></button>
        </Popover>
        <button aria-label="提及成员" className={styles.tool} onClick={() => insertAtCaret('@')} title="提及成员" type="button">
          <Icon name="atsign" size={19} />
        </button>
        <button aria-label="图片" className={styles.tool} disabled={Boolean(editing)} onClick={() => imagePicker.current?.click()} title="图片" type="button">
          <Icon name="image" size={19} />
        </button>
        <button aria-label="文件" className={styles.tool} disabled={Boolean(editing)} onClick={() => filePicker.current?.click()} title="文件" type="button">
          <Icon name="folder" size={19} />
        </button>
        <Dropdown
          menu={{
            items: FORMATS.map((format) => ({ key: format.key, label: format.label })),
            onClick: ({ key }) => {
              const format = FORMATS.find((item) => item.key === key)!;
              insertAtCaret(`${format.before}${format.placeholder}${format.after}`, {
                from: format.before.length, to: format.before.length + format.placeholder.length,
              });
            },
          }}
          placement="topLeft"
          trigger={['click']}
        >
          <button aria-label="格式" className={styles.tool} title="格式" type="button"><Icon name="type" size={19} /></button>
        </Dropdown>
        {otherDrafts.length > 0 ? (
          <Dropdown
            menu={{
              items: otherDrafts.map((draft) => ({ key: String(draft.id), label: draft.content.slice(0, 24) || '（空草稿）' })),
              onClick: ({ key }) => {
                const draft = otherDrafts.find((item) => String(item.id) === key);
                if (!draft) return;
                // 先把当前草稿的改动排进它自己的保存，再换到另一份；旧保存完成后只改旧草稿位。
                persistRef.current();
                const previous = slot.current;
                previous.onChange = null;
                previous.onError = null;
                slot.current = switchDraftSlot(conversationKey, draft);
                attach(slot.current);
                setText(draft.content);
                latestText.current = draft.content;
                unsent.set(conversationKey, draft.content);
                setDraftStatus('saved');
              },
            }}
            trigger={['click']}
          >
            <button className={styles.draftPick} type="button">草稿 {otherDrafts.length}<Icon name="chevron" size={14} /></button>
          </Dropdown>
        ) : null}
        <button
          aria-label="聊天记录"
          className={`${styles.tool} ${styles.toolEnd}`}
          onClick={() => window.dispatchEvent(new CustomEvent('eternalai:im-search'))}
          title="聊天记录（Ctrl+F）"
          type="button"
        >
          <Icon name="history" size={19} />
        </button>
        <input accept="image/*" hidden multiple onChange={(event) => { if (event.target.files) addFiles(event.target.files); event.target.value = ''; }} ref={imagePicker} type="file" />
        <input hidden multiple onChange={(event) => { if (event.target.files) addFiles(event.target.files); event.target.value = ''; }} ref={filePicker} type="file" />
      </div>
      {attachments.length > 0 ? (
        <ul aria-label="待发送的附件" className={styles.tray}>
          {attachments.map((item) => (
            <li className={styles.trayItem} key={item.id}>
              {item.preview ? <img alt="" src={item.preview} /> : <span className={styles.trayFile}><Icon name="file" size={18} /></span>}
              <span className={styles.trayName} title={item.file.name}>{item.file.name}</span>
              {busy ? <span className={styles.trayProgress}>{Math.round(item.progress * 100)}%</span> : (
                <button aria-label={`移除 ${item.file.name}`} onClick={() => removeAttachment(item.id)} type="button">
                  <Icon name="close" size={14} />
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      <div className={styles.inputWrap}>
        {mention && candidates.length > 0 ? (
          <ul aria-label="选择要提及的人" className={styles.mentions} role="listbox">
            {candidates.map((id, index) => (
              <li
                aria-selected={index === mention.index}
                key={id}
                onMouseDown={(event) => { event.preventDefault(); chooseMention(id); }}
                role="option"
              >
                {userName(state, id)}
              </li>
            ))}
          </ul>
        ) : null}
        <textarea
          aria-label={editing ? '编辑消息' : '输入消息'}
          className={styles.input}
          onBlur={() => persistRef.current()}
          onChange={(event) => update(event.target.value, event.target.selectionStart)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          placeholder={placeholder}
          ref={input}
          style={{ height }}
          value={text}
        />
      </div>
      <div className={styles.foot}>
        {error ? <span className={styles.error} role="alert">{error}</span> : null}
        {!error && draftStatus ? <span className={styles.draftStatus}>草稿{DRAFT_LABEL[draftStatus]}</span> : null}
        <span className={styles.hint}>{enterSends ? 'Enter 发送 · Shift + Enter 换行' : 'Ctrl + Enter 发送 · Enter 换行'}</span>
        <button className={styles.send} disabled={busy || (text.trim() === '' && attachments.length === 0)} onClick={() => void submit()} type="button">
          {busy ? (editing ? '保存中' : '发送中') : editing ? '保存' : '发送'}
        </button>
      </div>
    </div>
  );
}
