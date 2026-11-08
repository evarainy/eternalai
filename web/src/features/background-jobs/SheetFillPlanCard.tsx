/** Sheet fill confirmation card: shows the server-validated plan in business terms; "确认填表"
 * submits the background job (same key, plan and session on every retry); the accepted job
 * or a cancellation is kept with the transcript entry, so a remounted card never offers a
 * second submission. Nothing is queued before the user confirms. */

import { useState } from 'react';
import { Alert, Button, Card, Flex, Space, Tag, Typography } from 'antd';
import type { SheetFillMapping, SheetFillPlan } from '../../contracts/sheetFillPlan';
import { rememberReportJob } from './reportJob';
import {
  FIELD_LABELS,
  SOURCE_LABELS,
  forgetPendingSheetFill,
  loadPendingSheetFills,
  rememberPendingSheetFill,
  sheetFillRefusalText,
  submitSheetFill,
} from './sheetFill';

const { Text, Title } = Typography;

type Phase = 'idle' | 'submitting' | 'unconfirmed' | 'refused' | 'cancelled';

interface SheetFillPlanCardProps {
  plan: SheetFillPlan;
  responseId: string;
  /** The chat session the plan was proposed in; null cannot be confirmed. */
  sessionId: string | null;
  /** /me account scope of the current login; null keeps no same-tab recovery record. */
  recoveryScope: string | null;
  /** Cancelled earlier (shared transcript state): shown as the terminal state. */
  cancelled?: boolean;
  onAccepted: (jobId: string) => void;
  /** Records the cancellation where a remounted card reads it back. */
  onCancel?: () => void;
  /** Removes a recovered card from this tab's recovery list. */
  onDismiss?: () => void;
}

function MappingLine({ mapping }: { mapping: SheetFillMapping }) {
  const labels = SOURCE_LABELS[mapping.source];
  const fills = mapping.fills
    .map((fill) => `「${fill.header}」列（${fill.column}）填${FIELD_LABELS[fill.field]}`)
    .join('，');
  return (
    <li>
      <Text>
        工作表「{mapping.sheet}」第 {mapping.first_row}–{mapping.last_row} 行：
        按「{mapping.key_header}」列（{mapping.key_column}）作为{labels.key}，查{labels.source}；
        {fills}。
      </Text>
    </li>
  );
}

export function SheetFillPlanCard({
  plan, responseId, sessionId, recoveryScope, cancelled = false, onAccepted, onCancel, onDismiss,
}: SheetFillPlanCardProps) {
  const [stored] = useState(() => (recoveryScope === null ? [] : loadPendingSheetFills(recoveryScope))
    .find((item) => item.responseId === responseId) ?? null);
  // Generated once per card; a retry always reuses it so the server deduplicates.
  const [idempotencyKey] = useState(() => stored?.idempotencyKey ?? crypto.randomUUID());
  const [localPhase, setPhase] = useState<Phase>(stored === null ? 'idle' : 'unconfirmed');
  const phase: Phase = cancelled ? 'cancelled' : localPhase;
  const [refusal, setRefusal] = useState('');
  const submitSession = stored?.sessionId ?? sessionId;

  const confirm = async () => {
    if (submitSession === null) return;
    const item = { responseId, sessionId: submitSession, idempotencyKey, plan };
    rememberPendingSheetFill(recoveryScope, item);
    setPhase('submitting');
    const outcome = await submitSheetFill(item);
    if (outcome.kind === 'accepted') {
      forgetPendingSheetFill(recoveryScope, responseId);
      rememberReportJob(recoveryScope, outcome.jobId);
      onAccepted(outcome.jobId);
      return;
    }
    if (outcome.kind === 'refused') {
      forgetPendingSheetFill(recoveryScope, responseId);
      setRefusal(sheetFillRefusalText(outcome.code));
      setPhase('refused');
      return;
    }
    setPhase('unconfirmed');
  };

  const hasDocument = plan.mappings.some((mapping) => mapping.source === 'document');

  return (
    <Card aria-label="填表确认" size="small">
      <Flex align="center" justify="space-between" gap={12} wrap>
        <Space wrap>
          <Title level={5} style={{ margin: 0 }}>填表确认</Title>
          {phase === 'cancelled' ? <Tag>已取消</Tag> : null}
          {phase === 'unconfirmed' ? <Tag color="orange">提交结果未确认</Tag> : null}
        </Space>
        {phase === 'refused' && onDismiss ? <Button onClick={onDismiss}>移除</Button> : null}
      </Flex>
      <p><Text>文件：{plan.filename}</Text></p>
      <ul>
        {plan.mappings.map((mapping) => <MappingLine key={mapping.sheet} mapping={mapping} />)}
      </ul>
      <p>
        <Text type="secondary">
          只填写空白格，已有内容不会覆盖；查不到时写入标注并标浅黄色。
          {hasDocument ? '文档问答只填写文档原文。' : ''}
          结果另存为新文件，原表不变。
        </Text>
      </p>
      {phase === 'idle' || phase === 'submitting' ? (
        <Space>
          <Button type="primary" loading={phase === 'submitting'}
            disabled={phase === 'submitting' || submitSession === null}
            onClick={() => void confirm()}>
            确认填表
          </Button>
          <Button disabled={phase === 'submitting'} onClick={onCancel ?? (() => setPhase('cancelled'))}>
            取消
          </Button>
        </Space>
      ) : null}
      {phase === 'unconfirmed' ? (
        <Alert type="warning" showIcon title="填表任务提交结果未确认，可以重试，不会重复填写"
          action={<Button size="small" onClick={() => void confirm()}>重试</Button>} />
      ) : null}
      {phase === 'refused' ? <Alert type="error" showIcon title={refusal} /> : null}
      {phase === 'cancelled' ? <p>已取消，未提交填表。</p> : null}
    </Card>
  );
}
