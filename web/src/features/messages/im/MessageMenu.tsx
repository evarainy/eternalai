import type { ReactNode } from 'react';
import { App as AntApp, Dropdown } from 'antd';
import type { MenuProps } from 'antd';
import { Icon } from '../../../shared/ui/Icon';
import {
  deleteMessage, markUnread, readRawContent, toggleReaction, toggleStar, useImStore,
} from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';
import { messageLink } from '../zulip/links';
import type { ZulipMessage } from '../zulip/types';
import { useComposerBridge } from './composerStore';
import { QUICK_REACTIONS, emojiChar, unicodeEmojiRef } from './emoji';
import { messageText, userName } from './viewModel';
import styles from './MessageMenu.module.css';

function quoteBlock(text: string): string {
  return text.split('\n').map((line) => `> ${line}`).join('\n');
}

/**
 * 右键菜单（FE_PLAN §3）：表态、回复、引用、复制、收藏、标为未读、复制消息链接、交给我的 AI，
 * 自己的消息另有编辑、撤回。「回复」是 @发送人加引用块，「引用」只插入引用块；收藏只对本人生效。
 */
export function MessageMenu({ children, conversationKey, message }: {
  children: ReactNode;
  conversationKey: ConversationKey;
  message: ZulipMessage;
}) {
  const { message: toast, modal } = AntApp.useApp();
  const selfId = useImStore((state) => state.selfId);
  const mine = message.sender_id === selfId;
  const starred = message.flags.includes('starred');

  const run = (action: () => Promise<void>, failure: string) => {
    action().catch(() => void toast.error(failure));
  };
  const copy = (text: string, done: string) => {
    navigator.clipboard.writeText(text).then(() => void toast.success(done), () => void toast.error('复制失败，请手动选择文字复制'));
  };

  const items: MenuProps['items'] = [
    { key: 'reply', label: '回复', icon: <Icon name="reply" size={16} /> },
    { key: 'quote', label: '引用', icon: <Icon name="quote" size={16} /> },
    { key: 'copy', label: '复制', icon: <Icon name="copy" size={16} /> },
    { key: 'star', label: starred ? '取消收藏' : '收藏', icon: <Icon name="star" size={16} /> },
    ...(mine ? [] : [{ key: 'unread', label: '标为未读', icon: <Icon name="mail" size={16} /> }]),
    { key: 'link', label: '复制消息链接', icon: <Icon name="link" size={16} /> },
    { type: 'divider' as const },
    {
      key: 'ai', label: '交给我的 AI', icon: <Icon name="spark" size={16} />, disabled: true,
      title: '交给我的 AI（后续接入）', className: styles.toAi,
    },
    ...(mine ? [
      { type: 'divider' as const },
      { key: 'edit', label: '编辑', icon: <Icon name="pencil" size={16} />, extra: '↑' },
      { key: 'delete', label: '撤回（删除原消息）', icon: <Icon name="trash" size={16} />, danger: true },
    ] : []),
  ];

  const onClick: MenuProps['onClick'] = ({ key }) => {
    const state = useImStore.getState();
    const text = messageText(message.content);
    const bridge = useComposerBridge.getState();
    if (key === 'reply') {
      bridge.requestInsert(`@**${userName(state, message.sender_id)}|${message.sender_id}**\n${quoteBlock(text)}\n\n`);
    } else if (key === 'quote') {
      bridge.requestInsert(`${quoteBlock(text)}\n\n`);
    } else if (key === 'copy') {
      copy(text, '已复制');
    } else if (key === 'star') {
      run(() => toggleStar(message), '收藏没有成功，请重试');
    } else if (key === 'unread') {
      run(() => markUnread(conversationKey, message), '标为未读没有成功，请重试');
    } else if (key === 'link') {
      copy(messageLink(message.id), '已复制消息链接');
    } else if (key === 'edit') {
      readRawContent(message.id).then(
        (raw) => useComposerBridge.getState().startEditing(conversationKey, state.session, message.id, raw),
        () => void toast.error('读取原文失败，暂时不能编辑'),
      );
    } else if (key === 'delete') {
      void modal.confirm({
        title: '撤回这条消息？',
        content: '撤回会删除原消息，所有人都将看不到它。',
        okText: '撤回',
        okButtonProps: { danger: true },
        cancelText: '取消',
        onOk: () => deleteMessage(message.id).catch(() => {
          void toast.error('撤回没有成功：可能已超过可撤回时间或没有权限');
        }),
      });
    }
  };

  return (
    <Dropdown
      menu={{ items, onClick }}
      popupRender={(menu) => (
        <div className={styles.pop}>
          <div aria-label="表态" className={styles.quick} role="group">
            {QUICK_REACTIONS.map((emoji) => (
              <button
                aria-label={`表态 ${emoji.name}`}
                className={styles.quickButton}
                key={emoji.code}
                onClick={() => run(() => toggleReaction(message, unicodeEmojiRef(emoji)), '表态没有成功，请重试')}
                type="button"
              >
                {emojiChar(emoji.code)}
              </button>
            ))}
          </div>
          {menu}
        </div>
      )}
      trigger={['contextMenu']}
    >
      {children}
    </Dropdown>
  );
}
