import { useEffect } from 'react';
import type { PageContextDeclaration } from '../contracts/pageContext';
import { useAIDockStore } from '../stores/aiDockStore';

export function usePageContextRegistration(
  declaration: PageContextDeclaration,
) {
  const clearPageContext = useAIDockStore((state) => state.clearPageContext);
  const registerPageContext = useAIDockStore(
    (state) => state.registerPageContext,
  );
  const surfaceId = declaration.surface_id;

  useEffect(() => {
    registerPageContext(declaration);
  }, [declaration, registerPageContext]);

  useEffect(
    () => () => {
      clearPageContext(surfaceId);
    },
    [clearPageContext, surfaceId],
  );
}
