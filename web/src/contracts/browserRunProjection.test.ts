import { describe, expect, it } from 'vitest';
import {
  compareBrowserRunUpdate,
  isParsedBrowserRunView,
  parseBrowserRunProjection,
  type ParsedBrowserRunView,
} from './browserRunProjection';

const DIGEST = 'a'.repeat(64);

function running() {
  return {
    schema_version: 'browser.run.v1',
    task_id: 'task_1',
    run_id: 'run_1',
    state_revision: 2,
    status: 'running',
    progress: { phase: 'running', completed_steps: 1, total_steps: 3 },
    cancel: { requested: false, acknowledged: false },
    result: null,
    artifacts: [],
    draft: null,
  };
}

function completed() {
  return {
    ...running(),
    state_revision: 4,
    status: 'completed',
    progress: null,
    result: {
      business: 'completed',
      effect: 'acknowledged',
      verification: 'verified',
      cleanup: 'pending',
      error_code: null,
      dispatch_failure_code: null,
      terminal_revision: 4,
      automatic_replay: false,
    },
  };
}

function failedUnknown() {
  return {
    ...completed(),
    status: 'failed',
    result: {
      ...completed().result,
      business: 'failed',
      effect: 'unknown',
      verification: 'incomplete',
      error_code: 'browser_effect_unknown',
    },
  };
}

function artifact() {
  return {
    artifact_id: 'artifact_1',
    kind: 'report',
    media_type: 'application/pdf',
    size_bytes: 1024,
    expires_at: '2026-10-03T12:00:00+08:00',
    availability: 'available',
  };
}

function draft() {
  return {
    draft_id: 'draft_1',
    draft_revision: 1,
    base_revision: 0,
    base_publication_digest: DIGEST,
    draft_digest: DIGEST,
    state: 'validated',
    rejection: null,
    parameter_names: ['business_key'],
    executable: false,
  };
}

function parsed(raw: unknown): ParsedBrowserRunView {
  const view = parseBrowserRunProjection(raw);
  expect(view).not.toBeNull();
  if (view === null) throw new Error('synthetic_view_invalid');
  return view;
}

const target = {
  taskId: 'task_1', runId: 'run_1', requestGeneration: 3, currentGeneration: 3,
};

describe('unwired browser run projection', () => {
  it('accepts only a safe copy of a coherent snapshot', () => {
    const input = running();
    const view = parsed(input);
    input.progress.completed_steps = 2;
    expect(view.progress?.completed_steps).toBe(1);
    expect(Object.isFrozen(view)).toBe(true);
    expect(Object.isFrozen(view.progress)).toBe(true);
    expect(isParsedBrowserRunView(input)).toBe(false);
    expect(isParsedBrowserRunView(view)).toBe(true);
    expect(Object.keys(view)).toEqual([
      'schema_version', 'task_id', 'run_id', 'state_revision', 'status',
      'progress', 'cancel', 'result', 'artifacts', 'draft',
    ]);
  });

  it.each([
    { ...running(), schema_version: 'browser.run.v2' },
    { ...running(), raw_dom: '<synthetic>' },
    { ...running(), href: 'https://synthetic.invalid' },
    { ...running(), task_id: 'bad/id' },
    { ...running(), state_revision: Number.MAX_SAFE_INTEGER + 1 },
    { ...running(), state_revision: 1.5 },
    { ...running(), progress: { ...running().progress, value: 'synthetic' } },
    { ...running(), progress: { phase: 'running', completed_steps: 4, total_steps: 3 } },
    { ...running(), progress: null },
    { ...running(), cancel: { requested: false, acknowledged: true } },
    { ...running(), status: 'waiting_user' },
    { ...running(), status: 'cancelled' },
    { ...running(), artifacts: Array.from({ length: 65 }, artifact) },
  ])('rejects incompatible root, progress, cancel or size boundary %#', (input) => {
    expect(parseBrowserRunProjection(input)).toBeNull();
  });

  it('rejects accessor objects and prototype-bearing inputs', () => {
    const getter = Object.defineProperty(running(), 'status', {
      get: () => 'running', enumerable: true,
    });
    expect(parseBrowserRunProjection(getter)).toBeNull();
    expect(parseBrowserRunProjection(new Date())).toBeNull();
  });

  it.each([
    { ...completed(), result: { ...completed().result, automatic_replay: true } },
    { ...completed(), result: { ...completed().result, verification: 'incomplete' } },
    { ...completed(), result: { ...completed().result, error_code: 'browser_not_sent' } },
    { ...completed(), result: { ...completed().result, terminal_revision: 5 } },
    { ...failedUnknown(), result: { ...failedUnknown().result, error_code: null } },
    { ...failedUnknown(), result: { ...failedUnknown().result, effect: 'unknown', error_code: 'browser_not_sent' } },
    { ...completed(), result: { ...completed().result, value: 'synthetic_private' } },
    { ...completed(), result: { ...completed().result, business: 'invented' } },
  ])('rejects inconsistent or expanded terminal result %#', (input) => {
    expect(parseBrowserRunProjection(input)).toBeNull();
  });

  it('accepts unknown effects only as explicit non-replay failures', () => {
    const view = parsed(failedUnknown());
    expect(view.result?.effect).toBe('unknown');
    expect(view.result?.automatic_replay).toBe(false);
  });

  it.each([
    { ...artifact(), expires_at: '2026-10-03T12:00:00' },
    { ...artifact(), expires_at: '2026-02-30T12:00:00Z' },
    { ...artifact(), expires_at: '2026-10-03T12:00:00+25:00' },
    { ...artifact(), size_bytes: Number.MAX_SAFE_INTEGER + 1 },
    { ...artifact(), url: 'https://synthetic.invalid' },
    { ...artifact(), kind: 'script' },
  ])('rejects unsafe or unbounded artifact metadata %#', (item) => {
    expect(parseBrowserRunProjection({ ...running(), artifacts: [item] })).toBeNull();
  });

  it('rejects duplicate artifacts and private draft expansion', () => {
    expect(parseBrowserRunProjection({
      ...running(), artifacts: [artifact(), artifact()],
    })).toBeNull();
    for (const badDraft of [
      { ...draft(), executable: true },
      { ...draft(), value: 'synthetic_private' },
      { ...draft(), parameter_names: ['business_key', 'business_key'] },
      { ...draft(), state: 'rejected', rejection: null },
      { ...draft(), draft_revision: 0 },
    ]) {
      expect(parseBrowserRunProjection({ ...running(), draft: badDraft })).toBeNull();
    }
    const view = parsed({ ...running(), artifacts: [artifact()], draft: draft() });
    expect(view.draft?.state).toBe('validated');
    expect(view.draft?.executable).toBe(false);
    expect(Object.keys(view.artifacts[0] ?? {})).not.toContain('url');
  });

  it('orders by local generation, exact Task/Run and revision', () => {
    const current = parsed(running());
    expect(compareBrowserRunUpdate(null, current, target)).toBe('apply');
    expect(compareBrowserRunUpdate(current, parsed(running()), target)).toBe('noop');
    expect(compareBrowserRunUpdate(current, parsed({
      ...running(), progress: { ...running().progress, completed_steps: 2 },
    }), target)).toBe('conflict');
    expect(compareBrowserRunUpdate(current, parsed({
      ...running(), state_revision: 1,
    }), target)).toBe('drop');
    expect(compareBrowserRunUpdate(current, parsed({
      ...running(), state_revision: 3,
      progress: { ...running().progress, completed_steps: 2 },
    }), target)).toBe('apply');
    expect(compareBrowserRunUpdate(current, parsed(running()), {
      ...target, currentGeneration: 4,
    })).toBe('drop');
    expect(compareBrowserRunUpdate(current, parsed({
      ...running(), task_id: 'task_2', run_id: 'run_2',
    }), target)).toBe('drop');
    expect(compareBrowserRunUpdate(parsed({ ...running(), task_id: 'task_2' }),
      current, target)).toBe('conflict');
  });

  it('keeps terminal facts immutable while allowing later cleanup evidence', () => {
    const current = parsed(completed());
    expect(compareBrowserRunUpdate(current, parsed({
      ...completed(), state_revision: 5,
      result: { ...completed().result, cleanup: 'failed' },
    }), target)).toBe('apply');
    expect(compareBrowserRunUpdate(current, parsed({
      ...running(), state_revision: 5,
    }), target)).toBe('drop');
    expect(compareBrowserRunUpdate(current, parsed({
      ...completed(), state_revision: 5,
      result: { ...completed().result, terminal_revision: 5 },
    }), target)).toBe('conflict');
    expect(compareBrowserRunUpdate(current, parsed({
      ...completed(), state_revision: 5,
      result: { ...completed().result, effect: 'unknown' },
    }), target)).toBe('conflict');
  });

  it('does not lose cancellation or progress facts at a higher revision', () => {
    const requested = parsed({
      ...running(), cancel: { requested: true, acknowledged: false },
    });
    expect(compareBrowserRunUpdate(requested, parsed({
      ...running(), state_revision: 3,
    }), target)).toBe('conflict');
    const counted = parsed({ ...running(), state_revision: 3,
      progress: { phase: 'running', completed_steps: 2, total_steps: 3 } });
    expect(compareBrowserRunUpdate(counted, parsed({
      ...running(), state_revision: 4,
    }), target)).toBe('conflict');
  });

  it('rejects invalid local generation and unparsed values', () => {
    const view = parsed(running());
    expect(() => compareBrowserRunUpdate(null, view, {
      ...target, requestGeneration: Number.MAX_SAFE_INTEGER + 1,
    })).toThrow('browser_client_generation_invalid');
    expect(() => compareBrowserRunUpdate(null, running() as unknown as ParsedBrowserRunView,
      target)).toThrow('browser_view_not_parsed');
  });
});
