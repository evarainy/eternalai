import { Fragment } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Pager } from './Pager';
import { usePaging } from './paging';
import styles from './AnswerText.module.css';

/**
 * 助手回复的正文。模型爱用两种写法，这里照着排出来，别的原样显示：
 *
 * - `**要紧的话**` 排成粗体；
 * - 连着两行以上的清单（`1. …`、`- …`）排成一张清单小卡片，超过 5 条在卡片里翻页。
 *
 * 只拼 React 元素，不往页面里塞 HTML。没有清单的回复仍是一个段落，文字和原来一样。
 */
type Block =
  | { kind: 'text'; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] };

const ITEM = /^\s{0,3}(?:(\d{1,3})[.、)）]|[-*•·])\s+(\S.*)$/;

function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  const lines = text.split('\n');
  let prose: string[] = [];
  const flushProse = () => {
    const joined = prose.join('\n').replace(/^\n+|\n+$/g, '');
    if (joined !== '') blocks.push({ kind: 'text', text: joined });
    prose = [];
  };
  for (let index = 0; index < lines.length;) {
    let end = index;
    while (end < lines.length && ITEM.test(lines[end]!)) end += 1;
    // 孤零零一行「1. …」多半是句子，不当清单。
    if (end - index < 2) {
      prose.push(lines[index]!);
      index += 1;
      continue;
    }
    flushProse();
    const matches = lines.slice(index, end).map((line) => ITEM.exec(line)!);
    blocks.push({
      kind: 'list',
      ordered: matches.every((match) => match[1] !== undefined),
      items: matches.map((match) => match[2]!.trim()),
    });
    index = end;
  }
  flushProse();
  return blocks;
}

/** `**粗体**`；成不了对的星号原样留着。 */
function inline(text: string): ReactNode {
  const parts = text.split(/\*\*([^*\n]+)\*\*/);
  if (parts.length === 1) return text;
  return parts.map((part, index) => (
    // 奇数下标是两对星号中间的字。
    index % 2 === 1 ? <strong key={index}>{part}</strong> : <Fragment key={index}>{part}</Fragment>
  ));
}

function ListCard({ items, ordered }: { items: string[]; ordered: boolean }) {
  const paging = usePaging(items.length);
  const List = ordered ? 'ol' : 'ul';
  return (
    <div className={styles.listCard}>
      {/* 换页时整份清单重新落一遍。 */}
      <List className={styles.list} key={paging.page} start={ordered ? paging.from + 1 : undefined}>
        {items.slice(paging.from, paging.to).map((item, index) => (
          <li key={paging.from + index} style={{ '--i': index } as CSSProperties}>
            <span aria-hidden="true" className={styles.mark}>{ordered ? paging.from + index + 1 : ''}</span>
            <span className={styles.itemText}>{inline(item)}</span>
          </li>
        ))}
      </List>
      <Pager label="清单分页" paging={paging} total={items.length} />
    </div>
  );
}

export function AnswerText({ className, text }: { className?: string; text: string }) {
  const blocks = parseBlocks(text);
  if (blocks.length === 0) return <p className={className}>{text}</p>;
  return (
    <>
      {blocks.map((block, index) => (
        block.kind === 'text'
          ? <p className={className} key={index}>{inline(block.text)}</p>
          : <ListCard items={block.items} key={index} ordered={block.ordered} />
      ))}
    </>
  );
}
