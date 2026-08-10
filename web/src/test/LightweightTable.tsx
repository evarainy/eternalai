import type { Key, ReactNode } from 'react';

interface LightweightColumn {
  dataIndex?: string | number;
  key?: Key;
  render?: (value: unknown, record: Record<string, unknown>, index: number) => ReactNode;
  title?: ReactNode;
}

interface LightweightTableProps {
  columns?: LightweightColumn[];
  dataSource?: Record<string, unknown>[];
  rowKey?: string | ((record: Record<string, unknown>) => Key);
}

export function LightweightTable({
  columns = [],
  dataSource = [],
  rowKey,
}: LightweightTableProps) {
  return (
    <table>
      <thead>
        <tr>
          {columns.map((column, columnIndex) => (
            <th key={column.key ?? column.dataIndex ?? columnIndex}>{column.title}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {dataSource.map((record, rowIndex) => {
          const candidateKey =
            typeof rowKey === 'function'
              ? rowKey(record)
              : typeof rowKey === 'string'
                ? record[rowKey]
                : rowIndex;
          const recordKey =
            typeof candidateKey === 'string' || typeof candidateKey === 'number'
              ? candidateKey
              : rowIndex;

          return (
            <tr key={recordKey}>
              {columns.map((column, columnIndex) => {
                const value =
                  typeof column.dataIndex === 'string' || typeof column.dataIndex === 'number'
                    ? record[column.dataIndex]
                    : undefined;
                return (
                  <td key={column.key ?? column.dataIndex ?? columnIndex}>
                    {column.render
                      ? column.render(value, record, rowIndex)
                      : value === null || value === undefined
                        ? ''
                        : String(value)}
                  </td>
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
