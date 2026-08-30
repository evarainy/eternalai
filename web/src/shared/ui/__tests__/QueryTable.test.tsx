import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import type { ColumnsType } from 'antd/es/table';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { workbenchTheme } from '../../../app/theme';
import { QueryTable } from '../QueryTable';

interface ExampleRow {
  group: string;
  id: number;
  title: string;
}

const rows: ExampleRow[] = Array.from({ length: 12 }, (_, index) => ({
  group: index % 2 === 0 ? '甲组' : '乙组',
  id: index + 1,
  title: `事项 ${String(index + 1).padStart(2, '0')}`,
}));

const columns: ColumnsType<ExampleRow> = [
  {
    dataIndex: 'title',
    key: 'title',
    sorter: (left, right) => left.title.localeCompare(right.title, 'zh-CN'),
    title: '标题',
  },
  {
    dataIndex: 'group',
    filters: [
      { text: '甲组', value: '甲组' },
      { text: '乙组', value: '乙组' },
    ],
    key: 'group',
    onFilter: (value, item) => item.group === value,
    title: '分组',
  },
];

function renderTable(dataSource: ExampleRow[] = rows) {
  return render(
    <ConfigProvider locale={zhCN} theme={workbenchTheme}>
      <QueryTable<ExampleRow>
        columns={columns}
        dataSource={dataSource}
        emptyNextStep="下一步：调整查看范围。"
        emptyReason="当前为空，因为没有符合条件的事项。"
        queryResetKey="all"
        rowKey="id"
      />
    </ConfigProvider>,
  );
}

describe('project-owned thin query table', () => {
  it('uses native Table pagination and keeps the next page reachable', () => {
    const { container } = renderTable();

    expect(screen.getByText('事项 01')).toBeInTheDocument();
    expect(screen.queryByText('事项 11')).not.toBeInTheDocument();
    const pagination = container.querySelector('.ant-pagination');
    expect(pagination).not.toBeNull();
    fireEvent.click(within(pagination as HTMLElement).getByTitle('2'));
    expect(screen.getByText('事项 11')).toBeInTheDocument();
  });

  it('uses native column filtering and reports the filtered total', async () => {
    const { container } = renderTable();
    const filterTrigger = container.querySelector('.ant-table-filter-trigger');
    expect(filterTrigger).not.toBeNull();

    fireEvent.click(filterTrigger as HTMLElement);
    const dropdown = document.querySelector('.ant-table-filter-dropdown');
    expect(dropdown).not.toBeNull();
    fireEvent.click(within(dropdown as HTMLElement).getByText('甲组'));
    fireEvent.click(within(dropdown as HTMLElement).getByRole('button', { name: /确\s*定/ }));

    await waitFor(() => expect(screen.getByText('共 6 项')).toBeInTheDocument());
    expect(screen.getByText('事项 01')).toBeInTheDocument();
    expect(screen.queryByText('事项 02')).not.toBeInTheDocument();
  });

  it('explains both why an empty result is empty and what to do next', () => {
    renderTable([]);

    expect(screen.getByText('当前为空，因为没有符合条件的事项。')).toBeInTheDocument();
    expect(screen.getByText('下一步：调整查看范围。')).toBeInTheDocument();
  });
});
