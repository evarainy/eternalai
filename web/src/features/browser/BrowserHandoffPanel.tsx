/**
 * D handoff presentation (M3 §9). S6a placeholder: props and wiring points only; S6b fills in the
 * §9.1/§9.3/§9.4 business copy, the {HH:mm} deadline and the cleanup line.
 */

import { useState } from 'react';
import { Button } from 'antd';
import type { ParsedBrowserRunView } from '../../contracts/browserRunProjection';
import type { BrowserHandoff } from './api';

export interface BrowserHandoffPanelProps {
  /** The owner-checked run the handoff belongs to; `result`/`cleanup` drive the §9.1 rows. */
  readonly run: ParsedBrowserRunView;
  /**
   * null: the D run is not (yet) sealed. Rendered for every fill_stop card except an M2 historical
   * `completed` card without handoff, so S6b can show the §9.1 pre-seal rows here. The run card
   * keeps its own cancel button while this is null.
   */
  readonly handoff: BrowserHandoff | null;
  /**
   * "结束协助": the same cancel endpoint as the run card. Resolving means only that the request
   * was handed off; a rejection means it was not delivered. Acknowledgement comes from a later read.
   */
  readonly onEndAssist: () => Promise<void>;
}

/**
 * Sealed run: replaces the technical fill notice and the run card's own cancel button.
 * Pre-seal (handoff null): S6a placeholder renders nothing; the existing card presentation stays.
 */
export function BrowserHandoffPanel({ run, handoff, onEndAssist }: BrowserHandoffPanelProps) {
  const [ending, setEnding] = useState(false);
  if (handoff === null) return null;
  const canEnd = run.result === null && !run.cancel.requested;
  return (
    <section aria-label="报修协助">
      {canEnd ? (
        <Button loading={ending} disabled={ending} onClick={() => {
          setEnding(true);
          void onEndAssist().catch(() => undefined).finally(() => { setEnding(false); });
        }}>结束协助</Button>
      ) : null}
    </section>
  );
}

/** §9.3: shown above the input box while the current session has an active handoff. */
export function BrowserHandoffComposerNotice() {
  return null;
}

/** §9.4: status label for an M2 historical D `completed` run (no handoff). */
export const FILL_STOP_HISTORY_STATUS_LABEL = '已填写，提交情况未核实';

/** §9.4: business text that replaces the technical fill notice on M2 historical D cards. */
export function BrowserFillStopHistoryNotice() {
  return null;
}
