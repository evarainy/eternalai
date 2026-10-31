/**
 * D handoff presentation (M3 §9): business copy only, no status codes, durations or error codes.
 */

import { useEffect, useRef, useState } from 'react';
import { Alert, Button } from 'antd';
import type { BrowserResultView, ParsedBrowserRunView } from '../../contracts/browserRunProjection';
import type { BrowserHandoff } from './api';

export interface BrowserHandoffPanelProps {
  /** The owner-checked run the handoff belongs to; `result`/`cleanup` drive the §9.1 rows. */
  readonly run: ParsedBrowserRunView;
  /**
   * null: the D run is not (yet) sealed. Rendered for every fill_stop card except an M2 historical
   * `completed` card without handoff; the pre-seal rows (running, cancelled) are shown here.
   */
  readonly handoff: BrowserHandoff | null;
  /**
   * "取消协助" / "结束协助": the same cancel endpoint as the run card. Resolving means only that the
   * request was handed off; a rejection means it was not delivered. Acknowledgement comes from a later read.
   */
  readonly onEndAssist: () => Promise<void>;
}

function clock(deadline: string): string {
  const at = new Date(deadline);
  return `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`;
}

function cleanupLine(cleanup: BrowserResultView['cleanup']): string {
  if (cleanup === 'pending') return '正在关闭 OA 窗口。';
  if (cleanup === 'released' || cleanup === 'terminated') return 'OA 窗口已关闭。';
  return 'OA 窗口未能确认已关闭，如仍打开请直接关闭它。';
}

/**
 * Sealed run: replaces the technical fill notice and the run card's own cancel button.
 * Pre-seal (handoff null): only running and cancelled have copy; other failures keep the card as is.
 */
export function BrowserHandoffPanel({ run, handoff, onEndAssist }: BrowserHandoffPanelProps) {
  const [ending, setEnding] = useState(false);
  const [failed, setFailed] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const terminal = run.result !== null;
  let body: React.ReactNode = null;
  let action: string | null = null;
  if (handoff !== null) {
    if (!terminal) {
      const fields = handoff.fields;
      body = (
        <p>
          <strong>报修单已在 OA 窗口中填好。</strong>{' '}
          {fields === null ? '' : `已填写：类别 ${fields.category}；故障位置 ${fields.position}；故障描述 ${fields.description}。`}
          {`请到 OA 核对这张单：已提交的请勿重复提交；确认未提交且仍需报修时，由您在该窗口中亲自提交。处理完后点"结束协助"关闭窗口。窗口最晚保留到 ${clock(handoff.deadline)}。`}
        </p>
      );
      action = '结束协助';
    } else if (run.status === 'completed') {
      body = <p>已确认报修单提交成功。</p>;
    } else {
      body = (
        <p>
          <strong>协助已结束。</strong>{' '}
          提交状态请到 OA 中核对：已提交的请勿重复提交；确认未提交且仍需报修时，再重新发起报修。
        </p>
      );
    }
  } else if (run.status === 'running' && !terminal) {
    body = <p>正在打开 OA 报修单并填写。</p>;
    action = '取消协助';
  } else if (run.status === 'cancelled') {
    body = <p>已取消协助，没有提交任何内容。</p>;
  }
  if (body === null) return null;

  const canAct = action !== null && !terminal && !run.cancel.requested;
  return (
    <section aria-label="报修协助">
      {body}
      {run.result !== null ? <p>{cleanupLine(run.result.cleanup)}</p> : null}
      {canAct ? (
        <Button loading={ending} disabled={ending} onClick={() => {
          setEnding(true);
          setFailed(false);
          void onEndAssist()
            .catch(() => { if (mounted.current) setFailed(true); })
            .finally(() => { if (mounted.current) setEnding(false); });
        }}>{action}</Button>
      ) : null}
      {failed && canAct ? <p role="alert">操作没有成功，请稍后再试。</p> : null}
    </section>
  );
}

/** §9.3: shown above the input box while the current session has an active handoff. */
export function BrowserHandoffComposerNotice() {
  return (
    <Alert type="info" showIcon
      title={'您有一张报修单的协助尚未结束。请到 OA 核对：已提交的请勿重复提交；确认未提交且仍需报修时再提交。点"结束协助"后才能发起新的报修；其他问题可以照常提问。'} />
  );
}

/** §9.4: status label for an M2 historical D `completed` run (no handoff). */
export const FILL_STOP_HISTORY_STATUS_LABEL = '已填写，提交情况未核实';

/** §9.4: business text that replaces the technical fill notice on M2 historical D cards. */
export function BrowserFillStopHistoryNotice() {
  return (
    <Alert type="info" showIcon
      title="已在 OA 中填写类别、故障位置和故障描述。是否已提交请到 OA 中核对，已提交的请勿重复提交。" />
  );
}
