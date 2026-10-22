import { requestWithResponse } from './mutator';
import type { ApiRequestConfig } from './mutator';

export interface AttachmentView {
  attachment_id: string;
  work_object_id: string | null;
  handling_relation_id: string | null;
  purpose: 'task_material' | 'result';
  visibility: 'shared' | 'department_internal';
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  version: number;
  created_at: string;
  linked_at: string | null;
}

export const requestWorkDispatch = <T>(config: ApiRequestConfig) => requestWithResponse<T>(config);

export async function uploadWorkAttachment(
  file: File, options: { purpose: AttachmentView['purpose']; idempotencyKey: string; signal?: AbortSignal },
): Promise<AttachmentView> {
  return (await requestWithResponse<AttachmentView>({
    url: '/api/v1/work-objects/attachments', method: 'POST',
    params: { filename: file.name, media_type: mediaType(file.name), purpose: options.purpose },
    headers: { 'Content-Type': 'application/octet-stream', 'Idempotency-Key': options.idempotencyKey },
    rawBody: file, signal: options.signal,
  })).data;
}

function mediaType(filename: string): string {
  const types: Record<string, string> = {
    pdf: 'application/pdf', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png',
  };
  return types[filename.split('.').pop()?.toLowerCase() ?? ''] ?? 'application/octet-stream';
}

export async function downloadWorkAttachment(
  workObjectId: string, attachmentId: string, options?: { signal?: AbortSignal },
): Promise<Blob> {
  return (await requestWithResponse<Blob>({
    url: `/api/v1/work-objects/${encodeURIComponent(workObjectId)}/attachments/${encodeURIComponent(attachmentId)}/content`,
    method: 'GET', responseType: 'blob', signal: options?.signal,
  })).data;
}
