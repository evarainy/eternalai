import { useEffect, useState } from 'react';
import { App as AntApp, Modal, Select, Switch } from 'antd';
import { Icon } from '../../../shared/ui/Icon';
import {
  addStreamMembers, leaveStream, loadStreamMembers, openConversation, readStreamFiles, setStreamFlag, useImStore,
} from '../zulip/imStore';
import type { ConversationKey } from '../zulip/narrow';
import type { ZulipMessage } from '../zulip/types';
import { Avatar } from './Avatar';
import {
  conversationAvatar, listTime, messageShape, personAvatar, userDept, userName,
} from './viewModel';
import styles from './GroupDrawer.module.css';

const GRID = 9;

type Load<T> = { status: 'loading' } | { status: 'failed' } | { status: 'ready'; value: T };

/**
 * 群设置抽屉（v3-3，FE_PLAN §3 群设置一行）：简介、成员（查看全部、添加）、查找聊天记录、群文件、静音此群、
 * 固定到列表顶部、退出群聊。添加成员与退出群聊都由本人确认后才提交；开关与成员变化以随后的事件为准。
 */
export function GroupDrawer({ conversationKey, streamId, onClose }: {
  conversationKey: ConversationKey;
  streamId: number;
  onClose: () => void;
}) {
  const { message: toast, modal } = AntApp.useApp();
  const state = useImStore();
  const sub = state.subscriptions[streamId];
  const conversation = state.conversations[conversationKey];
  const [members, setMembers] = useState<'loading' | 'failed' | 'ready'>('loading');
  const [files, setFiles] = useState<Load<ZulipMessage[]> | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [adding, setAdding] = useState<number[] | null>(null);

  // 打开时读一次成员记进订阅；之后成员变化由 peer_add/peer_remove 事件更新同一份订阅成员。
  useEffect(() => {
    let live = true;
    setMembers('loading');
    loadStreamMembers(streamId).then(
      () => live && setMembers('ready'),
      () => live && setMembers('failed'),
    );
    return () => {
      live = false;
    };
  }, [streamId]);

  if (!sub || !conversation) return null;

  const memberIds = members === 'ready' ? sub.subscribers ?? [] : [];
  const shown = showAll ? memberIds : memberIds.slice(0, GRID);
  const candidates = Object.values(state.users)
    .filter((user) => user.is_active && !user.is_bot && !memberIds.includes(user.user_id))
    .map((user) => {
      const dept = userDept(state, user.user_id);
      return { value: user.user_id, label: dept ? `${user.full_name}（${dept}）` : user.full_name };
    });

  const toggle = (property: 'is_muted' | 'pin_to_top', value: boolean) => {
    setStreamFlag(streamId, property, value).catch(() => void toast.error('设置没有成功，请重试'));
  };

  const confirmAdd = () => {
    if (!adding || adding.length === 0) return;
    const names = adding.map((id) => userName(state, id)).join('、');
    void modal.confirm({
      title: `把 ${names} 加进「${sub.name}」？`,
      content: '加入后对方能看到本群和入群前的聊天记录。',
      okText: '添加',
      cancelText: '取消',
      onOk: () => addStreamMembers(sub.name, adding).then(() => setAdding(null), () => {
        void toast.error('添加成员没有成功，请确认你有权限后重试');
      }),
    });
  };

  const confirmLeave = () => {
    void modal.confirm({
      title: `退出「${sub.name}」？`,
      content: '退出后不再收到本群消息；私密群需要群里的人重新添加你才能回来。',
      okText: '退出群聊',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => leaveStream(sub.name).then(onClose, () => void toast.error('退出没有成功，请重试')),
    });
  };

  const openFiles = () => {
    setFiles({ status: 'loading' });
    readStreamFiles(conversation).then(
      (value) => setFiles({ status: 'ready', value }),
      () => setFiles({ status: 'failed' }),
    );
  };

  if (files) {
    return (
      <aside aria-label="群文件" className={styles.drawer}>
        <header className={styles.subHead}>
          <button aria-label="返回群设置" className={styles.back} onClick={() => setFiles(null)} type="button">
            <Icon name="chevron" size={16} />
          </button>
          群文件
        </header>
        {files.status === 'loading' ? <p className={styles.state} role="status">正在读取群文件…</p> : null}
        {files.status === 'failed' ? <p className={styles.state} role="alert">群文件读取失败，请重试。</p> : null}
        {files.status === 'ready' && files.value.length === 0 ? <p className={styles.state}>本群还没有文件。</p> : null}
        {files.status === 'ready' ? (
          <ul className={styles.files}>
            {files.value.map((message) => {
              const shape = messageShape(message.content);
              const name = shape.kind === 'file' ? shape.name : shape.kind === 'image' ? '图片' : '附件消息';
              return (
                <li key={message.id}>
                  <button onClick={() => openConversation(conversationKey, message.id)} type="button">
                    <span className={styles.fileName}>{name}</span>
                    <small>{userName(state, message.sender_id)} · {listTime(message.timestamp)}</small>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
      </aside>
    );
  }

  return (
    <aside aria-label="群设置" className={styles.drawer}>
      <header className={styles.head}>
        <Avatar avatar={conversationAvatar(state, conversation)} />
        <div className={styles.titleBlock}>
          <h3>{sub.name}</h3>
          {sub.description ? <p>{sub.description}</p> : null}
        </div>
        <button aria-label="关闭群设置" className={styles.close} onClick={onClose} type="button">
          <Icon name="close" size={16} />
        </button>
      </header>
      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <span>群成员{members === 'ready' ? ` · ${memberIds.length}` : ''}</span>
          {memberIds.length > GRID ? (
            <button className={styles.link} onClick={() => setShowAll((value) => !value)} type="button">
              {showAll ? '收起' : '查看全部'}
            </button>
          ) : null}
        </div>
        {members === 'loading' ? <p className={styles.state} role="status">正在读取成员…</p> : null}
        {members === 'failed' ? <p className={styles.state} role="alert">成员读取失败，请重试。</p> : null}
        <ul className={styles.grid}>
          {shown.map((id) => (
            <li key={id}>
              <Avatar avatar={personAvatar(state, id)} size={36} />
              <span>{userName(state, id)}</span>
            </li>
          ))}
          {members === 'ready' ? (
            <li>
              <button aria-label="添加成员" className={styles.add} onClick={() => setAdding([])} type="button">
                <Icon name="plus" size={18} />
              </button>
              <span>添加</span>
            </li>
          ) : null}
        </ul>
      </section>
      <section className={styles.section}>
        <button
          className={styles.row}
          onClick={() => {
            onClose();
            window.dispatchEvent(new CustomEvent('eternalai:im-search'));
          }}
          type="button"
        >
          查找聊天记录<Icon name="chevron" size={15} />
        </button>
        <button className={styles.row} onClick={openFiles} type="button">
          群文件<Icon name="chevron" size={15} />
        </button>
        <label className={styles.row}>
          静音此群
          <Switch checked={sub.is_muted} onChange={(value) => toggle('is_muted', value)} size="small" />
        </label>
        <label className={styles.row}>
          固定到列表顶部
          <Switch checked={sub.pin_to_top} onChange={(value) => toggle('pin_to_top', value)} size="small" />
        </label>
      </section>
      <section className={styles.section}>
        <button className={styles.leave} onClick={confirmLeave} type="button">退出群聊</button>
      </section>
      <Modal
        destroyOnHidden
        okButtonProps={{ disabled: !adding || adding.length === 0 }}
        okText="下一步"
        onCancel={() => setAdding(null)}
        onOk={confirmAdd}
        open={adding !== null}
        title="添加群成员"
      >
        <Select
          aria-label="选择要添加的人"
          mode="multiple"
          onChange={(value: number[]) => setAdding(value)}
          optionFilterProp="label"
          options={candidates}
          placeholder="输入姓名查找"
          style={{ width: '100%' }}
          value={adding ?? []}
        />
      </Modal>
    </aside>
  );
}
