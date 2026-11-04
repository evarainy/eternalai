import { useEffect, useRef, useState } from 'react';
import { logoutApiV1AuthLogoutPost } from '../generated/auth/auth';
import { configurationApiV1ImContextConfigurationGet } from '../generated/im-context/im-context';
import { useAuthStore } from '../stores/authStore';
import { notifyIdentityRecheck } from './identity';
import { discardReauthSnapshot } from './reauthRestore';
import { withAuthCookieLock } from './sessionKeepalive';
import { completeIMLogout, logoutIM } from '../features/messages/hostBridge';
import { customInstance, ApiError } from '../api/mutator';

export function useLogout() {
  const generation = useAuthStore((state) => state.generation);
  const busy = useRef(false);
  const live = useRef(true);
  const [operation, setOperation] = useState<{
    generation: number; pending: boolean; failed: boolean;
  } | null>(null);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; };
  }, []);

  const logout = async () => {
    if (busy.current) return;
    busy.current = true;
    let expectedGeneration = useAuthStore.getState().generation;
    const isCurrent = () => live.current &&
      useAuthStore.getState().generation === expectedGeneration;
    setOperation({ generation: expectedGeneration, pending: true, failed: false });
    try {
      const config = await configurationApiV1ImContextConfigurationGet();
      if (!isCurrent()) return;
      const withIM = config !== null;
      if (withIM) {
        const departure = logoutIM(config);
        expectedGeneration = useAuthStore.getState().generation;
        setOperation({ generation: expectedGeneration, pending: true, failed: false });
        await departure;
        if (!isCurrent()) return;
      }
      const result: unknown = await withAuthCookieLock(() => logoutApiV1AuthLogoutPost());
      if (
        typeof result !== 'object' || result === null ||
        !('authenticated' in result) || result.authenticated !== false ||
        Object.keys(result).length !== 1
      ) throw new Error('Logout response was not confirmed');
      if (!isCurrent()) return;
      // Query actual host state; a local clear or logout intent is not proof of logout.
      if (withIM) {
        const beforeCheck = useAuthStore.getState();
        try {
          await customInstance({ url: '/api/v1/auth/session', method: 'GET' });
          throw new Error('Host is still authenticated');
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 401) throw error;
        }
        const afterCheck = useAuthStore.getState();
        const expected = beforeCheck.generation + (beforeCheck.status === 'unauthenticated' ? 0 : 1);
        if (afterCheck.generation !== expected || afterCheck.status !== 'unauthenticated') return;
        expectedGeneration = expected;
      }
      setOperation(null);
      // A deliberate logout never leaves a restore copy behind in this tab.
      discardReauthSnapshot('logged_out');
      useAuthStore.getState().markUnauthenticated(expectedGeneration);
      completeIMLogout();
      notifyIdentityRecheck();
    } catch {
      if (isCurrent()) {
        setOperation({ generation: expectedGeneration, pending: false, failed: true });
      }
    } finally {
      busy.current = false;
    }
  };

  return {
    logout,
    pending: operation?.generation === generation && operation.pending,
    failed: operation?.generation === generation && operation.failed,
  };
}
