/** 软件中心里的「我的功能」：本人保存过的查询功能，只给本人看。
 * 「运行」按所选功能与版本打开表单，直接调运行接口，不经模型重新选择；
 * 停用、启用只在可用与停用之间切换；已暂停且依赖的查询还在的功能可「重新生成」：
 * 步骤、列和检查不变，按新的查询合同试运行一次，确认后保存为新版本。 */

import { useState } from 'react';
import { Alert, Button, Input, Popconfirm, Space, Tag, Typography } from 'antd';
import { ApiError } from '../../api/mutator';
import {
  deleteSkill, regenerateSkill, renameSkill, runSkill, saveSkill, setSkillState,
  type SkillListItem, type TrialResponse,
} from '../../api/userSkills';
import type { UserSkillPayload } from '../../contracts/userSkill';
import { useAIDockStore } from '../../stores/aiDockStore';
import { FormFields, UserSkillCard } from './UserSkillCard';
import { STATE_TAG } from './skillState';
import { useMySkills, useRefreshMySkills } from './useMySkills';
import styles from './MySkills.module.css';

const { Text } = Typography;

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

function RegeneratePanel({ item, onSaved }: { item: SkillListItem; onSaved: () => void }) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [phase, setPhase] = useState<'edit' | 'trying' | 'saving' | 'saved'>('edit');
  const [notice, setNotice] = useState('');
  const [trial, setTrial] = useState<{ sessionId: string; response: TrialResponse } | null>(null);
  const hasFields = Object.keys(item.form.schema.properties ?? {}).length > 0;
  const tryRun = async () => {
    setPhase('trying');
    setNotice('');
    setTrial(null);
    try {
      const session = useAIDockStore.getState().ensureSession();
      const response = await regenerateSkill(session, item.skill_id, item.current_version, values);
      setTrial({ sessionId: session, response });
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setPhase('edit');
    }
  };
  const save = async () => {
    if (trial === null) return;
    setPhase('saving');
    try {
      await saveSkill(trial.sessionId, trial.response.trial_id);
      setPhase('saved');
      onSaved();
    } catch (error) {
      setNotice(errorText(error));
      setPhase('edit');
    }
  };
  if (phase === 'saved') {
    return <Alert className={styles.panel} type="success" showIcon title="已保存为新版本，功能恢复可用。" />;
  }
  return (
    <div className={styles.panel}>
      <Text type="secondary">步骤、显示的列和结果检查都不变，按现在的查询方式试运行一次，确认结果后保存为新版本。</Text>
      {hasFields ? (
        <FormFields form={item.form} values={values}
          onChange={(name, value) => setValues((old) => ({ ...old, [name]: value }))} />
      ) : null}
      <Space style={{ marginTop: 8 }}>
        <Button size="small" loading={phase === 'trying'} disabled={phase !== 'edit'}
          onClick={() => void tryRun()}>试运行</Button>
        <Button type="primary" size="small" loading={phase === 'saving'}
          disabled={trial === null || !trial.response.savable || phase !== 'edit'}
          onClick={() => void save()}>保存为新版本</Button>
      </Space>
      {trial !== null && !trial.response.savable ? (
        <Alert style={{ marginTop: 8 }} type="warning" showIcon
          title={trial.response.not_savable_reason ?? '试运行没有成功，不能保存'} />
      ) : null}
      {trial !== null ? <UserSkillCard payload={trial.response.result} sessionId={null} /> : null}
      {notice ? <Alert style={{ marginTop: 8 }} type="warning" showIcon title={notice} /> : null}
    </div>
  );
}

function RenameField({ item, onDone }: { item: SkillListItem; onDone: (renamed: boolean) => void }) {
  const [name, setName] = useState(item.name);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const submit = async () => {
    setBusy(true);
    setNotice('');
    try {
      await renameSkill(item.skill_id, name);
      onDone(true);
    } catch (error) {
      setNotice(errorText(error));
      setBusy(false);
    }
  };
  return (
    <div className={styles.panel}>
      <Space wrap>
        <Input aria-label="新的功能名称" value={name} maxLength={40} style={{ minWidth: 220 }}
          onChange={(event) => setName(event.target.value)} onPressEnter={() => void submit()} />
        <Button type="primary" size="small" loading={busy} disabled={!name.trim()}
          onClick={() => void submit()}>保存名称</Button>
        <Button size="small" disabled={busy} onClick={() => onDone(false)}>取消</Button>
      </Space>
      {notice ? <Alert style={{ marginTop: 8 }} type="warning" showIcon title={notice} /> : null}
    </div>
  );
}

type Panel = { skillId: string; kind: 'run' | 'regenerate' | 'rename' } | null;

export function MySkillsSection() {
  const refresh = useRefreshMySkills();
  const [busy, setBusy] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>(null);
  const [notice, setNotice] = useState('');
  const skills = useMySkills();

  const act = async (item: SkillListItem, action: () => Promise<unknown>) => {
    setBusy(item.skill_id);
    setNotice('');
    try {
      await action();
      if (panel?.skillId === item.skill_id) setPanel(null);
      refresh();
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setBusy(null);
    }
  };
  const toggle = (item: SkillListItem) => act(item, () => setSkillState(
    item.skill_id, item.state === 'usable' ? 'disabled' : 'usable', item.current_version,
  ));
  const remove = (item: SkillListItem) => act(item, () => deleteSkill(item.skill_id));
  const openPanel = (item: SkillListItem, kind: 'run' | 'regenerate' | 'rename') => setPanel(
    (current) => (current?.skillId === item.skill_id && current.kind === kind
      ? null : { skillId: item.skill_id, kind }),
  );
  const isOpen = (item: SkillListItem, kind: 'run' | 'regenerate' | 'rename') =>
    panel?.skillId === item.skill_id && panel.kind === kind;

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
                <Button size="small" onClick={() => openPanel(item, 'run')}>
                  {isOpen(item, 'run') ? '收起' : '运行'}
                </Button>
              ) : null}
              {item.can_regenerate ? (
                <Button size="small" onClick={() => openPanel(item, 'regenerate')}>
                  {isOpen(item, 'regenerate') ? '收起' : '重新生成'}
                </Button>
              ) : null}
              {item.state !== 'paused' ? (
                <Button size="small" loading={busy === item.skill_id} disabled={busy !== null}
                  onClick={() => void toggle(item)}>
                  {item.state === 'usable' ? '停用' : '启用'}
                </Button>
              ) : null}
              <Button size="small" disabled={busy !== null} onClick={() => openPanel(item, 'rename')}>
                改名
              </Button>
              <Popconfirm title={`删除「${item.name}」？`}
                description="删除后不能在列表中恢复。已保存的历史版本会留档，不再使用。"
                okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
                onConfirm={() => remove(item)}>
                <Button size="small" danger disabled={busy !== null}>删除</Button>
              </Popconfirm>
            </Space>
            {isOpen(item, 'run') && item.state === 'usable' ? (
              <RunPanel key={`${item.skill_id}/${item.current_version}`} item={item} />
            ) : null}
            {isOpen(item, 'regenerate') && item.can_regenerate ? (
              <RegeneratePanel key={`${item.skill_id}/${item.current_version}`} item={item}
                onSaved={refresh} />
            ) : null}
            {isOpen(item, 'rename') ? (
              <RenameField key={`${item.skill_id}/rename`} item={item}
                onDone={(renamed) => { setPanel(null); if (renamed) refresh(); }} />
            ) : null}
          </li>
        ))}
      </ul>
      {notice ? <Alert type="warning" showIcon title={notice} /> : null}
    </section>
  );
}
