import type { ReactNode } from 'react';
import styles from './PlaceholderPage.module.css';

/**
 * 尚未开发的页面的统一写法。
 *
 * 2026-08-27「前端信息架构与终态导航」§四 的「空状态统一规范」要求说明为什么为空以及用户下一步能
 * 做什么，不得只写「暂无数据」。这里固定三段：现在做不了什么 / 以后会做什么 / 现在怎么办。
 */
export interface PlaceholderPageProps {
  /** 第一段：这个页面现在做不了什么。 */
  unavailable: string;
  /** 第二段：以后会做什么。 */
  planned: readonly ReactNode[];
  /** 第三段：现在怎么办，必须给出当前真的可用的替代路径。 */
  alternatives: readonly ReactNode[];
  title: string;
}

export function PlaceholderPage({
  alternatives,
  planned,
  title,
  unavailable,
}: PlaceholderPageProps) {
  return (
    <div className={styles.page}>
      <h1 className={styles.title}>{title}</h1>

      <section aria-label="这个页面现在做不了什么" className={styles.section}>
        <h2 className={styles.sectionTitle}>
          <span aria-hidden="true" className={styles.sectionIcon}>
            ⊘
          </span>
          这个页面现在做不了什么
        </h2>
        <p className={styles.text}>{unavailable}</p>
      </section>

      <section aria-label="以后会做什么" className={styles.section}>
        <h2 className={styles.sectionTitle}>
          <span aria-hidden="true" className={styles.sectionIcon}>
            ◇
          </span>
          以后会做什么
        </h2>
        <ul className={styles.list}>
          {planned.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      </section>

      <section aria-label="现在怎么办" className={styles.section}>
        <h2 className={styles.sectionTitle}>
          <span aria-hidden="true" className={styles.sectionIcon}>
            ➜
          </span>
          现在怎么办
        </h2>
        <ul className={styles.list}>
          {alternatives.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export default PlaceholderPage;
