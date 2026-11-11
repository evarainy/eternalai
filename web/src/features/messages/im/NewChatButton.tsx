import { useState } from 'react';
import { App as AntApp, Dropdown, Form, Input, Modal, Select } from 'antd';
import { Icon } from '../../../shared/ui/Icon';
import { createGroup, useImStore } from '../zulip/imStore';
import { userDept } from './viewModel';
import { chatWith } from './viewStore';

type Dialog = 'direct' | 'group' | null;

/**
 * 「＋」：发起单聊、新建群（FE_PLAN §3）。单聊直接打开会话；新建群由本人在对话框里确认后才提交，
 * 新群一律私密（FE_PLAN §9 第 1 件），对话框里写明。
 */
export function NewChatButton({ className, directOnly = false }: { className: string; directOnly?: boolean }) {
  const { message: toast } = AntApp.useApp();
  const state = useImStore();
  const [dialog, setDialog] = useState<Dialog>(null);
  const [person, setPerson] = useState<number | null>(null);
  const [groupName, setGroupName] = useState('');
  const [members, setMembers] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);

  const options = Object.values(state.users)
    .filter((user) => user && user.user_id !== state.selfId && user.is_active && !user.is_bot)
    .map((user) => {
      const dept = userDept(state, user!.user_id);
      return { value: user!.user_id, label: dept ? `${user!.full_name}（${dept}）` : user!.full_name };
    });

  const close = () => {
    setDialog(null);
    setPerson(null);
    setGroupName('');
    setMembers([]);
  };

  const submitGroup = async () => {
    setBusy(true);
    try {
      await createGroup(groupName.trim(), members);
      close();
    } catch {
      void toast.error('新建群没有成功，请检查群名是否重复后重试');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {directOnly ? (
        <button aria-label="添加联系人" className={className} onClick={() => setDialog('direct')} title="添加联系人（发起单聊）" type="button">
          <Icon name="useradd" size={17} />
        </button>
      ) : (
      <Dropdown
        menu={{
          items: [{ key: 'direct', label: '发起单聊' }, { key: 'group', label: '新建群' }],
          onClick: ({ key }) => setDialog(key as Dialog),
        }}
        trigger={['click']}
      >
        <button aria-label="发起单聊或新建群" className={className} title="发起单聊、新建群" type="button">
          <Icon name="plus" size={17} />
        </button>
      </Dropdown>
      )}
      <Modal
        destroyOnHidden
        okButtonProps={{ disabled: person === null }}
        okText="开始聊天"
        onCancel={close}
        onOk={() => {
          if (person !== null) chatWith([person]);
          close();
        }}
        open={dialog === 'direct'}
        title="发起单聊"
      >
        <Select
          aria-label="选择联系人"
          onChange={(value: number) => setPerson(value)}
          optionFilterProp="label"
          options={options}
          placeholder="输入姓名查找"
          showSearch
          style={{ width: '100%' }}
          value={person ?? undefined}
        />
      </Modal>
      <Modal
        confirmLoading={busy}
        destroyOnHidden
        okButtonProps={{ disabled: groupName.trim() === '' || members.length === 0 }}
        okText="创建"
        onCancel={close}
        onOk={() => void submitGroup()}
        open={dialog === 'group'}
        title="新建群"
      >
        <Form layout="vertical">
          <Form.Item label="群名称" required>
            <Input maxLength={60} onChange={(event) => setGroupName(event.target.value)} placeholder="例如：城市更新专项工作组" value={groupName} />
          </Form.Item>
          <Form.Item extra="新建的群只有成员能看到和搜索到，成员能看到入群前的聊天记录。" label="成员" required>
            <Select
              aria-label="选择群成员"
              mode="multiple"
              onChange={(value: number[]) => setMembers(value)}
              optionFilterProp="label"
              options={options}
              placeholder="输入姓名查找"
              value={members}
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
