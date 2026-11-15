import type { Paging } from './paging';
import styles from './Pager.module.css';

/** 卡片底下的翻页条：上一页、第几页共几条、下一页。只有一页时不显示。 */
export function Pager({ label, paging, total }: { label: string; paging: Paging; total: number }) {
  const { page, pageCount, setPage } = paging;
  if (pageCount <= 1) return null;
  return (
    <nav aria-label={label} className={styles.pager}>
      <button disabled={page === 0} onClick={() => setPage(page - 1)} type="button">上一页</button>
      <span aria-live="polite">{`第 ${page + 1} / ${pageCount} 页 · 共 ${total} 条`}</span>
      <button disabled={page === pageCount - 1} onClick={() => setPage(page + 1)} type="button">下一页</button>
    </nav>
  );
}
