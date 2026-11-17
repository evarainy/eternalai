/** 软件中心里的「我的功能」：本人保存过的查询功能，只给本人看。
 * 「运行」按所选功能与版本打开表单，直接调运行接口，不经模型重新选择；
 * 停用、启用只在可用与停用之间切换，已暂停的功能要重新查询并保存新版本才能恢复。 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Alert, Button, Space, Tag, Typography } from 'antd';
import { ApiError } from '../../api/mutator';
import { listSkills, runSkill, setSkillState, type SkillListItem } from '../../api/userSkills';
import type { UserSkillPayload } from '../../contracts/userSkill';
import { useAIDockStore } from '../../stores/aiDockStore';
import { useAuthStore } from '../../stores/authStore';
import { FormFields, UserSkillCard } from './UserSkillCard';
import styles from './MySkills.module.css';

const { Text } = Typography;

const STATE_TAG: Record<SkillListItem['state'], { label: string; color: string }> = {
  usable: { label: '可用', color: 'green' },
  paused: { label: '已暂停', color: 'orange' },
  disabled: { label: '已停用', color: 'default' },
};

function errorText(error: unknown): string {
  return error instanceof ApiError && error.message ? error.message : '操作没有成功，请稍后再试。';
}

function RunPanel({ item }: { item: SkillListItem }) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [payload, setPayload] = useState<UserSkillPayload | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [runCount, setRunCount] = useState(0);
  const hasFields = Object.keys(item.form.schema.properties ?? {}).length > 0;
  const run = async () => {
    setBusy(true);
    setNotice('');
    try {
      const session = useAIDockStore.getState().ensureSession();
      const response = await runSkill(session, item.skill_id, item.current_version, values);
      setSessionId(session);
      setPayload(response.user_skill);
      setRunCount((count) => count + 1);
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={styles.panel}>
      {hasFields ? (
        <FormFields form={item.form} values={values}
          onChange={(name, value) => setValues((old) => ({ ...old, [name]: value }))} />
      ) : null}
      <Button type="primary" size="small" style={{ marginTop: 8 }} loading={busy}
        onClick={() => void run()}>开始查询</Button>
      {notice ? <Alert style={{ marginTop: 8 }} type="warning" showIcon title={notice} /> : null}
      {payload !== null ? (
        <div className={styles.panel}>
          <UserSkillCard key={runCount} payload={payload} sessionId={sessionId} />
          {payload.kind === 'result' && payload.exportable ? (
            <Text type="secondary">导出 Excel 请在 AI 助手里使用这个功能。</Text>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function MySkillsSection() {
  const generation = useAuthStore((state) => state.generation);
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const skills = useQuery({
    queryKey: ['my-skills', generation],
    queryFn: listSkills,
    retry: false,
  });

  const toggle = async (item: SkillListItem) => {
    setBusy(item.skill_id);
    setNotice('');
    try {
      await setSkillState(item.skill_id, item.state === 'usable' ? 'disabled' : 'usable',
        item.current_version);
      if (open === item.skill_id) setOpen(null);
      await queryClient.invalidateQueries({ queryKey: ['my-skills', generation] });
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setBusy(null);
    }
  };

  const items = skills.data?.items ?? [];
  return (
    <section aria-label="我的功能" className={styles.section}>
      <div className={styles.secRow}>
        <h2 className={styles.sec}>我的功能</h2>
      </div>
      {skills.isError ? <Alert type="warning" showIcon title="我的功能暂时无法读取，请稍后再试。" /> : null}
      {skills.isSuccess && items.length === 0 ? (
        <Text type="secondary">
          还没有保存的功能。在 AI 助手里查完一次后，说「把刚才的查询保存为功能」即可保存。
        </Text>
      ) : null}
      <ul aria-label="我的功能列表" className={styles.list}>
        {items.map((item) => (
          <li key={item.skill_id} className={styles.item}>
            <Space wrap>
              <Text strong>{item.name}</Text>
              <Tag color={STATE_TAG[item.state].color}>
                {STATE_TAG[item.state].label}
                {item.pause_reason_label ? `：${item.pause_reason_label}` : ''}
              </Tag>
              <Text type="secondary">{item.purpose}</Text>
              {item.state === 'usable' ? (
                <Button size="small"
                  onClick={() => setOpen((current) => (current === item.skill_id ? null : item.skill_id))}>
                  {open === item.skill_id ? '收起' : '运行'}
                </Button>
              ) : null}
              {item.state !== 'paused' ? (
                <Button size="small" loading={busy === item.skill_id} disabled={busy !== null}
                  onClick={() => void toggle(item)}>
                  {item.state === 'usable' ? '停用' : '启用'}
                </Button>
              ) : null}
            </Space>
            {open === item.skill_id && item.state === 'usable' ? (
              <RunPanel key={`${item.skill_id}/${item.current_version}`} item={item} />
            ) : null}
          </li>
        ))}
      </ul>
      {notice ? <Alert type="warning" showIcon title={notice} /> : null}
    </section>
  );
}
