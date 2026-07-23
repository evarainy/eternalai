import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

interface AdminOpenApiDocument {
  paths: {
    '/api/v1/admin/registry': {
      post: {
        responses: Record<string, { content?: { 'application/json': { schema: { $ref: string } } } }>;
      };
    };
  };
  components: {
    schemas: {
      FastApiValidationErrorResponse: {
        properties: { detail: { type: string } };
      };
    };
  };
}

describe('Admin Registry curated OpenAPI', () => {
  it('declares create validation failures with the FastAPI array envelope', () => {
    const spec = JSON.parse(
      readFileSync(resolve(process.cwd(), 'openapi/admin.openapi.json'), 'utf8'),
    ) as AdminOpenApiDocument;
    const validationResponse = spec.paths['/api/v1/admin/registry'].post.responses['422'];

    expect(validationResponse?.content?.['application/json'].schema.$ref).toBe(
      '#/components/schemas/FastApiValidationErrorResponse',
    );
    expect(
      spec.components.schemas.FastApiValidationErrorResponse.properties.detail.type,
    ).toBe('array');
  });
});
