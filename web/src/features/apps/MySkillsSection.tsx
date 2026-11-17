/** 软件中心里的「我的功能」：本人保存过的查询功能，只给本人看。
 * 「使用」把一句点名的话填进 AI 助手，由用户自己发出；停用、启用只在可用与停用之间切换，
 * 已暂停的功能要重新查询并保存新版本才能恢复。 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Space, Tag, Typography } from 'antd';
import { ApiError } from '../../api/mutator';
import { listSkills, setSkillState, type SkillListItem } from '../../api/userSkills';
import { useAIDockStore } from '../../stores/aiDockStore';
import { useAuthStore } from '../../stores/authStore';
import styles from './Systems.module.css';

const { Text } = Typography;
const CHAT_PATH = '/chat';

const STATE_TAG: Record<SkillListItem['state'], { label: string; color: string }> = {
  usable: { label: '可用', color: 'green' },
  paused: { label: '已暂停', color: 'orange' },
  disabled: { label: '已停用', color: 'default' },
};

export function MySkillsSection() {
  const generation = useAuthStore((state) => state.generation);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const skills = useQuery({
    queryKey: ['my-skills', generation],
    queryFn: listSkills,
    retry: false,
  });

  const use = (item: SkillListItem) => {
    useAIDockStore.getState().setDraft(`用我的功能「${item.name}」查一下`);
    navigate(CHAT_PATH);
  };
  const toggle = async (item: SkillListItem) => {
    setBusy(item.skill_id);
    setNotice('');
    try {
      await setSkillState(item.skill_id, item.state === 'usable' ? 'disabled' : 'usable',
        item.current_version);
      await queryClient.invalidateQueries({ queryKey: ['my-skills', generation] });
    } catch (error) {
      setNotice(error instanceof ApiError && error.message ? error.message : '操作没有成功，请稍后再试。');
    } finally {
      setBusy(null);
    }
  };

  const items = skills.data?.items ?? [];
  return (
    <section aria-label="我的功能">
      <div className={styles.secRow}>
        <h2 className={styles.sec}>我的功能</h2>
      </div>
      {skills.isError ? <Alert type="warning" showIcon title="我的功能暂时无法读取，请稍后再试。" /> : null}
      {skills.isSuccess && items.length === 0 ? (
        <Text type="secondary">
          还没有保存的功能。在 AI 助手里查完一次后，说「把刚才的查询保存为功能」即可保存。
        </Text>
      ) : null}
      <ul aria-label="我的功能列表" style={{ listStyle: 'none', padding: 0 }}>
        {items.map((item) => (
          <li key={item.skill_id} style={{ padding: '8px 0' }}>
            <Space wrap>
              <Text strong>{item.name}</Text>
              <Tag color={STATE_TAG[item.state].color}>
                {STATE_TAG[item.state].label}
                {item.pause_reason_label ? `：${item.pause_reason_label}` : ''}
              </Tag>
              <Text type="secondary">{item.purpose}</Text>
              {item.state === 'usable' ? <Button size="small" onClick={() => use(item)}>使用</Button> : null}
              {item.state !== 'paused' ? (
                <Button size="small" loading={busy === item.skill_id} disabled={busy !== null}
                  onClick={() => void toggle(item)}>
                  {item.state === 'usable' ? '停用' : '启用'}
                </Button>
              ) : null}
            </Space>
          </li>
        ))}
      </ul>
      {notice ? <Alert type="warning" showIcon title={notice} /> : null}
    </section>
  );
}
