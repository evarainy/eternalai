import type { ReactNode } from 'react';

/**
 * 页面骨架（助手页与消息页之外的页面都用它）：页头不动，正文在自己的区域里滚。
 *
 * 页头左边是标题，标题右侧可以跟一组标签页（`lead`），最右边是搜索框和这个页面唯一的实心主按钮
 * （`actions`）。页头下面那条细线左端有一小段朱红，是纸面的页眉。
 */
export function PageFrame({
  actions,
  children,
  lead,
  title,
}: {
  actions?: ReactNode;
  children: ReactNode;
  lead?: ReactNode;
  title: string;
}) {
  return (
    <div className="wb-page">
      <header className="wb-page-head">
        <h1 className="wb-page-title">{title}</h1>
        {lead}
        <span className="wb-grow" />
        {actions}
      </header>
      <div className="wb-rule" />
      <div className="wb-page-body">{children}</div>
    </div>
  );
}
