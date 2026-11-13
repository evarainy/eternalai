import { useRef, useState } from 'react';
import { Button } from 'antd';
import type { UserAction } from '../generated/runtime/runtime.schemas';
import type { ConfirmCardView } from '../contracts/runtimeProjection';
import { fieldLabel, operationName, targetSystemLabels } from '../contracts/businessLabels';
import { userActionOutcomeMessages } from '../contracts/userActionOutcome';
import styles from './RuntimeViews.module.css';

interface ConfirmCardProps {
  confirm: ConfirmCardView;
  responseId: string | null;
  onAction: (action: UserAction) => Promise<void>;
  terminalNotice: string | null;
}

export function ConfirmCard({
  confirm,
  responseId,
  onAction,
  terminalNotice,
}: ConfirmCardProps) {
  const actionInFlight = useRef(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const displayedEntries = Object.entries(confirm.displayedArgumentValues);
  const displayedNames = new Set(displayedEntries.map(([fieldName]) => fieldName));

  const submitConfirmation = async (actionType: 'confirm' | 'cancel') => {
    if (responseId === null || terminalNotice !== null || actionInFlight.current) {
      return;
    }
    actionInFlight.current = true;
    setIsSubmitting(true);
    try {
      await onAction(actionType === 'confirm'
        ? { action_type: 'confirm', response_id: responseId, confirmed: true }
        : { action_type: 'cancel', response_id: responseId });
    } finally {
      actionInFlight.current = false;
      setIsSubmitting(false);
    }
  };

  // 印只在用户亲手确认、并且后端已受理的那一下出现；取消和其他结果不盖章。
  const accepted = terminalNotice === userActionOutcomeMessages.accepted;
  const cancelled = terminalNotice === userActionOutcomeMessages.cancelled;
  const settled = terminalNotice !== null;

  return (
    <section
      aria-label="操作提交前复核"
      className={styles.kcard}
      data-state={accepted ? 'done' : cancelled ? 'off' : settled ? 'void' : 'pending'}
    >
      <div className={styles.kcardHead}>
        <h3 className={styles.kcardTitle}>{operationName(confirm.operationSummary)}</h3>
        <small>{confirm.targetSystem === null ? '未指定目标系统' : targetSystemLabels[confirm.targetSystem]}</small>
        {accepted ? <span className={`wb-stamp ${styles.kcardStamp}`}>已确认</span> : null}
        {settled || responseId === null ? null : <span className={styles.kcardPill}>等你确认</span>}
      </div>

      <dl className={styles.kcardFields}>
        {confirm.fieldNames.map((fieldName) =>
          displayedNames.has(fieldName) ? (
            <div key={fieldName}>
              <dt>{fieldLabel(fieldName)}</dt>
              <dd>{confirm.displayedArgumentValues[fieldName]}</dd>
            </div>
          ) : (
            <div key={fieldName}>
              <dt>{fieldLabel(fieldName)}</dt>
              <dd className={styles.fieldValueUnavailable}>未提供可展示值</dd>
            </div>
          ),
        )}
        {displayedEntries
          .filter(([fieldName]) => !confirm.fieldNames.includes(fieldName))
          .map(([fieldName, fieldValue]) => (
            <div key={fieldName}>
              <dt>{fieldLabel(fieldName)}</dt>
              <dd>{fieldValue}</dd>
            </div>
          ))}
      </dl>

      {terminalNotice !== null ? (
        <p className={styles.kcardNote} role="status">{terminalNotice}</p>
      ) : responseId === null ? null : (
        <>
          <p className={styles.kcardNote}>提交前请逐项核对。点「确认提交」后才会执行。</p>
          <div className={styles.kcardActions}>
            <Button
              className={styles.minimumActionTarget}
              type="primary"
              disabled={isSubmitting}
              loading={isSubmitting}
              onClick={() => void submitConfirmation('confirm')}
            >
              确认提交
            </Button>
            <Button
              className={styles.minimumActionTarget}
              type="text"
              disabled={isSubmitting}
              onClick={() => void submitConfirmation('cancel')}
            >
              取消
            </Button>
          </div>
        </>
      )}
    </section>
  );
}
