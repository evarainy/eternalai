import type { RecordsView } from '../contracts/runtimeProjection';
import styles from './RuntimeViews.module.css';

const LONG_CONTENT_THRESHOLD = 120;

export function RecordsList({ records }: { records: RecordsView }) {
  const heading =
    records.kind === 'pending_workflows' ? '待办记录' : '系统消息';

  return (
    <section className={styles.panel} aria-label={heading}>
      <div className={styles.panelHeading}>
        <h3 className={styles.panelTitle}>{heading}</h3>
        {records.incomplete ? (
          <span className={styles.incomplete}>列表可能不完整</span>
        ) : null}
      </div>

      <ul className={styles.recordList}>
        {records.kind === 'pending_workflows'
          ? records.items.map((record) => (
              <li className={styles.record} key={record.todoId}>
                <h4 className={styles.recordTitle}>{record.title}</h4>
                <dl className={styles.recordFacts}>
                  <dt>待办编号</dt>
                  <dd>{record.todoId}</dd>
                  <dt>状态</dt>
                  <dd>{record.status}</dd>
                  <dt>接收时间</dt>
                  <dd>{record.receivedAt}</dd>
                  <dt>创建时间</dt>
                  <dd>{record.createdAt}</dd>
                  <dt>流程类型</dt>
                  <dd>{record.workflowTypeId}</dd>
                </dl>
              </li>
            ))
          : records.items.map((record) => (
              <li className={styles.record} key={record.messageId}>
                <h4 className={styles.recordTitle}>{record.title}</h4>
                <dl className={styles.recordFacts}>
                  <dt>消息编号</dt>
                  <dd>{record.messageId}</dd>
                  <dt>来源</dt>
                  <dd>{record.sourceName}</dd>
                  <dt>发生时间</dt>
                  <dd>{record.occurredAt}</dd>
                  <dt>业务状态</dt>
                  <dd>{record.businessState}</dd>
                </dl>
                {record.content.length > LONG_CONTENT_THRESHOLD ? (
                  <details className={styles.contentDetails}>
                    <summary>展开完整正文</summary>
                    <p className={styles.content}>{record.content}</p>
                  </details>
                ) : (
                  <p className={styles.content}>{record.content}</p>
                )}
              </li>
            ))}
      </ul>
    </section>
  );
}
