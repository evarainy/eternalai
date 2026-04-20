# Business Repo Phase 0 Review Checklist

## A. Scope and evidence
- [ ] The plan is based on actual business repository evidence, not just the planning pack itself.
- [ ] `planning/documentation.md` cites concrete evidence sources for major conclusions.
- [ ] The repo's real frameworks, toolchains, and entrypoints are identified.
- [ ] Known unknowns are explicitly listed rather than guessed.

## B. Spec-first completeness
- [ ] `spec/product_spec.md` contains complete required sections.
- [ ] `spec/product_spec.yaml` exists and is machine-readable.
- [ ] `spec/module_spec.yaml` enumerates real modules/subsystems.
- [ ] `spec/resource_spec.yaml` captures real shared resources.
- [ ] `spec/invariant_spec.yaml` defines critical invariants that must survive implementation.
- [ ] `contracts/*.yaml` exist where cross-boundary interfaces are present or implied.

## C. Task graph quality
- [ ] `planning/dev_plan.yaml` tasks are fine-grained, not broad epics.
- [ ] Every task has a valid `layer`.
- [ ] Dependencies reference real task IDs.
- [ ] There is no obvious DAG cycle risk.
- [ ] Overlap in `touched_paths` is intentional and justified.
- [ ] Shared resource collisions are identified.
- [ ] Human gates are declared where needed.

## D. Acceptance rigor
- [ ] `planning/acceptance_spec.yaml` uses concrete, automatable assertions.
- [ ] Acceptance is not expressed as vague prose.
- [ ] Every acceptance item has non-empty evidence paths.
- [ ] Critical success and failure paths both have acceptance coverage.
- [ ] Invariants are referenced where relevant.

## E. Execution contract quality
- [ ] `planning/implement.md` defines a thick per-task result contract.
- [ ] Result fields include pass/fail by acceptance ID.
- [ ] Result fields include blocker and human-escalation signals.
- [ ] Merge gates and validation expectations are explicit.

## F. Readiness honesty
- [ ] `planning/phase0_result.json` separates `planning_ready` from `execution_ready`.
- [ ] `execution_ready` is false if critical blockers remain.
- [ ] `next_step` is concrete and executable.
- [ ] Risks and blockers are consistent with the stated readiness.
