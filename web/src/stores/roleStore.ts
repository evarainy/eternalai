import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface RoleState {
  roles: string[];
  setRoles: (roles: string[]) => void;
}

export const useRoleStore = create<RoleState>()(
  persist(
    (set) => ({
      roles: [],
      setRoles: (roles) => set({ roles }),
    }),
    { name: 'eternalai-admin-roles' },
  ),
);
