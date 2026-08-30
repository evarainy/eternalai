import { Empty, Table } from 'antd';
import type { TableProps } from 'antd';
import { useTableQuery } from '../query/useTableQuery';
import styles from './QueryTable.module.css';

interface QueryTableProps<RecordType extends object>
  extends Omit<TableProps<RecordType>, 'pagination' | 'onChange'> {
  emptyReason: string;
  emptyNextStep: string;
  queryResetKey: string;
}

export function QueryTable<RecordType extends object>({
  dataSource = [],
  emptyNextStep,
  emptyReason,
  queryResetKey,
  ...tableProps
}: QueryTableProps<RecordType>) {
  const { onChange, pagination } = useTableQuery<RecordType>({
    resetKey: queryResetKey,
    total: dataSource.length,
  });

  return (
    <Table<RecordType>
      {...tableProps}
      dataSource={dataSource}
      locale={{
        emptyText: (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <div className={styles.emptyState}>
                <strong>{emptyReason}</strong>
                <span>{emptyNextStep}</span>
              </div>
            }
          />
        ),
      }}
      onChange={onChange}
      pagination={pagination}
    />
  );
}
