import type { IMConfiguration } from '../../generated/im-context/im-context.schemas';
import { useAuthStore } from '../../stores/authStore';
import { useAIDockStore } from '../../stores/aiDockStore';

type Selection = {
  realm_id: string; im_user_id: string; message_id: string; conversation_ref: string;
};
type SessionState = {
  nonce: string; status: 'authenticated' | 'unauthenticated' | 'failed';
  realm_id?: string; im_user_id?: string;
};
export type IMEvent = {
  protocol: 'eternalai.im-bridge'; version: 2; provider: string; im_instance_id: string;
} & (
  | { type: 'eternalai:message-selected'; payload: Selection }
  | { type: 'eternalai:navigate'; payload: { route: 'messages' | 'work-object' | 'delivery'; object_ref: string } }
  | { type: 'eternalai:logout-requested'; payload: Record<string, never> }
  | { type: 'eternalai:session-state'; payload: SessionState }
);
function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function exact(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => key in value);
}
function text(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0;
}

export function validateIMEvent(value: unknown, config: IMConfiguration): IMEvent | null {
  if (!object(value) || !exact(value, ['protocol', 'version', 'type', 'provider', 'im_instance_id', 'payload'])
      || value.protocol !== 'eternalai.im-bridge' || value.version !== 2
      || value.provider !== config.provider || value.im_instance_id !== config.im_instance_id
      || !text(value.provider) || !text(value.im_instance_id) || !object(value.payload)) return null;
  const p = value.payload;
  switch (value.type) {
    case 'eternalai:message-selected':
      if (!exact(p, ['realm_id', 'im_user_id', 'message_id', 'conversation_ref'])
          || !text(p.realm_id) || !text(p.im_user_id)
          || !text(p.message_id) || !text(p.conversation_ref)) return null;
      break;
    case 'eternalai:navigate':
      if (!exact(p, ['route', 'object_ref']) || (p.route !== 'messages' && p.route !== 'work-object' && p.route !== 'delivery')
          || !text(p.object_ref)) return null;
      break;
    case 'eternalai:logout-requested':
      if (!exact(p, [])) return null;
      break;
    case 'eternalai:session-state':
      if (!text(p.nonce)) return null;
      if (p.status === 'authenticated') {
        if (!exact(p, ['nonce', 'status', 'realm_id', 'im_user_id'])
            || !text(p.realm_id) || !text(p.im_user_id)) return null;
      } else if ((p.status !== 'unauthenticated' && p.status !== 'failed')
          || !exact(p, ['nonce', 'status'])) return null;
      break;
    default: return null;
  }
  return value as IMEvent;
}

export function safeIMLink(value: string, config: IMConfiguration): string {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.origin !== config.im_origin || url.username || url.password) {
    throw new Error('im_link_invalid');
  }
  return url.href;
}

let configuration: IMConfiguration | null = null;
let freezeCurrent: (() => void) | null = null;
let returnCurrent: ((link: string) => void) | null = null;
let loggingOut = false;
let bridgeBlocked = false;

export function configureIM(config: IMConfiguration): void {
  if (config.host_origin !== location.origin || new URL(config.im_origin).origin !== config.im_origin
      || !config.im_origin.startsWith('https:') || config.im_origin === location.origin) {
    throw new Error('im_configuration_invalid');
  }
  safeIMLink(config.helper_url, config);
  configuration = {
    provider: config.provider, im_instance_id: config.im_instance_id,
    im_origin: config.im_origin, host_origin: config.host_origin, helper_url: config.helper_url,
  };
}
export function registerIMView(freeze: () => void, returnTo: (link: string) => void): () => void {
  freezeCurrent = freeze;
  returnCurrent = returnTo;
  return () => {
    if (freezeCurrent === freeze) { freezeCurrent = null; returnCurrent = null; }
  };
}
export function returnToIMSource(link: string): void {
  if (configuration !== null && returnCurrent !== null && !loggingOut) {
    returnCurrent(safeIMLink(link, configuration));
  }
}
export function imLogoutInProgress(): boolean { return loggingOut; }
export function imBridgeBlocked(): boolean { return bridgeBlocked; }
export function blockIMBridge(): void { bridgeBlocked = true; }
export function hasIMConfiguration(): boolean { return configuration !== null; }
export function completeIMLogout(): void { loggingOut = false; bridgeBlocked = false; }
/** Only a server-confirmed host login may start a new IM identity cycle after a failure. */
export function beginIMLogin(): void { loggingOut = false; bridgeBlocked = false; }

export function imSessionOperation(
  config: IMConfiguration, type: 'eternalai:session-check' | 'eternalai:session-logout',
  isCurrent: () => boolean,
  signal?: AbortSignal,
): Promise<SessionState> {
  return new Promise((resolve, reject) => {
    const frame = document.createElement('iframe');
    frame.hidden = true;
    frame.title = '消息身份核验';
    frame.src = safeIMLink(config.helper_url, config);
    const nonce = crypto.randomUUID();
    let settled = false;
    const finish = (state?: SessionState) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      unsubscribe();
      signal?.removeEventListener('abort', abort);
      window.removeEventListener('message', receive);
      frame.onload = null;
      frame.remove();
      if (!isCurrent() || state === undefined || state.status === 'failed') reject(new Error('im_session_unconfirmed'));
      else resolve(state);
    };
    const abort = () => finish();
    const unsubscribe = useAuthStore.subscribe(() => { if (!isCurrent()) finish(); });
    const receive = (event: MessageEvent<unknown>) => {
      if (!isCurrent() || event.origin !== config.im_origin || event.source !== frame.contentWindow) return;
      const data = validateIMEvent(event.data, config);
      if (data?.type === 'eternalai:session-state' && data.payload.nonce === nonce) finish(data.payload);
    };
    const timer = window.setTimeout(() => finish(), 15000);
    frame.onload = () => {
      if (!isCurrent()) { finish(); return; }
      frame.contentWindow?.postMessage({
        protocol: 'eternalai.im-bridge', version: 2, type,
        provider: config.provider, im_instance_id: config.im_instance_id, payload: { nonce },
      }, config.im_origin);
    };
    window.addEventListener('message', receive);
    signal?.addEventListener('abort', abort, { once: true });
    document.body.append(frame);
  });
}

/** Freeze and invalidate before starting a fresh, correlated IM logout operation. */
export async function logoutIM(config: IMConfiguration): Promise<number> {
  configureIM(config);
  loggingOut = true;
  freezeCurrent?.();
  useAIDockStore.getState().clearSession();
  useAuthStore.getState().invalidateIdentity(useAuthStore.getState().status);
  const generation = useAuthStore.getState().generation;
  const current = () => useAuthStore.getState().generation === generation;
  const state = await imSessionOperation(config, 'eternalai:session-logout', current);
  if (!current() || state.status !== 'unauthenticated') throw new Error('im_logout_unconfirmed');
  return generation;
}
