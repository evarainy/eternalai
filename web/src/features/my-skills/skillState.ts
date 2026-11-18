/** 功能状态在列表、面板里的同一套显示。 */

import type { SkillListItem } from '../../api/userSkills';

export const STATE_TAG: Record<SkillListItem['state'], { label: string; color: string }> = {
  usable: { label: '可用', color: 'green' },
  paused: { label: '已暂停', color: 'orange' },
  disabled: { label: '已停用', color: 'default' },
};
