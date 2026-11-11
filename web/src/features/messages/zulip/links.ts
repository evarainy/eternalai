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
