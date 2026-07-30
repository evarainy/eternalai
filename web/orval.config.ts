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
  auth: {
    input: './openapi/auth.openapi.json',
    output: {
      mode: 'split',
      target: './src/generated/auth/auth.ts',
      mock: false,
      override: {
        mutator: {
          path: './src/api/mutator.ts',
          name: 'customInstance',
        },
      },
    },
  },
  runtime: {
    input: './openapi/runtime.openapi.json',
    output: {
      mode: 'split',
      target: './src/generated/runtime/runtime.ts',
      mock: false,
      override: {
        mutator: {
          path: './src/api/mutator.ts',
          name: 'customInstance',
        },
      },
    },
  },
  adminTrace: {
    input: './openapi/admin-trace.openapi.json',
    output: {
      mode: 'split',
      target: './src/generated/admin-trace/admin-trace.ts',
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
