import { useEffect, useRef, useState } from 'react';
import { logoutApiV1AuthLogoutPost } from '../generated/auth/auth';
import { useAuthStore } from '../stores/authStore';
import { notifyIdentityRecheck } from './identity';

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
    const expectedGeneration = useAuthStore.getState().generation;
    const isCurrent = () => live.current &&
      useAuthStore.getState().generation === expectedGeneration;
    setOperation({ generation: expectedGeneration, pending: true, failed: false });
    try {
      const result: unknown = await logoutApiV1AuthLogoutPost();
      if (
        typeof result !== 'object' || result === null ||
        !('authenticated' in result) || result.authenticated !== false ||
        Object.keys(result).length !== 1
      ) throw new Error('Logout response was not confirmed');
      if (!isCurrent()) return;
      setOperation(null);
      useAuthStore.getState().markUnauthenticated(expectedGeneration);
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
