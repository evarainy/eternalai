/** 聊天页的「我的功能」面板：看本人全部功能与状态，可用的「使用」= 在输入框里指定它；
 * 已暂停、已停用的到软件中心处理。 */

import { Link } from 'react-router-dom';
import { Button, Drawer, Empty, Flex, Tag, Typography } from 'antd';
import type { SkillListItem } from '../../api/userSkills';
import { STATE_TAG } from './skillState';
import { useMySkills } from './useMySkills';
import styles from './MySkills.module.css';

const { Text } = Typography;
const MANAGE = '/apps';

export function MySkillsPanel({ open, onClose, onUse }: {
  open: boolean;
  onClose: () => void;
  onUse: (item: SkillListItem) => void;
}) {
  const skills = useMySkills(open);
  const items = skills.data?.items ?? [];
  return (
    <Drawer title="我的功能" open={open} onClose={onClose} placement="right"
      size="default" className={styles.drawer}
      footer={<Link to={MANAGE} onClick={onClose}>管理我的功能</Link>}>
      {skills.isError ? <Text type="warning">我的功能暂时读不到，请稍后再试。</Text> : null}
      {skills.isSuccess && items.length === 0 ? (
        <Empty description="还没有保存的功能。查完一次后说「保存为功能」即可。" />
      ) : null}
      <ul className={styles.list} aria-label="我的功能">
        {items.map((item) => (
          <li key={item.skill_id} className={styles.item}>
            <Flex align="center" gap={8} wrap>
              <Text strong>{item.name}</Text>
              <Tag color={STATE_TAG[item.state].color}>{STATE_TAG[item.state].label}</Tag>
            </Flex>
            <Text type="secondary">{item.purpose}</Text>
            <div className={styles.panel}>
              {item.state === 'usable' ? (
                <Button size="small" type="primary" onClick={() => { onUse(item); onClose(); }}>
                  使用
                </Button>
              ) : null}
              {item.state === 'paused' && item.can_regenerate ? (
                <Link to={MANAGE} onClick={onClose}>依赖的查询已变化，去重新生成</Link>
              ) : null}
              {item.state === 'paused' && !item.can_regenerate ? (
                <Text type="secondary">{item.pause_reason_label ?? '已暂停'}</Text>
              ) : null}
              {item.state === 'disabled' ? (
                <Link to={MANAGE} onClick={onClose}>已停用，去启用</Link>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </Drawer>
  );
}
