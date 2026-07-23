import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

interface AdminOpenApiDocument {
  paths: Record<
    string,
    {
      get?: {
        operationId: string;
        responses: Record<
          string,
          { $ref?: string; content?: { 'application/json': { schema: { $ref: string } } } }
        >;
      };
      post?: {
        responses: Record<
          string,
          { content?: { 'application/json': { schema: { $ref: string } } } }
        >;
      };
    }
  >;
  components: {
    responses: Record<
      string,
      { content: { 'application/json': { schema: { $ref: string } } } }
    >;
    schemas: {
      FastApiValidationErrorResponse: {
        properties: { detail: { type: string } };
      };
      TaskStatus: { enum: string[] };
      TargetSystem: { enum: string[] };
      ExecutionIdentity: { enum: string[] };
      IdentityBindStatus: { enum: string[] };
      AdminTaskEventEvidence: {
        additionalProperties: boolean;
        required?: string[];
        properties: Record<string, unknown>;
      };
    };
  };
}

function loadSpec(): AdminOpenApiDocument {
  return JSON.parse(
    readFileSync(resolve(process.cwd(), 'openapi/admin.openapi.json'), 'utf8'),
  ) as AdminOpenApiDocument;
}

function requiredPath(spec: AdminOpenApiDocument, path: string) {
  const pathItem = spec.paths[path];
  if (!pathItem) {
    throw new Error(`Missing OpenAPI path: ${path}`);
  }
  return pathItem;
}

function requiredResponse(spec: AdminOpenApiDocument, responseName: string) {
  const response = spec.components.responses[responseName];
  if (!response) {
    throw new Error(`Missing OpenAPI response: ${responseName}`);
  }
  return response;
}

describe('Admin Registry curated OpenAPI', () => {
  it('declares create validation failures with the FastAPI array envelope', () => {
    const spec = loadSpec();
    const validationResponse = requiredPath(spec, '/api/v1/admin/registry').post?.responses[
      '422'
    ];

    expect(validationResponse?.content?.['application/json'].schema.$ref).toBe(
      '#/components/schemas/FastApiValidationErrorResponse',
    );
    expect(
      spec.components.schemas.FastApiValidationErrorResponse.properties.detail.type,
    ).toBe('array');
  });

  it('adds only the three 005b read paths with stable operation IDs', () => {
    const spec = loadSpec();
    const addedPaths = Object.keys(spec.paths).filter(
      (path) => !path.startsWith('/api/v1/admin/registry'),
    );

    expect(addedPaths).toEqual([
      '/api/v1/admin/tasks',
      '/api/v1/admin/tasks/{task_id}/events',
      '/api/v1/admin/bindings',
    ]);
    expect(requiredPath(spec, '/api/v1/admin/tasks').get?.operationId).toBe('listTasks');
    expect(requiredPath(spec, '/api/v1/admin/tasks/{task_id}/events').get?.operationId).toBe(
      'listTaskEvents',
    );
    expect(requiredPath(spec, '/api/v1/admin/bindings').get?.operationId).toBe(
      'listBindings',
    );
  });

  it('uses AdminErrorResponse for the service-owned Task and Binding errors', () => {
    const spec = loadSpec();
    const taskResponses = requiredPath(spec, '/api/v1/admin/tasks').get?.responses;
    const eventResponses = requiredPath(spec, '/api/v1/admin/tasks/{task_id}/events').get
      ?.responses;
    const bindingResponses = requiredPath(spec, '/api/v1/admin/bindings').get?.responses;

    expect(taskResponses?.['422']?.$ref).toBe('#/components/responses/TaskFilterRequired');
    expect(eventResponses?.['404']?.$ref).toBe('#/components/responses/TaskNotFound');
    expect(bindingResponses?.['422']?.$ref).toBe(
      '#/components/responses/BindingQueryInvalid',
    );
    expect(taskResponses?.['403']?.$ref).toBe('#/components/responses/RoleNotAllowed');
    expect(eventResponses?.['503']?.$ref).toBe(
      '#/components/responses/RegistryUnavailable',
    );
    expect(bindingResponses?.['403']?.$ref).toBe('#/components/responses/RoleNotAllowed');

    for (const responseName of [
      'TaskFilterRequired',
      'TaskNotFound',
      'BindingQueryInvalid',
    ]) {
      expect(
        requiredResponse(spec, responseName).content['application/json'].schema.$ref,
      ).toBe('#/components/schemas/AdminErrorResponse');
    }
  });

  it('mirrors the backend enums and the exact optional evidence allowlist', () => {
    const spec = loadSpec();

    expect(spec.components.schemas.TaskStatus.enum).toEqual([
      'created',
      'running',
      'waiting_user',
      'completed',
      'failed',
      'no_capability_found',
    ]);
    expect(spec.components.schemas.TargetSystem.enum).toEqual(['oa', 'u8', 'hikvision_ivms']);
    expect(spec.components.schemas.ExecutionIdentity.enum).toEqual([
      'user_delegated',
      'system_scope',
      'admin_approved_proxy',
    ]);
    expect(spec.components.schemas.IdentityBindStatus.enum).toEqual([
      'active',
      'unbound',
      'expired',
      'revoked',
      'verification_failed',
      'needs_binding_scope',
    ]);

    const evidence = spec.components.schemas.AdminTaskEventEvidence;
    expect(evidence.additionalProperties).toBe(false);
    expect(evidence.required).toBeUndefined();
    expect(Object.keys(evidence.properties)).toEqual([
      'capability_id',
      'selection_rule',
      'workflow_id',
      'workflow_version',
      'workflow_status',
      'error_code',
      'step_id',
      'step_index',
      'step_status',
      'attempt',
      'retry_number',
      'max_attempts',
      'waiting_step_id',
      'waiting_step_index',
      'confirmed_capability_id',
      'completed_step_ids',
      'step_output_keys',
      'recovery_input_keys',
    ]);
  });
});
