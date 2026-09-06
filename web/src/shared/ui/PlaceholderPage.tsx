import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Icon } from './Icon';
import type { IconName } from './Icon';
import styles from './PlaceholderPage.module.css';

/**
 * 尚未开发的页面的统一写法：**一个图标 + 一句标题 + 一到两句说明 + 一排动作按钮**，形态照定稿画板
 * `_scratch/design/glass/Empty.dc.html`。
 *
 * 2026-08-27「前端信息架构与终态导航」§四 的「空状态统一规范」要求说明**为什么为空**以及**用户下一步
 * 能做什么**，不得只写「暂无数据」——这条继续有效，由 `reason`（为什么）与 `nextStep` + `actions`
 * （下一步）承担。
 *
 * 之前这里是三个各带 `h2` 标题与字符图标的区块（「现在做不了什么 / 以后会做什么 / 现在怎么办」），
 * 使用方再各塞 5~6 条长句，结果整屏都是说明文字。低数字素养用户的界面靠的是**一屏一个重点 + 层级
 * 对比**，不是把每处都加解释、把每个字号都往上顶——满屏强调等于没有重点。字号下限（正文 19px、
 * 点击目标 44×44、对比度 4.5:1）不变，减的是文字量与标题层级。
 */
export interface PlaceholderAction {
  label: string;
  to: string;
}

export interface PlaceholderPageProps {
  /** 现在真的可用的去处，1~3 个。 */
  actions: readonly PlaceholderAction[];
  icon: IconName;
  /** 一句「现在怎么办」，可带链接；动作按钮已经说清楚时可以不写。 */
  nextStep?: ReactNode;
  /** 一句「为什么这里现在是空的」。 */
  reason: string;
  title: string;
}

export function PlaceholderPage({
  actions,
  icon,
  nextStep,
  reason,
  title,
}: PlaceholderPageProps) {
  return (
    <section className={styles.page}>
      <span className={styles.mark}>
        <Icon name={icon} size={34} strokeWidth={1.9} />
      </span>
      <h1 className={styles.title}>{title}</h1>
      <p className={styles.reason}>{reason}</p>
      {nextStep === undefined ? null : <p className={styles.nextStep}>{nextStep}</p>}
      <div className={styles.actions}>
        {actions.map((action) => (
          <Link className={styles.action} key={action.to} to={action.to}>
            {action.label}
          </Link>
        ))}
      </div>
    </section>
  );
}

export default PlaceholderPage;
