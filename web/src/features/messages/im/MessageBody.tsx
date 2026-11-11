/// <reference types="vite/client" />
import { useMemo, useState } from 'react';
import type { MouseEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import DOMPurify from 'dompurify';
import { Icon } from '../../../shared/ui/Icon';
import { isMergedRecord } from '../zulip/forward';
import { imDownloadUrl, imFileUrl, messagePagePath, zulipMessageTarget } from '../zulip/links';
import { messageShape } from './viewModel';
import type { FileKind } from './viewModel';
import styles from './MessageItem.module.css';

/**
 * 消息正文的唯一 HTML 入口（FE_PLAN §4.2）。服务端渲染的 HTML 只在这里经 DOMPurify 净化后插入；
 * 附件、缩略图、头像和组织 emoji 的地址改写到代理文件入口（合同 §4.2），外链新窗口打开。
 * 识别消息链接与交付卡片链接在 lane 4 接入。
 */
const purifier = DOMPurify(window);

function allowedImageSource(src: string): string | null {
  const rewritten = imFileUrl(src);
  if (rewritten) return rewritten;
  // 只有开发期合成数据会出现 data: 图片；真实消息里的图一律走代理入口。
  return import.meta.env.DEV && src.startsWith('data:image/') ? src : null;
}

purifier.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    const href = node.getAttribute('href') ?? '';
    const rewritten = imFileUrl(href);
    const message = rewritten ? null : zulipMessageTarget(href);
    if (rewritten) {
      node.setAttribute('href', rewritten);
    } else if (message) {
      // 正文里的 Zulip 消息链接：改成消息页的定位地址，本页内跳转后定位原消息。
      node.setAttribute('href', messagePagePath(message));
      node.setAttribute('data-internal', 'true');
    } else if (/^https?:/i.test(href) && new URL(href).origin === window.location.origin) {
      // 工作台自己的页面（送核卡片的「回到 EternalAI 核实」）：本页内跳转。
      node.setAttribute('data-internal', 'true');
    } else if (/^https?:/i.test(href)) {
      node.setAttribute('target', '_blank');
      node.setAttribute('rel', 'noopener noreferrer');
    } else if (!href.startsWith('#') && !(import.meta.env.DEV && href.startsWith('data:image/'))) {
      node.removeAttribute('href');
    }
  } else if (node.tagName === 'IMG') {
    const src = allowedImageSource(node.getAttribute('src') ?? '');
    if (src) node.setAttribute('src', src);
    else node.removeAttribute('src');
    node.removeAttribute('srcset');
  } else if (node.tagName === 'SPAN' && node.classList.contains('emoji')) {
    // Unicode emoji 由 Zulip 渲染成 `:name:` 文字加 `emoji-<码点>` class，这里换回字符本身。
    const code = [...node.classList].find((name) => /^emoji-[0-9a-f-]+$/.test(name));
    if (code) {
      node.textContent = String.fromCodePoint(...code.slice(6).split('-').map((part) => parseInt(part, 16)));
    }
  }
});

/**
 * 只放行 Zulip 渲染消息实际用到、且不自行加载资源的标签；图片之外的媒体（音视频等）一律去掉，只留
 * 它外层的文件链接，避免出现绕过代理文件入口的第二条资源通道。
 */
const SANITIZE_OPTIONS = {
  ALLOWED_TAGS: [
    'a', 'b', 'blockquote', 'br', 'code', 'del', 'details', 'div', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr',
    'i', 'img', 'li', 'ol', 'p', 'pre', 's', 'span', 'strong', 'sub', 'summary', 'sup', 'table', 'tbody', 'td',
    'th', 'thead', 'time', 'tr', 'ul',
  ],
  ALLOWED_ATTR: [
    'alt', 'class', 'colspan', 'data-user-group-id', 'data-user-id', 'datetime', 'href', 'rowspan', 'src', 'title',
  ],
};

function fileLabel(ext: string): string {
  return ext ? ext.toUpperCase().slice(0, 4) : 'FILE';
}

export function FileIcon({ ext, kind }: { ext: string; kind: FileKind }) {
  return <div aria-hidden="true" className={styles.fileIcon} data-kind={kind}>{fileLabel(ext)}</div>;
}

/** 合并转发的「聊天记录」：默认折叠成卡片，点开看全部（FE_PLAN §11）。正文仍是同一份净化后的 HTML。 */
function MergedRecord({ clean, mine }: { clean: string; mine: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={styles.bubble} data-merged={open ? 'open' : 'closed'} data-mine={mine ? 'true' : 'false'}>
      {/* 已经过 DOMPurify 净化。 */}
      <div className={styles.mergedBody} dangerouslySetInnerHTML={{ __html: clean }} />
      <button className={styles.mergedToggle} onClick={() => setOpen((value) => !value)} type="button">
        {open ? '收起' : '展开全部'}
      </button>
    </div>
  );
}

/** 点到净化时标记为工作台内部的链接时，在本页内跳转（例如打开送核抽屉）。 */
function useInternalLinks() {
  const navigate = useNavigate();
  return (event: MouseEvent<HTMLDivElement>) => {
    const link = (event.target as Element).closest('a[data-internal]');
    if (!(link instanceof HTMLAnchorElement)) return;
    event.preventDefault();
    navigate(`${link.pathname}${link.search}`);
  };
}

export function MessageBody({ html, mine }: { html: string; mine: boolean }) {
  const shape = useMemo(() => messageShape(html), [html]);
  const onClick = useInternalLinks();
  const clean = useMemo(() => (shape.kind === 'html' ? purifier.sanitize(html, SANITIZE_OPTIONS) : ''), [html, shape]);

  if (shape.kind === 'file') {
    const url = imFileUrl(shape.href);
    return (
      <div className={styles.file}>
        <FileIcon ext={shape.ext} kind={shape.fileKind} />
        <div className={styles.fileText}>
          <div className={styles.fileName} title={shape.name}>{shape.name}</div>
          <div className={styles.fileMeta}>{shape.ext ? `${shape.ext.toUpperCase()} 文件` : '文件'}</div>
        </div>
        <div className={styles.fileActions}>
          {url ? (
            <a aria-label={`下载 ${shape.name}`} className={styles.fileAction} download href={imDownloadUrl(url)} title="下载">
              <Icon name="download" size={17} />
            </a>
          ) : null}
        </div>
      </div>
    );
  }
  if (shape.kind === 'image') {
    const src = allowedImageSource(shape.src);
    const href = imFileUrl(shape.href) ?? (src?.startsWith('data:') ? null : src);
    const image = src ? <img alt={shape.title} src={src} /> : <span>[图片无法显示]</span>;
    return href
      ? <a className={styles.image} href={href} rel="noopener noreferrer" target="_blank" title={shape.title}>{image}</a>
      : <div className={styles.image}>{image}</div>;
  }
  if (isMergedRecord(clean)) return <MergedRecord clean={clean} mine={mine} />;
  return (
    <div
      onClick={onClick}
      className={styles.bubble}
      data-mine={mine ? 'true' : 'false'}
      // 已经过 DOMPurify 净化，这是本模块唯一的 HTML 插入点。
      dangerouslySetInnerHTML={{ __html: clean }}
    />
  );
}
