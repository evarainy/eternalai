import { useState } from 'react';
import type { FormEvent } from 'react';
import Input from 'antd/es/input/Input';
import { Icon } from '../shared/ui/Icon';
import styles from './WorkObjectSearchField.module.css';

/**
 * 工作事项搜索框。2026-11-11 全站取消顶栏后，它出现在两处：Ctrl K 快速搜索弹窗与 `/search` 结果页顶部。
 * 外观沿用原顶栏凹槽（2026-09-04 雨爷走查要求的带文字「搜索」按钮、左侧装饰图标）。
 */
export function WorkObjectSearchField({
  autoFocus = false,
  initialValue,
  onSubmit,
}: {
  autoFocus?: boolean;
  initialValue: string;
  onSubmit: (value: string) => void;
}) {
  const [value, setValue] = useState(initialValue);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSubmit(value);
  };

  return (
    <form
      className={styles.searchField}
      data-focus-ring="host"
      onSubmit={handleSubmit}
      role="search"
    >
      <span aria-hidden="true" className={styles.searchIcon}>
        <Icon name="search" size={19} />
      </span>
      <Input
        allowClear
        aria-label="搜索工作事项"
        autoFocus={autoFocus}
        className={styles.searchInput}
        onChange={(event) => setValue(event.target.value)}
        placeholder="搜索工作事项、文件编号、责任人"
        value={value}
        variant="borderless"
      />
      <button className={styles.searchSubmit} type="submit">
        搜索
      </button>
    </form>
  );
}
