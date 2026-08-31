import { useRef, useState } from 'react';
import { Button } from 'antd';
import type { ConfirmCardView } from '../contracts/runtimeProjection';
import styles from './RuntimeViews.module.css';

interface ConfirmCardProps {
  confirm: ConfirmCardView;
  responseId: string | null;
  onConfirm: (responseId: string) => Promise<void>;
}

export function ConfirmCard({
  confirm,
  responseId,
  onConfirm,
}: ConfirmCardProps) {
  const actionInFlight = useRef(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const displayedEntries = Object.entries(confirm.displayedArgumentValues);
  const displayedNames = new Set(displayedEntries.map(([fieldName]) => fieldName));

  const submitConfirmation = async () => {
    if (responseId === null || actionInFlight.current) {
      return;
    }
    actionInFlight.current = true;
    setIsSubmitting(true);
    try {
      await onConfirm(responseId);
    } finally {
      actionInFlight.current = false;
      setIsSubmitting(false);
    }
  };

  return (
    <section className={styles.panel} aria-label="操作提交前复核">
      <div className={styles.panelHeading}>
        <h3 className={styles.panelTitle}>提交前请逐项复核</h3>
      </div>
      <p className={styles.reviewSummary}>{confirm.operationSummary}</p>
      <dl className={styles.facts}>
        <dt>能力标识</dt>
        <dd>{confirm.capabilityId}</dd>
        <dt>目标系统</dt>
        <dd>{confirm.targetSystem === null ? '未指定' : confirm.targetSystem}</dd>
      </dl>

      <div className={styles.arguments}>
        <strong className={styles.argumentsTitle}>操作参数</strong>
        <dl className={styles.argumentList}>
          {confirm.fieldNames.map((fieldName) =>
            displayedNames.has(fieldName) ? (
              <div key={fieldName}>
                <dt>{fieldName}</dt>
                <dd>{confirm.displayedArgumentValues[fieldName]}</dd>
              </div>
            ) : (
              <div key={fieldName}>
                <dt>{fieldName}</dt>
                <dd className={styles.fieldValueUnavailable}>未提供可展示值</dd>
              </div>
            ),
          )}
          {displayedEntries
            .filter(([fieldName]) => !confirm.fieldNames.includes(fieldName))
            .map(([fieldName, fieldValue]) => (
              <div key={fieldName}>
                <dt>{fieldName}</dt>
                <dd>{fieldValue}</dd>
              </div>
            ))}
        </dl>
      </div>

      {responseId === null ? null : (
        <div className={styles.actionRow}>
          <Button
            type="primary"
            className={styles.minimumActionTarget}
            disabled={isSubmitting}
            loading={isSubmitting}
            onClick={() => void submitConfirmation()}
          >
            确认提交这项操作
          </Button>
        </div>
      )}
    </section>
  );
}
