---
name: frontend-task
description: Implement or validate EternalAI frontend changes with the repository's scoped checks and isolated component tests.
---

# EternalAI frontend task

Follow the current Goal and repository AGENTS.md for scope, review tier, full-test triggers and authorization. This skill supplies frontend commands, not another approval gate.

## Choose checks by impact

- Select tests covering the changed behavior, its failure paths and nearest affected callers.
- From the repository root, run a selected test with `pnpm --dir web exec vitest --run src/path/to/file.test.tsx` (or `.test.ts`). Replace the example with an existing test.
- **Each selected `.test.tsx` file must run in its own process**, one command per file. Do not group multiple component files into one Vitest invocation. Full frontend coverage uses `pnpm --dir web test`, whose existing component runner preserves that isolation.
- For TypeScript/TSX changes, run `pnpm --dir web lint` and `pnpm --dir web typecheck`. Run `pnpm --dir web build` when bundling, entrypoints, imports, styling/assets or build configuration are affected. Pure prose changes do not trigger this package's gates.
- API schema, generated clients or their shared transport changes also need `pnpm --dir web test:openapi`. `pnpm --dir web generate:api` rewrites generated files: use it only when the approved change requires regeneration, then inspect that diff.
- Shared theme/app-shell behavior requires its affected component set and visual inspection of the changed states. Preserve the approved design system and reproduce a performance problem before claiming improvement.
- Full backend, architecture, dependency and Golden checks follow AGENTS.md triggers; a frontend edit alone does not trigger them.

Fix failures caused by this change within scope and rerun the affected checks. Report unrelated/environment blockers with their evidence; full-suite failure follows the repository stop rule. Never weaken assertions, increase retries/timeouts or regenerate expected output to conceal a failure.

Report actual commands, results and reasons for omitted checks. A successful build is not evidence of a completed user interaction test.
