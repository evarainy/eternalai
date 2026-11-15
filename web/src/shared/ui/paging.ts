import { useState } from 'react';

/** 一张卡片一页放这么多条，多了在卡片里翻页。 */
export const PAGE_SIZE = 5;

export interface Paging {
  /** 当前页，从 0 起。条数变少后自动退回最后一页。 */
  page: number;
  pageCount: number;
  /** 当前页在整份清单里的起止下标：`items.slice(from, to)`。 */
  from: number;
  to: number;
  setPage: (page: number) => void;
}

export function usePaging(total: number): Paging {
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const [requested, setPage] = useState(0);
  const page = Math.min(requested, pageCount - 1);
  const from = page * PAGE_SIZE;
  return { page, pageCount, from, to: from + PAGE_SIZE, setPage };
}
