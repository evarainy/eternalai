import { useEffect, useMemo, useState } from 'react';
import type { TablePaginationConfig, TableProps } from 'antd';

export interface TableQueryState {
  current: number;
  pageSize: number;
}

interface UseTableQueryOptions {
  defaultPageSize?: number;
  resetKey: string;
  total: number;
}

export function useTableQuery<RecordType>({
  defaultPageSize = 10,
  resetKey,
  total,
}: UseTableQueryOptions) {
  const [query, setQuery] = useState<TableQueryState>({
    current: 1,
    pageSize: defaultPageSize,
  });
  const { current, pageSize } = query;

  useEffect(() => {
    setQuery((current) => ({ ...current, current: 1 }));
  }, [resetKey]);

  useEffect(() => {
    const lastPage = Math.max(1, Math.ceil(total / pageSize));
    if (current > lastPage) {
      setQuery((current) => ({ ...current, current: lastPage }));
    }
  }, [current, pageSize, total]);

  const pagination = useMemo<TablePaginationConfig>(
    () => ({
      current,
      pageSize,
      showSizeChanger: true,
      showTotal: (count) => `共 ${count} 项`,
    }),
    [current, pageSize],
  );

  const onChange: NonNullable<TableProps<RecordType>['onChange']> = (
    nextPagination,
  ) => {
    setQuery((current) => ({
      current: nextPagination.current ?? current.current,
      pageSize: nextPagination.pageSize ?? current.pageSize,
    }));
  };

  return { onChange, pagination, query };
}
