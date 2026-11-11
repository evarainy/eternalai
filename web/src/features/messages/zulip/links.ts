import { IM_CLIENT_PREFIX } from './transport';

/**
 * 合同 §4.2 的前端改写规则：凡指向 Zulip 文件根路径的相对链接、以固定上游 origin 开头的绝对链接，
 * 统一改写到 `/api/v1/im-client/files/<上游根路径>`，由代理 B 以本人身份读取。
 * 不对浏览器开放 `/api/v1/user_uploads/...`（临时签名 URL 的 JSON 接口）和 `user_uploads/temporary/...`。
 */
const FILE_ROOTS = [
  /^\/user_uploads\/(?!temporary\/)/,
  /^\/thumbnail(?:\?|$)/,
  /^\/avatar\//,
  /^\/user_avatars\//,
  /^\/[^/]+\/emoji\/images\//,
];

const FILES_PREFIX = `${IM_CLIENT_PREFIX}/files`;

let upstreamOrigin: string | null = null;

/** lane 3 在 register 返回后按 `realm_url` 设置；未设置时只改写相对路径。 */
export function setImUpstreamOrigin(origin: string | null): void {
  upstreamOrigin = origin;
}

/**
 * 返回改写后的同源地址；不是 Zulip 文件资源时返回 null。`data:` 只在开发期合成数据中出现，
 * 原样放行由调用方决定。
 */
export function imFileUrl(raw: string): string | null {
  let path = raw.trim();
  if (path.startsWith(FILES_PREFIX)) return path;
  if (upstreamOrigin && path.startsWith(upstreamOrigin + '/')) path = path.slice(upstreamOrigin.length);
  // Zulip 12.3 把指向本站的绝对链接改写成不带前导 `/` 的根相对形式（如 `user_uploads/2/...`）。
  if (/^(user_uploads|user_avatars|avatar|thumbnail)(\/|\?|$)/.test(path)) path = '/' + path;
  if (!path.startsWith('/') || path.startsWith('//')) return null;
  return FILE_ROOTS.some((pattern) => pattern.test(path)) ? FILES_PREFIX + path : null;
}

/** 文件的强制下载入口：`/user_uploads/{realm}/{path}` → `/user_uploads/download/{realm}/{path}`。 */
export function imDownloadUrl(fileUrl: string): string {
  const marker = `${FILES_PREFIX}/user_uploads/`;
  if (!fileUrl.startsWith(marker) || fileUrl.startsWith(`${marker}download/`) || fileUrl.startsWith(`${marker}thumbnail/`)) {
    return fileUrl;
  }
  return `${marker}download/${fileUrl.slice(marker.length)}`;
}

/**
 * 消息页内的消息链接，由消息页解析并定位（`ImWorkspace`）。群消息带上频道，定位时按频道读，入群前的
 * 共享历史也能找到。
 */
export function messageLink(messageId: number, streamId: number | null): string {
  return `${window.location.origin}${messagePagePath({ messageId, streamId })}`;
}

/**
 * Zulip 的消息链接（`#narrow/channel/12-名称/topic/…/near/345`，或带固定上游 origin 的绝对地址）：
 * 取出消息 id 和所在频道。不是消息链接时返回 null。
 */
export function zulipMessageTarget(href: string): { messageId: number; streamId: number | null } | null {
  let hash = href.trim();
  if (upstreamOrigin && hash.startsWith(upstreamOrigin + '/')) hash = hash.slice(upstreamOrigin.length + 1);
  if (!hash.startsWith('#narrow/')) return null;
  const message = /\/(?:near|with)\/(\d+)(?:\/|$)/.exec(hash);
  if (!message) return null;
  const channel = /\/(?:channel|stream)\/(\d+)(?:-|\/|$)/.exec(hash);
  return { messageId: Number(message[1]), streamId: channel ? Number(channel[1]) : null };
}

/** 打开消息页并定位某条消息的站内地址（返回原消息、正文里的 Zulip 消息链接共用）。 */
export function messagePagePath(target: { messageId: number; streamId: number | null }): string {
  const params = new URLSearchParams({ im_message: String(target.messageId) });
  if (target.streamId !== null) params.set('im_stream', String(target.streamId));
  return `/messages?${params.toString()}`;
}
