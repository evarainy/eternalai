import { ApiError } from '../../../api/mutator';
import { selectApiV1ImContextSourcesPost } from '../../../generated/im-context/im-context';
import { useAIDockStore } from '../../../stores/aiDockStore';
import { useAuthStore } from '../../../stores/authStore';
import { useImStore } from './imStore';
import type { ConversationKey } from './narrow';
import type { ZulipMessage } from './types';

/**
 * 「交给我的 AI」（FE_PLAN §11、V1 多条来源 `P2-IM-V1-MULTISOURCE-001`）：把同一会话里所选的 1–50 条消息
 * 作为一组来源交给本人 AI 助手。只提交消息 id、会话和本人身份四元组，正文由后端以本人权限读回。
 * 迟到结果按 auth generation、AIDock 会话和选择序号丢弃；只有完整成功才写入 AIDock 并打开。
 */

let sequence = 0;

/** 后端 V1 的会话引用：频道按 stream_id + 话题，私聊按除本人外的成员（自聊为本人）。 */
function conversationRef(messages: ZulipMessage[], selfId: number): string | null {
  const first = messages[0];
  if (!first) return null;
  if (first.type === 'stream') {
    const sameTopic = messages.every((message) => message.type === 'stream'
      && message.stream_id === first.stream_id && message.subject === first.subject);
    return sameTopic ? JSON.stringify({ type: 'stream', stream_id: first.stream_id, topic: first.subject }) : null;
  }
  const others = [...new Set(first.display_recipient.map((user) => user.id).filter((id) => id !== selfId))]
    .sort((a, b) => a - b);
  return JSON.stringify({ type: 'private', to_user_ids: others.length > 0 ? others : [selfId] });
}

function failureText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'im_source_not_found' || error.code === 'im_source_changed') {
      return '所选消息里有已撤回或无权查看的内容，请重新选择。';
    }
    if (error.code === 'im_mapping_required' || error.code === 'im_identity_mismatch') {
      return '消息账号与绑定的身份对不上，请联系管理员核对。';
    }
  }
  return '交给 AI 没有成功，请稍后重试。';
}

/** 返回 null 表示已交给 AI；否则返回给使用者看的原因。 */
export async function sendToAi(key: ConversationKey, messageIds: number[]): Promise<string | null> {
  const state = useImStore.getState();
  const identity = state.identity;
  if (!identity) return '消息还没有准备好，请稍后再试。';
  const loaded = new Map((state.windows[key]?.messages ?? []).map((message) => [message.id, message]));
  const messages = messageIds.map((id) => loaded.get(id)).filter((message): message is ZulipMessage => message !== undefined);
  if (messages.length !== messageIds.length) return '所选消息已不在当前窗口，请重新选择。';
  const ref = conversationRef(messages, state.selfId);
  if (!ref) return '旧群里请只选同一话题的消息交给 AI。';

  const generation = useAuthStore.getState().generation;
  const dock = useAIDockStore.getState();
  const sessionId = dock.ensureSession();
  const current = ++sequence;
  try {
    const source = await selectApiV1ImContextSourcesPost({
      provider: identity.provider,
      im_instance_id: identity.im_instance_id,
      realm_id: identity.realm_id,
      im_user_id: identity.im_user_id,
      conversation_ref: ref,
      message_ids: messageIds.map(String),
      session_id: sessionId,
    });
    if (useAuthStore.getState().generation !== generation || current !== sequence
      || useAIDockStore.getState().sessionId !== sessionId) return null;
    useAIDockStore.getState().setIMSource({ source, authGeneration: generation, sessionId });
    useAIDockStore.getState().openDock();
    return null;
  } catch (error) {
    if (current === sequence && useAuthStore.getState().generation === generation) {
      useAIDockStore.getState().setIMSource(null);
    }
    return failureText(error);
  }
}
