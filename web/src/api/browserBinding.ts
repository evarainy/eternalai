import { customInstance } from './mutator';

export interface BrowserBindingStatus {
  readonly target_system: 'oa';
  readonly binding_id: string | null;
  readonly binding_revision: number | null;
  readonly binding_state: 'unverified' | 'active' | 'unbound' | 'revoked';
  readonly verified_at: string | null;
}

export interface BrowserBindingSelection {
  readonly binding_id: string;
  readonly binding_revision: number;
}

function parseStatus(value: unknown): BrowserBindingStatus {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('browser_binding_status_invalid');
  }
  const data = value as Record<string, unknown>;
  const id = data.binding_id;
  const revision = data.binding_revision;
  const verified = data.verified_at;
  const state = data.binding_state;
  if (data.target_system !== 'oa'
      || !(id === null || typeof id === 'string' && /^[A-Za-z0-9_-]{1,96}$/.test(id))
      || !(revision === null || typeof revision === 'number' && Number.isSafeInteger(revision)
        && revision >= 1)
      || !(verified === null || typeof verified === 'string' && verified.length <= 64
        && Number.isFinite(Date.parse(verified)))
      || !(state === 'active' || state === 'unverified' || state === 'unbound' || state === 'revoked')
      || (id === null) !== (revision === null)
      || state === 'active' && (id === null || revision === null || verified === null)) {
    throw new Error('browser_binding_status_invalid');
  }
  return { target_system: 'oa', binding_id: id, binding_revision: revision,
    binding_state: state, verified_at: verified };
}

export async function getBrowserBindingStatus(signal?: AbortSignal): Promise<BrowserBindingStatus> {
  return parseStatus(await customInstance<unknown>({
    url: '/api/v1/credential-bindings/oa/browser-activation', method: 'GET', signal,
  }));
}

export async function activateBrowserBinding(
  selection: BrowserBindingSelection,
  credential: { login_id: string; password: string },
): Promise<BrowserBindingStatus> {
  if (!Number.isSafeInteger(selection.binding_revision) || selection.binding_revision < 1
      || selection.binding_revision >= Number.MAX_SAFE_INTEGER) {
    throw new Error('browser_binding_activation_unconfirmed');
  }
  const result = parseStatus(await customInstance<unknown>({
    url: '/api/v1/credential-bindings/oa/browser-activation', method: 'POST',
    data: { ...selection, login_id: credential.login_id, password: credential.password },
  }));
  if (result.binding_state !== 'active' || result.binding_id !== selection.binding_id
      || result.binding_revision !== selection.binding_revision + 1) {
    throw new Error('browser_binding_activation_unconfirmed');
  }
  return result;
}
