import { ApiError, requestWithResponse } from '../../api/mutator';

export const XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
export const CHAT_UPLOAD_MAX_BYTES = 20 * 1024 * 1024;
export const CHAT_ATTACHMENT_LIMIT = 5;

export interface UploadedSheet {
  readonly name: string;
  readonly max_row: number | null;
  readonly max_column: number | null;
}

export interface ChatUploadResult {
  readonly artifact_id: string;
  readonly filename: string;
  readonly size_bytes: number;
  readonly sheets: readonly UploadedSheet[];
  readonly expires_at: string;
}

const TOO_LARGE = '文件超过 20 MB，请拆分或压缩后再上传。';
const TYPE_INVALID = '只支持 .xlsx；.xls 或 WPS 表格请先另存为 .xlsx。';
const MESSAGES: Record<string, string> = {
  chat_upload_too_large: TOO_LARGE,
  chat_upload_type_invalid: TYPE_INVALID,
  chat_upload_encrypted: '文件设有密码，请取消密码后再上传。',
  chat_upload_macro_or_external: '文件含宏、外部链接或嵌入对象，请另存为普通 .xlsx 后再上传。',
  chat_upload_zip_limit: '文件内容过大或结构过于复杂，请拆分后再上传。',
  chat_upload_unreadable: '文件无法读取，请用 Excel 或 WPS 另存为 .xlsx 后再上传。',
  chat_upload_inspection_timeout: '文件检查超时，请拆分后再上传。',
  chat_upload_timeout: '上传超时，请检查网络后重新上传。',
  chat_upload_idempotency_conflict: '该文件与之前的上传不一致，请移除后重新选择文件。',
  chat_upload_in_progress: '该文件正在上传，请稍候。',
  chat_attachment_invalid: '附件已失效或不属于当前会话，请移除后重新上传。',
  chat_attachment_unsupported: '浏览器技能或补充回复时不能带附件，请移除附件后再发送。',
  file_artifact_expired: '附件已过期，请重新上传。',
  file_artifact_unavailable: '文件服务暂时不可用，请稍后再试。',
  authentication_required: '登录已失效，请重新登录后再上传。',
  csrf_validation_failed: '当前请求来源未通过安全校验，请联系管理员检查部署配置。',
};

/** Business-language message for a failed upload or attachment; never a technical detail. */
export function chatUploadMessage(error: unknown): string {
  if (error instanceof ApiError) {
    // Quota messages say which limit was reached; the server writes them for users.
    if (error.code === 'chat_upload_quota_exceeded') return error.message;
    const known = MESSAGES[error.code];
    if (known !== undefined) return known;
  }
  return '上传没有完成，请稍后重新上传。';
}

export function isChatAttachmentRejection(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 422
    && (error.code === 'chat_attachment_invalid' || error.code === 'chat_attachment_unsupported');
}

/** Local check before uploading, for a quicker answer only; the server decides. */
export function precheckChatFile(file: File): string | null {
  if (!file.name.toLowerCase().endsWith('.xlsx')) return TYPE_INVALID;
  if (file.size > CHAT_UPLOAD_MAX_BYTES) return TOO_LARGE;
  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseSheet(value: unknown): UploadedSheet | null {
  if (!isRecord(value) || typeof value.name !== 'string') return null;
  const size = (item: unknown) => (item === null || typeof item === 'number' ? item : undefined);
  const maxRow = size(value.max_row);
  const maxColumn = size(value.max_column);
  if (maxRow === undefined || maxColumn === undefined) return null;
  return { name: value.name, max_row: maxRow, max_column: maxColumn };
}

function parseResult(value: unknown): ChatUploadResult {
  if (!isRecord(value) || typeof value.artifact_id !== 'string'
      || typeof value.filename !== 'string' || typeof value.size_bytes !== 'number'
      || typeof value.expires_at !== 'string' || !Array.isArray(value.sheets)) {
    throw new SyntaxError('chat_upload_response_invalid');
  }
  const sheets = (value.sheets as unknown[]).map(parseSheet);
  if (sheets.some((sheet) => sheet === null)) throw new SyntaxError('chat_upload_response_invalid');
  return {
    artifact_id: value.artifact_id,
    filename: value.filename,
    size_bytes: value.size_bytes,
    sheets: sheets as UploadedSheet[],
    expires_at: value.expires_at,
  };
}

/**
 * One file per request. The key is fixed per attachment entry, so retrying the same entry is
 * answered with the sealed result instead of a second copy.
 */
export async function uploadChatFile(
  file: File, sessionId: string, idempotencyKey: string, signal?: AbortSignal,
): Promise<ChatUploadResult> {
  const { data } = await requestWithResponse<unknown>({
    url: '/api/v1/file-artifacts/chat-uploads',
    method: 'POST',
    params: { session_id: sessionId, filename: file.name },
    headers: { 'Content-Type': XLSX_MIME, 'Idempotency-Key': idempotencyKey },
    rawBody: file,
    signal,
  });
  return parseResult(data);
}

export function attachmentDownloadUrl(artifactId: string): string {
  return `/api/v1/file-artifacts/${encodeURIComponent(artifactId)}/content`;
}
