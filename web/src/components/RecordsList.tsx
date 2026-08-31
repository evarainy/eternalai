import type {
  RecordsIncompleteReason,
  RecordsView,
} from '../contracts/runtimeProjection';
import styles from './RuntimeViews.module.css';

const LONG_CONTENT_THRESHOLD = 120;
const incompleteReasonLabels: Record<RecordsIncompleteReason, string> = {
  authoritative_count_missing: 'OA 未提供总记录数。',
  authoritative_count_mismatch: 'OA 总记录数与实际展示记录数不一致。',
  producer_completeness_missing: 'OA 未声明本次结果是否完整。',
  producer_declared_incomplete: 'OA 表示本次结果尚未完整返回。',
  returned_count_missing: 'OA 未提供本次返回记录数。',
  returned_count_mismatch: 'OA 返回计数与实际记录数不一致。',
};

const navigationMessages = {
  deployment_unconfigured: '当前部署未配置可信 OA 地址。',
  missing: 'OA 未提供可打开的链接。',
  untrusted: 'OA 提供的链接未通过安全校验。',
} as const;

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

      {records.incomplete ? (
        <div className={styles.incompleteNotice} role="status">
          <strong>当前仅展示已取回的 {records.items.length} 条记录。</strong>
          {records.kind === 'pending_workflows' &&
          records.authoritativeCount !== null ? (
            <span>OA 标示共有 {records.authoritativeCount} 条。</span>
          ) : null}
          <ul>
            {records.incompleteReasons.map((reason) => (
              <li key={reason}>{incompleteReasonLabels[reason]}</li>
            ))}
          </ul>
          <span>下一步：到 OA 查看完整列表或稍后重试。</span>
        </div>
      ) : null}

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
                {record.navigation.kind === 'allowed' ? (
                  <a
                    className={styles.oaLink}
                    href={record.navigation.href}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    去 OA 查看（新窗口）
                  </a>
                ) : (
                  <p className={styles.navigationNotice}>
                    {navigationMessages[record.navigation.kind]}
                    下一步：到 OA 消息中心查找或联系管理员。
                  </p>
                )}
              </li>
            ))}
      </ul>
    </section>
  );
}
