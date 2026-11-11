import { ApiError, requestWithResponse } from '../../../api/mutator';
import type {
  ImIdentity, ZulipDraft, ZulipEmojiRef, ZulipEventsResponse, ZulipMessagesQuery, ZulipMessagesResponse,
  ZulipRegisterResponse, ZulipSendRequest, ZulipUploadResult,
} from './types';
import { uploadFile } from './upload';

/**
 * 浏览器到本人代理 B 的接口（`IM_CLIENT_CONTRACT.md` v2.2）。界面与 store 只依赖这个接口；
 * 真实实现走 `/api/v1/im-client`，开发期合成数据见 `devTransport.ts`。出错一律抛 mutator 的
 * `ApiError`，业务层只按 `code` 分支（合同 §1）。
 */
export interface ImTransport {
  identity(signal?: AbortSignal): Promise<ImIdentity>;
  register(signal?: AbortSignal): Promise<ZulipRegisterResponse>;
  events(queueId: string, lastEventId: number, signal: AbortSignal): Promise<ZulipEventsResponse>;
  deleteQueue(queueId: string): Promise<void>;
  messages(query: ZulipMessagesQuery, signal?: AbortSignal): Promise<ZulipMessagesResponse>;
  send(request: ZulipSendRequest): Promise<{ id: number }>;
  /** 编辑时取原文：同一 GET /messages 加 apply_markdown=false（合同 §3.2）。 */
  rawContent(messageId: number): Promise<string>;
  editMessage(messageId: number, content: string): Promise<void>;
  deleteMessage(messageId: number): Promise<void>;
  react(messageId: number, emoji: ZulipEmojiRef, add: boolean): Promise<void>;
  setFlag(messageIds: number[], flag: 'read' | 'starred', add: boolean): Promise<void>;
  createDraft(draft: ZulipDraft): Promise<number>;
  editDraft(draftId: number, draft: ZulipDraft): Promise<void>;
  deleteDraft(draftId: number): Promise<void>;
  /** 新群一律私密、历史对成员可见、只用空话题（FE_PLAN §9 第 1 件）。 */
  createGroup(name: string, userIds: number[]): Promise<{ id: number }>;
  upload(file: File, onProgress: (ratio: number) => void, signal: AbortSignal): Promise<ZulipUploadResult>;
}

export const IM_CLIENT_PREFIX = '/api/v1/im-client';

/** 合同 §3.1 冻结的 register 参数。lane 3 只能在集合内删减。 */
export const REGISTER_FETCH_EVENT_TYPES = [
  'message', 'update_message', 'delete_message', 'update_message_flags', 'subscription', 'stream',
  'realm_user', 'realm_user_groups', 'user_group', 'presence', 'drafts', 'user_settings', 'user_topic',
  'realm', 'custom_profile_fields', 'realm_emoji', 'recent_private_conversations',
] as const;

export const REGISTER_EVENT_TYPES = [
  'message', 'update_message', 'delete_message', 'update_message_flags', 'reaction', 'subscription',
  'stream', 'realm_user', 'user_group', 'presence', 'drafts', 'user_settings', 'user_topic', 'realm',
  'custom_profile_fields', 'realm_emoji',
] as const;

const REGISTER_CLIENT_CAPABILITIES = { empty_topic_name: true, simplified_presence_events: true };

/** Zulip 的复合参数值本身是 JSON 字符串；整体按表单编码，经 `rawBody` 发送（合同 §1）。 */
function formBody(fields: Record<string, string | number | boolean | object | null | undefined>): URLSearchParams {
  const body = new URLSearchParams();
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined || value === null) continue;
    body.set(key, typeof value === 'object' ? JSON.stringify(value) : String(value));
  }
  return body;
}

const FORM_HEADERS = { 'Content-Type': 'application/x-www-form-urlencoded' };

async function zulip<T>(
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  path: string,
  options: { query?: Record<string, string | number | boolean>; form?: URLSearchParams; signal?: AbortSignal } = {},
): Promise<T> {
  const { data } = await requestWithResponse<T>({
    url: `${IM_CLIENT_PREFIX}/zulip${path}`,
    method,
    params: options.query,
    headers: options.form ? FORM_HEADERS : undefined,
    rawBody: options.form,
    signal: options.signal,
  });
  return data;
}

export function createHttpTransport(): ImTransport {
  return {
    async identity(signal) {
      const { data } = await requestWithResponse<ImIdentity>({
        url: `${IM_CLIENT_PREFIX}/identity`, method: 'GET', signal,
      });
      return data;
    },
    register(signal) {
      return zulip<ZulipRegisterResponse>('POST', '/register', {
        signal,
        form: formBody({
          apply_markdown: true,
          client_capabilities: REGISTER_CLIENT_CAPABILITIES,
          fetch_event_types: REGISTER_FETCH_EVENT_TYPES,
          event_types: REGISTER_EVENT_TYPES,
        }),
      });
    },
    // 不设更短的超时：只按 AbortSignal 中止（合同 §3.2 超时链第 3 条）。
    events(queueId, lastEventId, signal) {
      return zulip<ZulipEventsResponse>('GET', '/events', {
        signal, query: { queue_id: queueId, last_event_id: lastEventId },
      });
    },
    async deleteQueue(queueId) {
      await zulip<unknown>('DELETE', '/events', { form: formBody({ queue_id: queueId }) });
    },
    messages(query, signal) {
      return zulip<ZulipMessagesResponse>('GET', '/messages', {
        signal,
        query: {
          anchor: String(query.anchor),
          num_before: query.numBefore,
          num_after: query.numAfter,
          narrow: JSON.stringify(query.narrow),
          apply_markdown: query.applyMarkdown ?? true,
          allow_empty_topic_name: true,
        },
      });
    },
    async rawContent(messageId) {
      const result = await zulip<ZulipMessagesResponse>('GET', '/messages', {
        query: {
          anchor: messageId, num_before: 0, num_after: 0, narrow: '[]', apply_markdown: false,
          allow_empty_topic_name: true,
        },
      });
      const message = result.messages.find((item) => item.id === messageId);
      if (!message) throw new ApiError(404, 'im_message_not_found', '这条消息已不存在或无权查看。');
      return message.content;
    },
    async editMessage(messageId, content) {
      await zulip<unknown>('PATCH', `/messages/${messageId}`, { form: formBody({ content }) });
    },
    async deleteMessage(messageId) {
      await zulip<unknown>('DELETE', `/messages/${messageId}`);
    },
    async react(messageId, emoji, add) {
      await zulip<unknown>(add ? 'POST' : 'DELETE', `/messages/${messageId}/reactions`, {
        form: formBody({ emoji_name: emoji.name, emoji_code: emoji.code, reaction_type: emoji.type }),
      });
    },
    async setFlag(messageIds, flag, add) {
      await zulip<unknown>('POST', '/messages/flags', {
        form: formBody({ messages: messageIds, op: add ? 'add' : 'remove', flag }),
      });
    },
    async createDraft(draft) {
      const result = await zulip<{ ids: number[] }>('POST', '/drafts', { form: formBody({ drafts: [draft] }) });
      const id = result.ids[0];
      if (id === undefined) throw new ApiError(502, 'im_upstream:BAD_RESPONSE', '草稿保存结果未确认。');
      return id;
    },
    async editDraft(draftId, draft) {
      await zulip<unknown>('PATCH', `/drafts/${draftId}`, { form: formBody({ draft }) });
    },
    async deleteDraft(draftId) {
      await zulip<unknown>('DELETE', `/drafts/${draftId}`);
    },
    createGroup(name, userIds) {
      return zulip<{ id: number }>('POST', '/channels/create', {
        form: formBody({
          name, subscribers: userIds, invite_only: true, history_public_to_subscribers: true,
          // 12.3 的 topics_policy 是 Json[TopicsPolicy]：值本身要是 JSON 字符串。
          topics_policy: JSON.stringify('empty_topic_only'),
        }),
      });
    },
    upload: uploadFile,
    send({ target, content, queueId, localId }) {
      const recipient = target.type === 'stream'
        ? { type: 'stream', to: target.streamId, topic: target.topic }
        : { type: 'direct', to: target.userIds };
      return zulip<{ id: number }>('POST', '/messages', {
        form: formBody({
          type: recipient.type,
          to: recipient.to,
          topic: 'topic' in recipient ? recipient.topic : undefined,
          content,
          queue_id: queueId,
          local_id: localId,
        }),
      });
    },
  };
}
