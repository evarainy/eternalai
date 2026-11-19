/** `$` 候选列表与「指定功能」标签；状态与键盘处理在 useSkillMention。 */

import type { SkillMention } from './useSkillMention';
import styles from './MySkills.module.css';

export function SkillMentionList({ mention }: { mention: SkillMention }) {
  if (!mention.open) return null;
  const empty = mention.loading ? '正在读取我的功能…'
    : mention.failed ? '我的功能暂时读不到，请稍后再试。' : '没有找到可用的功能。';
  return (
    <div className={styles.mentionList} role="listbox" aria-label="选择要指定的功能">
      {mention.candidates.length === 0 ? <div className={styles.mentionEmpty}>{empty}</div> : null}
      {mention.candidates.map((item) => (
        <div
          key={item.skill_id}
          role="option"
          aria-selected={mention.active?.skill_id === item.skill_id}
          className={styles.mentionItem}
          data-active={mention.active?.skill_id === item.skill_id ? 'true' : 'false'}
          // Keep the caret in the input: choose on mouse down, before it would blur.
          onMouseDown={(event) => { event.preventDefault(); mention.select(item); }}
          onMouseEnter={() => mention.setActiveId(item.skill_id)}
        >
          <b>{item.name}</b>
          <span>{item.purpose}</span>
        </div>
      ))}
    </div>
  );
}

export function SkillMentionTag({ mention }: { mention: SkillMention }) {
  if (mention.binding === null) return null;
  return (
    <div className={styles.mentionTag}>
      <span>指定功能：{mention.binding.name}</span>
      <button type="button" aria-label="取消指定功能" onClick={() => mention.clear()}>×</button>
    </div>
  );
}
