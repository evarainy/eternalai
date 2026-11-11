import { ApiError } from '../../../api/mutator';
import { useAuthStore } from '../../../stores/authStore';
import type { ZulipUploadResult } from './types';

/**
 * 附件上传（合同 §4.1）：fetch 拿不到上传进度，所以这里用 XHR，自己带 CSRF 头，并把错误转换成与
 * mutator 相同的 `ApiError`。由调用方的 AbortSignal 纳入 generation 生命周期；身份变化后的迟到
 * 结果按 mutator 的口径丢弃。
 */
export function uploadFile(file: File, onProgress: (ratio: number) => void, signal: AbortSignal): Promise<ZulipUploadResult> {
  const authGeneration = useAuthStore.getState().generation;
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('The operation was aborted.', 'AbortError'));
      return;
    }
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    signal.addEventListener('abort', abort, { once: true });
    const done = () => signal.removeEventListener('abort', abort);
    xhr.open('POST', '/api/v1/im-client/zulip/user_uploads');
    xhr.setRequestHeader('X-EternalAI-CSRF', '1');
    xhr.responseType = 'json';
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
    };
    xhr.onabort = () => {
      done();
      reject(new DOMException('The operation was aborted.', 'AbortError'));
    };
    xhr.onerror = () => {
      done();
      reject(new ApiError(504, 'im_unavailable', '上传连接中断。'));
    };
    xhr.onload = () => {
      done();
      const body: unknown = xhr.response;
      if (xhr.status === 401) {
        useAuthStore.getState().markUnauthenticated(authGeneration);
        reject(new ApiError(401, 'authentication_required', 'Authentication is required.'));
        return;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        const detail = typeof body === 'object' && body !== null && 'detail' in body
          ? (body as { detail: { code?: unknown; message?: unknown } }).detail : null;
        reject(new ApiError(
          xhr.status,
          typeof detail?.code === 'string' ? detail.code : `http_${xhr.status}`,
          typeof detail?.message === 'string' ? detail.message : `HTTP ${xhr.status}`,
        ));
        return;
      }
      if (useAuthStore.getState().generation !== authGeneration) {
        reject(new ApiError(409, 'identity_changed', '身份已变化，请重新读取。'));
        return;
      }
      const result = body as Partial<ZulipUploadResult> | null;
      if (!result || typeof result.url !== 'string') {
        reject(new ApiError(502, 'im_upstream:BAD_RESPONSE', '上传结果未确认。'));
        return;
      }
      resolve({ url: result.url, filename: typeof result.filename === 'string' ? result.filename : file.name });
    };
    const form = new FormData();
    form.append('file', file, file.name);
    xhr.send(form);
  });
}
