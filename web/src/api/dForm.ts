import { customInstance } from './mutator';

export type DFormOperation =
  | 'confirm_handoff' | 'prepare' | 'validate' | 'request_review' | 'confirm_review' | 'activate'
  | 'close_owned_browser';
export type DFormChoice = 'installed_binding' | 'recorded_resource';
export type DFormGap =
  | 'd_form_identity_missing' | 'd_form_identity_invalid' | 'd_form_key_reference_missing'
  | 'd_form_pool_missing' | 'd_form_pool_mismatch' | 'd_form_authorization_missing'
  | 'd_form_reference_missing' | 'd_form_binding_unavailable' | 'd_form_binding_unverified'
  | 'd_form_subject_proof_unavailable' | 'd_form_handoff_pending'
  | 'd_form_publication_pending' | 'd_form_review_pending' | 'd_form_authorization_denied'
  | 'd_form_resource_scope_denied' | 'd_form_subject_mismatch'
  | 'd_form_owned_deployment_missing' | 'd_form_login_contract_missing';

export interface DFormHandoffView {
  version_token: string;
  resource_version: number;
  task_id: string;
  run_id: string;
}

export interface DFormState {
  structure_valid: boolean;
  execution_ready: false;
  fixed_activation: boolean;
  gaps: DFormGap[];
  choices: DFormChoice[];
  available_operations: DFormOperation[];
  publication: 'unavailable' | 'missing' | 'prepared' | 'active' | 'inactive';
  task_id: string | null;
  run_id: string | null;
  request_id: string | null;
  handoff_preview: DFormHandoffView | null;
}

export interface DFormCommand {
  operation: DFormOperation;
  selection: DFormChoice;
  session_id?: string;
  task_id?: string;
  run_id?: string;
  request_id?: string;
  parameters?: { category: string; position: string; description: string };
  handoff_confirmation?: DFormHandoffView;
}

export const getDFormState = () => customInstance<DFormState>({
  url: '/api/v1/admin/d-form', method: 'GET',
});

export const performDFormAction = (data: DFormCommand) => customInstance<DFormState>({
  url: '/api/v1/admin/d-form/actions', method: 'POST', data,
});
