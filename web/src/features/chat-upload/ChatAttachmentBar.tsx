import { useState } from 'react';
import { Attachments } from '@ant-design/x';
import type { AttachmentsProps } from '@ant-design/x';
import { Typography, Upload } from 'antd';
import { CHAT_ATTACHMENT_LIMIT, precheckChatFile, XLSX_MIME } from './chatUploadApi';
import type { ChatAttachmentsController } from './useChatAttachments';

type AttachmentItem = NonNullable<AttachmentsProps['items']>[number];

const NOT_ALLOWED = '使用浏览器技能或补充回复时不能带附件。';

function sheetSummary(names: readonly string[]): string {
  return names.length === 0 ? '已上传' : `工作表：${names.join('、')}`;
}

/**
 * Chat attachments: each selected .xlsx is uploaded on its own (one request per file);
 * finished entries list their worksheet names. ``allowed`` is false for browser-skill
 * sends and collection replies, which cannot carry attachments.
 */
export function ChatAttachmentBar({
  controller, allowed,
}: {
  controller: ChatAttachmentsController;
  allowed: boolean;
}) {
  const [notice, setNotice] = useState<string | null>(null);
  const items: AttachmentItem[] = [
    ...controller.attachments.map((attachment): AttachmentItem => ({
      uid: attachment.artifact_id,
      name: attachment.filename,
      status: 'done',
      description: sheetSummary(attachment.sheetNames),
    })),
    ...controller.uploads.map((upload): AttachmentItem => ({
      uid: upload.uid,
      name: upload.filename,
      status: upload.status,
      description: upload.status === 'uploading' ? '上传中…' : upload.message ?? undefined,
    })),
  ];

  const beforeUpload: AttachmentsProps['beforeUpload'] = (file, batch) => {
    if (!allowed) {
      setNotice(NOT_ALLOWED);
      return Upload.LIST_IGNORE;
    }
    if (batch.indexOf(file) >= controller.remaining) {
      setNotice(`每条消息最多带 ${CHAT_ATTACHMENT_LIMIT} 个附件。`);
      return Upload.LIST_IGNORE;
    }
    const problem = precheckChatFile(file);
    if (problem !== null) {
      setNotice(problem);
      return Upload.LIST_IGNORE;
    }
    setNotice(null);
    return true;
  };

  return (
    <div>
      <Attachments
        accept={`.xlsx,${XLSX_MIME}`}
        multiple
        maxCount={CHAT_ATTACHMENT_LIMIT}
        items={items}
        beforeUpload={beforeUpload}
        customRequest={({ file }) => {
          if (file instanceof File) controller.start(file);
        }}
        onRemove={(item) => {
          controller.remove(item.uid);
          return false;
        }}
        placeholder={{
          title: '上传 .xlsx 表格',
          description: `可拖入或点击选择；单个不超过 20 MB，每条消息最多 ${CHAT_ATTACHMENT_LIMIT} 个。`,
        }}
      />
      {!allowed && items.length > 0 ? (
        <Typography.Text type="warning">{NOT_ALLOWED}请先移除附件。</Typography.Text>
      ) : null}
      {notice !== null ? <Typography.Text type="danger" role="alert">{notice}</Typography.Text> : null}
    </div>
  );
}
