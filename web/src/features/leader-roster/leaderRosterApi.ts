import { customInstance } from '../../api/mutator';

export type LeaderRole = 'unit_leader' | 'supervising_leader';
export type DirectoryStatus = 'found' | 'missing' | 'ambiguous' | 'unavailable';

export interface LeaderRosterEntry {
  entry_id: string;
  role: LeaderRole;
  directory_user_id: string;
  active: boolean;
  directory_status: DirectoryStatus;
  display_name: string | null;
  department_display_name: string | null;
  created_by_ai_user_id: string;
  created_at: string;
  revoked_by_ai_user_id: string | null;
  revoked_at: string | null;
}

export interface LeaderRosterList {
  items: LeaderRosterEntry[];
  directory_unavailable_code: string | null;
}

export interface LeaderRosterCandidate {
  directory_user_id: string;
  display_name: string;
  department_id: string;
  department_display_name: string;
}

export interface LeaderRosterCandidates {
  items: LeaderRosterCandidate[];
  has_more: boolean;
}

export interface LeaderRosterRevocation {
  entry_id: string;
  role: LeaderRole;
  revoked_at: string;
  changed: boolean;
}

export const LEADER_ROLE_LABELS: Record<LeaderRole, string> = {
  unit_leader: '单位领导',
  supervising_leader: '分管领导',
};

/** The navigation entry and the page share this key, so one read serves both. */
export const leaderRosterQueryKey = (generation: number) => ['admin', 'leader-roster', generation] as const;

export const readLeaderRoster = () => customInstance<LeaderRosterList>({
  url: '/api/v1/admin/leader-roster', method: 'GET',
});

export const readLeaderRosterCandidates = (query: string) => customInstance<LeaderRosterCandidates>({
  url: '/api/v1/admin/leader-roster/candidates', method: 'GET', params: { query, limit: 50 },
});

export const addLeaderRosterEntry = (role: LeaderRole, directoryUserId: string) => customInstance<LeaderRosterEntry>({
  url: '/api/v1/admin/leader-roster', method: 'POST', data: { role, directory_user_id: directoryUserId },
});

export const revokeLeaderRosterEntry = (entryId: string) => customInstance<LeaderRosterRevocation>({
  url: `/api/v1/admin/leader-roster/${encodeURIComponent(entryId)}/revoke`, method: 'POST',
});
