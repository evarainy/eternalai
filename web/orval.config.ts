import { defineConfig } from 'orval';

export default defineConfig({
  api: {
    input: './openapi/health.openapi.json',
    output: {
      mode: 'split',
      target: './src/generated/api.ts',
      mock: false,
      override: {
        mutator: {
          path: './src/api/mutator.ts',
          name: 'customInstance',
        },
      },
    },
  },
  admin: {
    input: './openapi/admin.openapi.json',
    output: {
      mode: 'split',
      target: './src/generated/admin/admin.ts',
      mock: false,
      override: {
        mutator: {
          path: './src/api/mutator.ts',
          name: 'customInstance',
        },
      },
    },
  },
});
