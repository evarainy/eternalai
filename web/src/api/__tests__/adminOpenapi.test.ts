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
        operationId?: string;
        parameters?: Array<{ $ref: string }>;
        responses: Record<
          string,
          {
            $ref?: string;
            content?: { 'application/json': { schema: { $ref: string } } };
          }
        >;
      };
    }
  >;
  components: {
    parameters: {
      BindingId: { name: string; in: string; required: boolean };
    };
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
      AdminCapabilityCreate: {
        properties: {
          name: { minLength: number; maxLength: number };
          owner: { minLength: number; maxLength: number };
          short_description: { minLength: number; maxLength: number };
          intent_tags: {
            maxItems: number;
            items: { minLength: number; maxLength: number; pattern: string };
          };
        };
      };
      AdminBindingMutationView: {
        required: string[];
        properties: {
          binding_id: { type: string };
          target_system: { enum: string[] };
          execution_identity: { enum: string[] };
          bind_status: { enum: string[] };
          binding_scope: { type: string; nullable: boolean; enum: null[] };
          account_set_id: { type: string; nullable: boolean; enum: null[] };
          device_domain_id: { type: string; nullable: boolean; enum: null[] };
          reason_code: { enum: string[] };
        };
      };
      AdminBindingMutationResponse: {
        required: string[];
        properties: {
          action: { enum: string[] };
          binding: { $ref: string };
          changed: { type: string };
          next_action: { enum: string[] };
        };
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

  it('keeps the three 005b read paths and adds only the two binding mutations', () => {
    const spec = loadSpec();
    const addedPaths = Object.keys(spec.paths).filter(
      (path) => !path.startsWith('/api/v1/admin/registry'),
    );

    expect(addedPaths).toEqual([
      '/api/v1/admin/tasks',
      '/api/v1/admin/tasks/{task_id}/events',
      '/api/v1/admin/bindings',
      '/api/v1/admin/bindings/{binding_id}/revoke',
      '/api/v1/admin/bindings/{binding_id}/reset',
    ]);
    expect(requiredPath(spec, '/api/v1/admin/tasks').get?.operationId).toBe('listTasks');
    expect(requiredPath(spec, '/api/v1/admin/tasks/{task_id}/events').get?.operationId).toBe(
      'listTaskEvents',
    );
    expect(requiredPath(spec, '/api/v1/admin/bindings').get?.operationId).toBe(
      'listBindings',
    );
    expect(
      requiredPath(spec, '/api/v1/admin/bindings/{binding_id}/revoke').post?.operationId,
    ).toBe('revokeBinding');
    expect(
      requiredPath(spec, '/api/v1/admin/bindings/{binding_id}/reset').post?.operationId,
    ).toBe('resetBinding');
  });

  it('declares the binding mutation parameter, success contract, and exact errors', () => {
    const spec = loadSpec();

    expect(spec.components.parameters.BindingId).toEqual({
      name: 'binding_id',
      in: 'path',
      required: true,
      schema: { type: 'string' },
    });

    for (const path of [
      '/api/v1/admin/bindings/{binding_id}/revoke',
      '/api/v1/admin/bindings/{binding_id}/reset',
    ]) {
      const operation = requiredPath(spec, path).post;
      expect(operation?.parameters).toEqual([
        { $ref: '#/components/parameters/BindingId' },
      ]);
      expect(operation?.responses['200']?.content?.['application/json'].schema.$ref).toBe(
        '#/components/schemas/AdminBindingMutationResponse',
      );
      expect(operation?.responses['403']?.$ref).toBe(
        '#/components/responses/RoleNotAllowed',
      );
      expect(operation?.responses['404']?.$ref).toBe(
        '#/components/responses/BindingNotFound',
      );
      expect(operation?.responses['503']?.$ref).toBe(
        '#/components/responses/BindingMutationUnavailable',
      );
    }

    for (const responseName of ['BindingNotFound', 'BindingMutationUnavailable']) {
      expect(
        requiredResponse(spec, responseName).content['application/json'].schema.$ref,
      ).toBe('#/components/schemas/AdminErrorResponse');
    }

    expect(spec.components.schemas.AdminBindingMutationView).toMatchObject({
      required: [
        'binding_id',
        'target_system',
        'execution_identity',
        'bind_status',
        'binding_scope',
        'account_set_id',
        'device_domain_id',
        'reason_code',
      ],
      properties: {
        binding_id: { type: 'string' },
        target_system: { enum: ['oa'] },
        execution_identity: { enum: ['user_delegated'] },
        bind_status: { enum: ['revoked'] },
        binding_scope: { type: 'string', nullable: true, enum: [null] },
        account_set_id: { type: 'string', nullable: true, enum: [null] },
        device_domain_id: { type: 'string', nullable: true, enum: [null] },
        reason_code: { enum: ['identity_revoked'] },
      },
    });
    expect(spec.components.schemas.AdminBindingMutationResponse).toEqual({
      type: 'object',
      additionalProperties: false,
      required: ['action', 'binding', 'changed', 'next_action'],
      properties: {
        action: { type: 'string', enum: ['revoke', 'reset'] },
        binding: { $ref: '#/components/schemas/AdminBindingMutationView' },
        changed: { type: 'boolean' },
        next_action: { type: 'string', enum: ['none', 'reauthenticate'] },
      },
    });
  });

  it('publishes the create text and intent-tag bounds', () => {
    const create = loadSpec().components.schemas.AdminCapabilityCreate.properties;

    expect(create.name).toEqual({ type: 'string', minLength: 1, maxLength: 120 });
    expect(create.owner).toEqual({ type: 'string', minLength: 1, maxLength: 120 });
    expect(create.short_description).toEqual({
      type: 'string',
      minLength: 1,
      maxLength: 500,
    });
    expect(create.intent_tags).toEqual({
      type: 'array',
      maxItems: 32,
      items: {
        type: 'string',
        minLength: 1,
        maxLength: 64,
        pattern: '^[a-z0-9]+(?:[._-][a-z0-9]+)*$',
      },
      default: [],
    });
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
