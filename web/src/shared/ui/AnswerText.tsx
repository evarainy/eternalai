import { Fragment } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Pager } from './Pager';
import { usePaging } from './paging';
import styles from './AnswerText.module.css';

/**
 * 助手回复的正文。模型常用的几种写法照着排出来，别的原样显示：
 *
 * - `## 小标题` 排成小标题；
 * - 连着两行以上的清单（`1. …`、`- …`）排成一张清单小卡片，超过 5 条在卡片里翻页；
 * - `| 列 | 列 |` 加一行 `|---|---|` 的表格排成表格卡片，超过 5 行在卡片里翻页；
 * - 行内的 `**粗体**`、`` `代码` ``、`[文字](链接)`；链接只认 http(s) 和本站路径，别的原样显示；
 * - 单独一行的 `---` 是分隔线，不显示。
 *
 * 只拼 React 元素，不往页面里塞 HTML。什么写法都没有的回复仍是一个段落，文字和原来一样。
 */
type Block =
  | { kind: 'text'; text: string }
  | { kind: 'heading'; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'table'; head: string[]; rows: string[][] };

const ITEM = /^\s{0,3}(?:(\d{1,3})[.、)）]|[-*•·])\s+(\S.*)$/;
const HEADING = /^\s{0,3}#{1,4}\s+(\S.*?)\s*#*\s*$/;
const RULE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_RULE = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$/;

function cells(line: string): string[] {
  return line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
}

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
    const line = lines[index]!;
    const heading = HEADING.exec(line);
    if (heading) {
      flushProse();
      blocks.push({ kind: 'heading', text: heading[1]! });
      index += 1;
      continue;
    }
    if (TABLE_ROW.test(line) && index + 1 < lines.length && TABLE_RULE.test(lines[index + 1]!)) {
      flushProse();
      const head = cells(line);
      let end = index + 2;
      while (end < lines.length && TABLE_ROW.test(lines[end]!)) end += 1;
      // 每行补齐或截到表头的列数，表格不会参差。
      const rows = lines.slice(index + 2, end)
        .map((row) => head.map((_, column) => cells(row)[column] ?? ''));
      blocks.push({ kind: 'table', head, rows });
      index = end;
      continue;
    }
    if (RULE.test(line)) {
      index += 1;
      continue;
    }
    let end = index;
    while (end < lines.length && ITEM.test(lines[end]!)) end += 1;
    // 孤零零一行「1. …」多半是句子，不当清单。
    if (end - index < 2) {
      prose.push(line);
      index += 1;
      continue;
    }
    flushProse();
    const matches = lines.slice(index, end).map((item) => ITEM.exec(item)!);
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

const INLINE = /\*\*([^*\n]+)\*\*|`([^`\n]+)`|\[([^\]\n]+)\]\(([^)\s]+)\)/g;

function linkTo(label: string, href: string, key: number): ReactNode {
  if (/^https?:\/\//i.test(href)) {
    return <a className={styles.link} href={href} key={key} rel="noopener noreferrer" target="_blank">{label}</a>;
  }
  // 本站路径在当前页里跳，对话和进行中的任务不丢。
  if (href.startsWith('/') && !href.startsWith('//')) {
    return <Link className={styles.link} key={key} to={href}>{label}</Link>;
  }
  return <Fragment key={key}>{`[${label}](${href})`}</Fragment>;
}

/** 行内写法；成不了对的符号原样留着。 */
function inline(text: string): ReactNode {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index;
    if (at > last) parts.push(text.slice(last, at));
    const key = at;
    if (match[1] !== undefined) parts.push(<strong key={key}>{match[1]}</strong>);
    else if (match[2] !== undefined) parts.push(<code className={styles.code} key={key}>{match[2]}</code>);
    else parts.push(linkTo(match[3]!, match[4]!, key));
    last = at + match[0].length;
  }
  if (parts.length === 0) return text;
  if (last < text.length) parts.push(text.slice(last));
  return parts.map((part, index) => (typeof part === 'string' ? <Fragment key={`t${index}`}>{part}</Fragment> : part));
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

function TableCard({ head, rows }: { head: string[]; rows: string[][] }) {
  const paging = usePaging(rows.length);
  return (
    <div className={styles.listCard}>
      <div className={styles.tableScroll}>
        <table className={styles.table}>
          <thead>
            <tr>{head.map((cell, column) => <th key={column} scope="col">{inline(cell)}</th>)}</tr>
          </thead>
          <tbody key={paging.page}>
            {rows.slice(paging.from, paging.to).map((row, index) => (
              <tr key={paging.from + index} style={{ '--i': index } as CSSProperties}>
                {row.map((cell, column) => <td key={column}>{inline(cell)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager label="表格分页" paging={paging} total={rows.length} />
    </div>
  );
}

export function AnswerText({ className, text }: { className?: string; text: string }) {
  const blocks = parseBlocks(text);
  if (blocks.length === 0) return <p className={className}>{text}</p>;
  return (
    <>
      {blocks.map((block, index) => {
        if (block.kind === 'text') return <p className={className} key={index}>{inline(block.text)}</p>;
        if (block.kind === 'heading') return <p className={styles.heading} key={index}>{inline(block.text)}</p>;
        if (block.kind === 'list') return <ListCard items={block.items} key={index} ordered={block.ordered} />;
        return <TableCard head={block.head} key={index} rows={block.rows} />;
      })}
    </>
  );
}
