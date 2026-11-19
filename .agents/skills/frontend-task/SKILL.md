---
name: frontend-task
description: Implement or validate EternalAI frontend changes with the repository's scoped checks and isolated component tests.
---

# EternalAI frontend task

Use the current Goal and repository AGENTS.md for scope, authorization and validation. Verify altered UI behavior through the actual entry point and preserve the approved design system and required CI.

- Choose checks by impact: `pnpm --dir web lint` for relevant lint, `pnpm --dir web typecheck` for affected TypeScript contracts, and `pnpm --dir web build` for affected bundles, entrypoints or build assets/configuration. Use visual inspection when a changed state or interaction needs it.
- Select necessary existing tests. From repository root use `pnpm --dir web exec vitest --run src/path/to/file.test.tsx` (or `.test.ts`); replace the example with an existing test. **Run each selected .test.tsx file in its own process**, one command per file. `pnpm --dir web test` is the full frontend entry point and preserves that isolation; local full runs require explicit request. Add tests only when current acceptance explicitly requires them.
- When an API schema, generated client or shared transport contract is affected, use `pnpm --dir web test:openapi` as needed. `pnpm --dir web generate:api` rewrites generated files; use it only for an approved output change and inspect its diff.
- Backend, architecture, dependencies and Golden verify only the applicable contracts under AGENTS.md. Record actual commands, results, and environmental gaps. Preserve original assertions and report runtime and static evidence at their actual scope.
