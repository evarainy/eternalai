import { useEffect, useRef, useState } from 'react';
import { useAIDockStore } from '../../stores/aiDockStore';
import type { ChatAttachment } from '../../stores/aiDockStore';
import { useAuthStore } from '../../stores/authStore';
import { CHAT_ATTACHMENT_LIMIT, chatUploadMessage, uploadChatFile } from './chatUploadApi';

/** An upload that has not finished; finished ones live in the dock store's draft attachments. */
export interface PendingUpload {
  readonly uid: string;
  readonly filename: string;
  readonly status: 'uploading' | 'error';
  readonly message: string | null;
  readonly controller: AbortController | null;
  readonly sessionId: string;
  readonly generation: number;
}

export interface ChatAttachmentsController {
  readonly attachments: readonly ChatAttachment[];
  readonly uploads: readonly PendingUpload[];
  /** True while any file is still uploading: sending waits for it. */
  readonly busy: boolean;
  readonly remaining: number;
  start: (file: File) => void;
  remove: (uid: string) => void;
}

/**
 * Uploads belong to the conversation and login they started in: a new conversation, a new
 * session id or another auth generation aborts them and drops their entries. Removing an
 * uploading entry aborts its request.
 */
export function useChatAttachments(): ChatAttachmentsController {
  const sessionId = useAIDockStore((state) => state.sessionId);
  const attachments = useAIDockStore((state) => state.draftAttachments);
  const generation = useAuthStore((state) => state.generation);
  const [uploads, setUploads] = useState<PendingUpload[]>([]);
  const uploadsRef = useRef(uploads);
  uploadsRef.current = uploads;

  useEffect(() => {
    const stale = uploadsRef.current.filter((upload) =>
      upload.sessionId !== sessionId || upload.generation !== generation);
    if (stale.length === 0) return;
    for (const upload of stale) upload.controller?.abort();
    setUploads((current) => current.filter((upload) => !stale.includes(upload)));
  }, [sessionId, generation]);

  useEffect(() => () => {
    for (const upload of uploadsRef.current) upload.controller?.abort();
  }, []);

  const start = (file: File) => {
    const conversation = useAIDockStore.getState().ensureSession();
    const startedGeneration = useAuthStore.getState().generation;
    const controller = new AbortController();
    const upload: PendingUpload = {
      uid: crypto.randomUUID(), filename: file.name, status: 'uploading', message: null,
      controller, sessionId: conversation, generation: startedGeneration,
    };
    setUploads((current) => [...current, upload]);
    // One fixed key per entry: the server answers a repeated request with the sealed result.
    uploadChatFile(file, conversation, crypto.randomUUID(), controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      setUploads((current) => current.filter((entry) => entry.uid !== upload.uid));
      const store = useAIDockStore.getState();
      if (store.sessionId !== conversation
          || useAuthStore.getState().generation !== startedGeneration
          || store.draftAttachments.some((entry) => entry.artifact_id === result.artifact_id)) return;
      store.setDraftAttachments([...store.draftAttachments, {
        artifact_id: result.artifact_id,
        filename: result.filename,
        sheetNames: result.sheets.map((sheet) => sheet.name),
      }]);
    }, (error: unknown) => {
      if (controller.signal.aborted) return;
      setUploads((current) => current.map((entry) => (entry.uid === upload.uid
        ? { ...entry, status: 'error', message: chatUploadMessage(error), controller: null }
        : entry)));
    });
  };

  const remove = (uid: string) => {
    const upload = uploadsRef.current.find((entry) => entry.uid === uid);
    if (upload !== undefined) {
      upload.controller?.abort();
      setUploads((current) => current.filter((entry) => entry.uid !== uid));
      return;
    }
    const store = useAIDockStore.getState();
    store.setDraftAttachments(store.draftAttachments.filter((entry) => entry.artifact_id !== uid));
  };

  return {
    attachments,
    uploads,
    busy: uploads.some((upload) => upload.status === 'uploading'),
    remaining: Math.max(0, CHAT_ATTACHMENT_LIMIT - attachments.length - uploads.length),
    start,
    remove,
  };
}
