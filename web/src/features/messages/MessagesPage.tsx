import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Alert from 'antd/es/alert';
import {
  configurationApiV1ImContextConfigurationGet, loginApiV1ImContextLoginPost,
  selectApiV1ImContextSourcesPost,
} from '../../generated/im-context/im-context';
import { useAuthStore } from '../../stores/authStore';
import { useAIDockStore } from '../../stores/aiDockStore';
import { PlaceholderPage } from '../../shared/ui/PlaceholderPage';
import { useLogout } from '../../app/logout';
import { withAuthCookieLock } from '../../app/sessionKeepalive';
import {
  blockIMBridge, configureIM, imBridgeBlocked, imLogoutInProgress, imSessionOperation, registerIMView,
  safeIMLink, validateIMEvent,
} from './hostBridge';

export default function MessagesPage() {
  const status = useAuthStore((state) => state.status);
  const generation = useAuthStore((state) => state.generation);
  const frame = useRef<HTMLIFrameElement>(null);
  const [phase, setPhase] = useState<'loading' | 'ready' | 'failed' | 'disabled'>('loading');
  const [phaseGeneration, setPhaseGeneration] = useState(generation);
  const [sourceError, setSourceError] = useState(false);
  const navigate = useNavigate();
  const { logout } = useLogout();
  const logoutRef = useRef(logout);
  logoutRef.current = logout;

  useEffect(() => {
    if (status !== 'authenticated' || imLogoutInProgress() || imBridgeBlocked()) return;
    let live = true;
    const abort = new AbortController();
    const full = frame.current;
    if (full === null) return;
    const sourceWindow = full.contentWindow;
    full.hidden = true;
    let identityReady = false;
    let selectionSequence = 0;
    let removeReceiver = () => {};
    const isCurrent = () => live && useAuthStore.getState().generation === generation
      && useAuthStore.getState().status === 'authenticated' && !imLogoutInProgress();
    const freeze = () => {
      if (!live) return;
      live = false;
      identityReady = false;
      removeReceiver();
      useAIDockStore.getState().clearSession();
      full.hidden = true;
      setPhase('failed');
    };
    const failIdentity = () => {
      if (!live) return;
      freeze();
      blockIMBridge();
      useAuthStore.getState().invalidateIdentity(useAuthStore.getState().status);
    };
    setPhase('loading');
    setPhaseGeneration(generation);
    useAIDockStore.getState().setIMSource(null);
    const unregister = registerIMView(freeze, (link) => {
      if (isCurrent() && identityReady) {
        full.src = link;
        full.hidden = false;
        useAIDockStore.getState().closeDock();
      }
    });
    const start = async () => {
      try {
        await withAuthCookieLock(async () => {
          if (!isCurrent()) return;
          const fixed = await configurationApiV1ImContextConfigurationGet();
          if (!isCurrent()) return;
          if (fixed === null) { setPhase('disabled'); return; }
          configureIM(fixed);
          const config = await loginApiV1ImContextLoginPost();
          if (!isCurrent()) return;
          const old = await imSessionOperation(config, 'eternalai:session-logout', isCurrent, abort.signal);
          if (!isCurrent()) return;
          if (old.status !== 'unauthenticated') throw new Error('im_logout_unconfirmed');
          const receive = async (event: MessageEvent<unknown>) => {
            if (!isCurrent() || event.origin !== config.im_origin || event.source !== sourceWindow) return;
            const data = validateIMEvent(event.data, config);
            if (data === null) return;
            if (data.type === 'eternalai:logout-requested') { void logoutRef.current(); return; }
            if (full.hidden) return;
            if (data.type === 'eternalai:navigate') {
              navigate(data.payload.route === 'messages' ? '/messages'
                : `/work-objects/${encodeURIComponent(data.payload.object_ref)}`);
            } else if (data.type === 'eternalai:message-selected') {
              if (data.payload.realm_id !== config.realm_id || data.payload.im_user_id !== config.im_user_id) {
                failIdentity(); return;
              }
              const sessionId = useAIDockStore.getState().ensureSession();
              const sequence = ++selectionSequence;
              setSourceError(false);
              try {
                const source = await selectApiV1ImContextSourcesPost({
                  ...data.payload, provider: config.provider, im_instance_id: config.im_instance_id,
                  session_id: sessionId,
                });
                if (!isCurrent() || sequence !== selectionSequence
                    || useAIDockStore.getState().sessionId !== sessionId) return;
                safeIMLink(source.deep_link, config);
                useAIDockStore.getState().setIMSource({ source, authGeneration: generation, sessionId });
                useAIDockStore.getState().openDock();
              } catch {
                if (isCurrent() && sequence === selectionSequence) {
                  useAIDockStore.getState().setIMSource(null);
                  setSourceError(true);
                }
              }
            }
          };
          window.addEventListener('message', receive);
          removeReceiver = () => window.removeEventListener('message', receive);
          if (!isCurrent()) return;
          if (Date.parse(config.expires_at) <= Date.now()) throw new Error('im_login_expired');
          const form = document.createElement('form');
          form.method = 'POST';
          form.action = safeIMLink(config.action_url, config);
          form.target = full.name;
          form.hidden = true;
          for (const [name, value] of Object.entries(config.fields)) {
            const input = document.createElement('input');
            input.type = 'hidden'; input.name = name; input.value = value; form.append(input);
          }
          document.body.append(form);
          // Keep the cookie lock until the native SSO load and its real identity receipt finish.
          await new Promise<void>((resolve, reject) => {
            const stop = () => finish(new Error('im_login_unconfirmed'));
            const timer = window.setTimeout(stop, 15000);
            const finish = (error?: unknown) => {
              window.clearTimeout(timer);
              abort.signal.removeEventListener('abort', stop);
              if (error !== undefined) reject(error);
              else resolve();
            };
            abort.signal.addEventListener('abort', stop, { once: true });
            full.onload = async () => {
              if (!isCurrent()) { stop(); return; }
              identityReady = false;
              full.hidden = true;
              try {
                const identity = await imSessionOperation(config, 'eternalai:session-check', isCurrent, abort.signal);
                if (!isCurrent()) { stop(); return; }
                if (identity.status !== 'authenticated' || identity.realm_id !== config.realm_id
                    || identity.im_user_id !== config.im_user_id) throw new Error('im_identity_mismatch');
                identityReady = true;
                full.hidden = false;
                setPhase('ready');
                finish();
              } catch (error) {
                finish(error);
                if (isCurrent()) failIdentity();
              }
            };
            form.submit();
            form.remove();
          });
        });
      } catch {
        if (isCurrent()) failIdentity();
      }
    };
    void start();
    return () => {
      live = false;
      abort.abort();
      full.hidden = true;
      full.onload = null;
      removeReceiver();
      unregister();
      useAIDockStore.getState().setIMSource(null);
    };
  }, [generation, status, navigate]);

  const placeholder = (
    <PlaceholderPage
      title="消息"
      icon="mail"
      reason="消息尚未启用，或当前登录状态还未确认。"
      nextStep="OA 里的通知和待办，请照原来的方式直接去 OA 查看。"
      actions={[{ label: '去工作事项', to: '/work-objects' }]}
    />
  );
  if (status !== 'authenticated') return placeholder;
  return <div>
    {phase === 'disabled' ? placeholder : <h1>消息</h1>}
    {phase === 'loading' && <p role="status">正在核验消息账号，请稍候。</p>}
    {phase === 'failed' && <Alert type="error" showIcon message="消息身份或来源未能确认，请重新登录后重试。" />}
    {sourceError && <Alert type="error" showIcon message="无法读取这条消息，请重新选择你有权查看的消息。" />}
    {!imBridgeBlocked() && (phase !== 'failed' || phaseGeneration !== generation) && <iframe key={generation} ref={frame}
      name={`eternalai-im-${generation}`} title="单位消息" hidden={phase !== 'ready' || phaseGeneration !== generation}
      style={{ width: '100%', height: 'calc(100vh - 180px)', border: 0 }} />}
  </div>;
}
