# Implementation Guidance

## Execution Order

Follow the dependency order in `planning/dev_plan.yaml`. The intended backbone is:
1. `DV-001` to stabilize runtime configuration.
2. `DV-002` and `DV-003` to freeze route structure and schemas.
3. `DV-004` to add durable persistence.
4. `DV-005` to `DV-010` to build the backend workflow in thin vertical slices.
5. `DV-011` and `DV-012` only after the public API contract is stable enough for clients.
6. `DV-013` after real feature paths exist and can be verified.

## Working Rules

- Treat `spec/` and `contracts/` as the approval boundary; implementation follows those files.
- Respect `touched_paths` and `forbidden_paths` for each task to reduce merge conflict and scope creep.
- Keep bootstrap smoke tests green while adding business code.
- Favor thin vertical slices over large framework-first rewrites.
- Add tests with each task, not as a cleanup pass.

## Review Gates

Pause before implementation if any of these remain unresolved:
- auth mode for non-bootstrap routes
- first supported knowledge-source formats
- whether raw audio bytes are retained
- which frontend stack is chosen for `apps/web` and `apps/miniapp`
- whether ASR runtime assumptions require GPU, model downloads, or large binary dependencies

## Definition Of Done Per Task

A task is done only when:
- its acceptance IDs in `planning/acceptance_spec.yaml` pass or have runnable tests added for them
- touched paths match the task boundary
- contract YAML is updated where applicable
- docs or runbooks are updated if operator behavior changed
- no unrelated business implementation leaks into sibling modules
