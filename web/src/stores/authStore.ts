import { create } from 'zustand';

type AuthenticationStatus = 'authenticated' | 'unauthenticated';

interface AuthenticationState {
  generation: number;
  status: AuthenticationStatus;
  markAuthenticated: () => void;
  markUnauthenticated: (expectedGeneration?: number) => void;
}

export const useAuthStore = create<AuthenticationState>((set) => ({
  generation: 0,
  status: 'unauthenticated',
  markAuthenticated: () =>
    set((state) => ({
      generation: state.generation + 1,
      status: 'authenticated',
    })),
  markUnauthenticated: (expectedGeneration) =>
    set((state) => {
      if (
        (expectedGeneration !== undefined &&
          expectedGeneration !== state.generation) ||
        state.status === 'unauthenticated'
      ) {
        return state;
      }
      return {
        generation: state.generation + 1,
        status: 'unauthenticated',
      };
    }),
}));
